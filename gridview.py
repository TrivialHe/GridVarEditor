"""Read-only geographic snapshots of curvilinear cell-centred data.

Display vertices are interpolated from supplied coordinates, not POP flux
faces. No interpolation or regridding is applied to variable values.
"""
import numpy as np
import matplotlib as mpl
from matplotlib.figure import Figure
from matplotlib.collections import PolyCollection
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg
from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                            QDoubleSpinBox, QSpinBox, QPushButton, QLabel,
                            QFileDialog, QMessageBox)


def _corners(values):
    padded = np.pad(values, 1, mode='edge')
    padded[0, 1:-1] = 2 * values[0] - values[1]
    padded[-1, 1:-1] = 2 * values[-1] - values[-2]
    padded[:, 0] = 2 * padded[:, 1] - padded[:, 2]
    padded[:, -1] = 2 * padded[:, -2] - padded[:, -3]
    return (padded[:-1, :-1] + padded[1:, :-1] +
            padded[:-1, 1:] + padded[1:, 1:]) / 4


def cell_polygons(lons, lats):
    """Approximate display cells, with seam copies instead of world-spanning edges."""
    lons, lats = np.asarray(lons, float), np.asarray(lats, float)
    if lons.shape != lats.shape or lons.ndim != 2 or min(lons.shape) < 2:
        raise ValueError('地图预览需要至少 2×2、形状相同的经纬度坐标。')
    if not np.all(np.isfinite(lons)) or not np.all(np.isfinite(lats)):
        raise ValueError('经纬度包含无效值，无法构建地图预览。')
    continuous = np.rad2deg(np.unwrap(np.unwrap(np.deg2rad(lons), axis=1), axis=0))
    x, y = _corners(continuous), np.clip(_corners(lats), -90, 90)
    polygons, indices = [], []
    for i, j in np.ndindex(lons.shape):
        px = np.array([x[i,j], x[i,j+1], x[i+1,j+1], x[i+1,j]])
        py = np.array([y[i,j], y[i,j+1], y[i+1,j+1], y[i+1,j]])
        centre = (lons[i,j] + 180) % 360 - 180
        px = centre + (px - lons[i,j] + 180) % 360 - 180
        # Fold/pole cells cannot be represented by a single unambiguous planar quad.
        if np.ptp(px) >= 180:
            continue
        for shift in (-360, 0, 360):
            shifted = px + shift
            if shifted.max() >= -180 and shifted.min() <= 180:
                polygons.append(np.column_stack([shifted, py]))
                indices.append(i * lons.shape[1] + j)
    return polygons, np.asarray(indices, dtype=int)


def make_map_figure(lons, lats, values, title, extent=(-180,180,-90,90),
                    size=(12,6), dpi=150, cmap='viridis', mask_land=False,
                    units='', geometry=None):
    west, east, south, north = extent
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError('范围须满足 -180 ≤ 西 < 东 ≤ 180，-90 ≤ 南 < 北 ≤ 90。')
    if not (2 <= size[0] <= 30 and 2 <= size[1] <= 30 and 72 <= dpi <= 600):
        raise ValueError('图片尺寸须为 2–30 英寸，分辨率须为 72–600 dpi。')
    if size[0] * size[1] * dpi * dpi > 50_000_000:
        raise ValueError('图片超过 5000 万像素，请减小尺寸或分辨率。')
    polygons, indices = geometry if geometry is not None else cell_polygons(lons, lats)
    data = np.ma.masked_invalid(np.ma.array(values, copy=True))
    if data.shape != np.shape(lons):
        raise ValueError('变量与经纬度形状不一致。')
    if mask_land:
        data = np.ma.masked_where(data <= 0, data)
    palette = mpl.colormaps[cmap].copy() if isinstance(cmap, str) else cmap.copy()
    palette.set_bad('#777777')
    fig = Figure(figsize=size, dpi=dpi, layout='constrained')
    ax = fig.add_subplot(111)
    collection = PolyCollection(polygons, cmap=palette, edgecolors='none', linewidths=0,
                                antialiaseds=False)
    collection.set_array(data.ravel()[indices])
    collection.set_clim(float(data.min()) if data.count() else 0,
                        float(data.max()) if data.count() else 1)
    ax.add_collection(collection)
    ax.set(xlim=(west,east), ylim=(south,north), xlabel='Longitude (degrees east)',
           ylabel='Latitude (degrees north)', title=title)
    ax.set_aspect('equal', adjustable='box')
    ax.grid(alpha=0.25)
    fig.colorbar(collection, ax=ax, pad=0.02, shrink=0.85, label=units or 'Value')
    return fig


