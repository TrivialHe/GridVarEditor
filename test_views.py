import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import unittest
from pathlib import Path
import numpy as np
from netCDF4 import Dataset
from PyQt5.QtWidgets import QApplication
from GridVarEditor import GridVarEditor


class ViewsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / 'grid.nc'
        with Dataset(self.file, 'w') as ds:
            ds.createDimension('y', 5)
            ds.createDimension('x', 6)
            for name, values in [('TLAT', np.broadcast_to(np.linspace(-20,20,5)[:,None],(5,6))),
                                 ('TLONG', np.broadcast_to(np.linspace(150,200,6),(5,6))),
                                 ('KMT', np.arange(30).reshape(5,6))]:
                ds.createVariable(name, 'f8' if name != 'KMT' else 'i4', ('y','x'))[:] = values
        self.win = GridVarEditor([str(self.file)], 'KMT')
        self.win.show()
        self.app.processEvents()

    def tearDown(self):
        self.win.close()
        self.tmp.cleanup()

    def test_modes_preserve_data_and_zoom(self):
        before = self.win.dc.data.copy()
        self.assertEqual(self.win.axes.get_aspect(), 'auto')
        self.win.axes.set_xlim(1,4)
        for mode, expected in [(0,'auto'),(1,1.0),(2,self.win.dc.aspect)]:
            self.win.aspect_combo.setCurrentIndex(mode)
            self.assertEqual(self.win.axes.get_aspect(), expected)
            self.assertEqual(self.win.axes.get_xlim(), (1,4))
        np.testing.assert_array_equal(self.win.dc.data, before)
        self.assertEqual(self.win.dc.editCount(), 0)

    def test_edit_undo_redo_survives_view_switch(self):
        original = self.win.dc.data[2,2]
        self.win.dc.setValue(2,2,53)
        self.win.aspect_combo.setCurrentIndex(2)
        self.win.do_undo()
        self.assertEqual(self.win.dc.data[2,2], original)
        self.win.do_redo()
        self.assertEqual(self.win.dc.data[2,2], 53)
        self.win.do_undo()

    def test_sidebar_toggle_recovers_canvas_space(self):
        width = self.win.canvas.width()
        self.win.sidebar_action.setChecked(False)
        self.app.processEvents()
        self.assertFalse(self.win.sidebar_scroll.isVisible())
        self.assertGreater(self.win.canvas.width(), width)
        self.win.sidebar_action.setChecked(True)
        self.app.processEvents()
        self.assertTrue(self.win.sidebar_scroll.isVisible())

    def test_preview_uses_unsaved_snapshot_and_exports(self):
        self.win.dc.data[1,1] = 53
        self.win.open_map_preview()
        preview = self.win.map_preview
        self.assertEqual(preview.values[1,1], 53)
        self.win.dc.data[1,1] = 17
        self.assertEqual(preview.values[1,1], 53)
        for ext in ['png','svg','pdf']:
            path = Path(self.tmp.name) / ('map.' + ext)
            preview.figure.savefig(path)
            self.assertGreater(path.stat().st_size, 1000)
        preview.close()
        with Dataset(self.file) as ds:
            self.assertEqual(ds['KMT'][1,1], 7)

    def test_seam_and_invalid_extent(self):
        import gridview
        polygons, indices = gridview.cell_polygons(self.win.dc.lons, self.win.dc.lats)
        self.assertTrue(all(np.ptp(p[:,0]) < 30 for p in polygons))
        self.assertEqual(len(indices), len(polygons))
        with self.assertRaises(ValueError):
            gridview.make_map_figure(self.win.dc.lons, self.win.dc.lats,
                                    self.win.dc.data, 'KMT', (20,10,-90,90))

    def test_export_size_and_invalid_coordinates(self):
        import gridview
        figure = gridview.make_map_figure(self.win.dc.lons,self.win.dc.lats,
                                          self.win.dc.data,'KMT',size=(8,4),dpi=200)
        np.testing.assert_allclose(figure.get_size_inches(), [8,4])
        self.assertEqual(figure.dpi, 200)
        bad = self.win.dc.lons.copy()
        bad[0,0] = np.nan
        with self.assertRaises(ValueError):
            gridview.cell_polygons(bad, self.win.dc.lats)
        with self.assertRaises(ValueError):
            gridview.make_map_figure(self.win.dc.lons,self.win.dc.lats,
                                     self.win.dc.data,'KMT',size=(30,30),dpi=600)


if __name__ == '__main__':
    unittest.main()