class MapPreview(QDialog):
    def __init__(self, lons, lats, values, varname, units, cmap, mask_land, filename, parent=None):
        super().__init__(parent)
        self.setWindowTitle('经纬度地图预览与导出 — ' + varname)
        self.resize(1120, 760)
        self.lons, self.lats = np.array(lons, copy=True), np.array(lats, copy=True)
        self.values = np.ma.array(values, copy=True)
        self.geometry = cell_polygons(self.lons, self.lats)
        self.varname, self.units, self.cmap, self.mask_land = varname, units, cmap, mask_land
        self.title = filename + '\n' + varname
        layout = QVBoxLayout(self)
        note = QLabel('只读快照：包含打开预览时的未保存编辑；再次打开可刷新。'
                      '\n等距经纬度展示；格点边缘由坐标近似推算，极点折叠单元可能省略。'
                      'KMT 显示有效层数；灰色为陆地或无效值。')
        note.setWordWrap(True)
        layout.addWidget(note)
        row = QHBoxLayout()
        self.bounds = []
        for label, value, lo, hi in [('西',-180,-180,180),('东',180,-180,180),
                                      ('南',-90,-90,90),('北',90,-90,90)]:
            box = QDoubleSpinBox()
            box.setRange(lo,hi)
            box.setValue(value)
            row.addWidget(QLabel(label))
            row.addWidget(box)
            self.bounds.append(box)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.width_box, self.height_box = QDoubleSpinBox(), QDoubleSpinBox()
        for label, box, value in [('宽（英寸）',self.width_box,12),('高（英寸）',self.height_box,6)]:
            box.setRange(2,30)
            box.setValue(value)
            row.addWidget(QLabel(label))
            row.addWidget(box)
        self.dpi_box = QSpinBox()
        self.dpi_box.setRange(72,600)
        self.dpi_box.setValue(300)
        row.addWidget(QLabel('dpi'))
        row.addWidget(self.dpi_box)
        layout.addLayout(row)
        self.figure = self.build_figure(preview=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas, 1)
        buttons = QHBoxLayout()
        refresh = QPushButton('更新预览范围')
        refresh.clicked.connect(self.refresh)
        export = QPushButton('导出 PNG / SVG / PDF')
        export.clicked.connect(self.export)
        buttons.addWidget(refresh)
        buttons.addWidget(export)
        layout.addLayout(buttons)

    def build_figure(self, preview=False):
        return make_map_figure(self.lons, self.lats, self.values, self.title,
                               tuple(b.value() for b in self.bounds),
                               (self.width_box.value(),self.height_box.value()),
                               100 if preview else self.dpi_box.value(), self.cmap,
                               self.mask_land, self.units or ('Wet levels' if self.mask_land else 'Value'),
                               self.geometry)

    def refresh(self):
        try:
            figure = self.build_figure(preview=True)
        except ValueError as error:
            QMessageBox.warning(self, '无法预览', str(error))
            return
        self.figure = figure
        self.canvas.figure = figure
        figure.set_canvas(self.canvas)
        self.canvas.draw_idle()

    def export(self):
        path, selected = QFileDialog.getSaveFileName(self, '导出地图', self.varname + '.png',
                                                    'PNG (*.png);;SVG (*.svg);;PDF (*.pdf)')
        if not path:
            return
        extension = selected.split('*.')[-1].rstrip(')')
        if not path.lower().endswith(('.png','.svg','.pdf')):
            path += '.' + extension
        try:
            figure = self.build_figure()
            figure.savefig(path, dpi=self.dpi_box.value())
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, '导出失败', str(error))
            return
        QMessageBox.information(self, '导出完成', path)
