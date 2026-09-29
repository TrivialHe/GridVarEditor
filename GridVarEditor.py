#!/usr/bin/env python
# =============================================================================
# 代码名称：POP 海洋网格二维变量可视化编辑器 GridVarEditor
# 代码作者：何悠
# 联系方式：tr1vial@foxmail.com
# 当前版本：v1.0.0
# 版本日期：2026-08-14
# 主要功能：可视化编辑 POP 海洋模式曲线网格上的二维字段（KMT 有效层数、
#           水深/地形 elevation、区域掩码等），支持整图缩放平移、点选/
#           画笔编辑、撤销重做，以及同时打开多文件并切换而不丢失修改。
# 使用方法：参见 README.md
# =============================================================================
"""
GridVarEditor.py

A visual editor for 2D variables (KMT level index, water depth/elevation,
region mask, ...) stored on a POP ocean model curvilinear grid. Shows the
whole grid at once and lets you pan/zoom it like a normal raster editor or
map viewer, with click-to-select / paint-mode editing, full undo/redo, and
support for having multiple files open and switching between them without
losing in-progress edits.

Usage: see README.md in this directory for setup and full usage
instructions (quick start: `uv sync` once, then
`uv run GridVarEditor.py <file.nc> [<file2.nc> ...]`).
"""

import argparse
import glob
import os
import sys

import numpy as np
import matplotlib as mpl
mpl.use("Qt5Agg")
from matplotlib import pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.patches import Rectangle
import cartopy.crs as ccrs
from netCDF4 import Dataset

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QLineEdit, QComboBox,
    QPushButton, QCheckBox, QFormLayout, QVBoxLayout, QHBoxLayout,
    QGroupBox, QSplitter, QMessageBox, QFileDialog, QAction, QShortcut,
    QListWidget, QListWidgetItem, QScrollArea,
)
from PyQt5.QtGui import QKeySequence

import gridio
from gridview import MapPreview

DEFAULT_MASKABLE_CMAP = "tab20"
DEFAULT_CONTINUOUS_CMAP = "viridis"
LAND_COLOR = "#3a3a3a"


class MapToolbar(NavigationToolbar):
    """Home/Save only -- see the comment where this is instantiated."""
    toolitems = [t for t in NavigationToolbar.toolitems if t[0] in ("Home", "Save")]


def is_kmt_like(varname):
    """Heuristic: does this variable look like a 'deepest level index' field?"""
    name = varname.lower()
    return "kmt" in name or "kmu" in name


class Edit(object):
    __slots__ = ("i", "j", "old", "new")

    def __init__(self, i, j, old, new):
        self.i, self.j, self.old, self.new = i, j, old, new


class DataContainer(object):
    """Owns the full 2D array for one variable, plus its undo/redo history."""

    def __init__(self, fname, varname, levels_file=None):
        self.fname = fname
        self.varname = varname
        self.__read_nc_file(levels_file)
        self.orig_data = np.copy(self.data)

        self.undo_stack = []   # list of list[Edit] (one list per stroke/edit)
        self.redo_stack = []
        self._group = None     # currently open stroke, or None

        # Save target for *this* (file, variable) -- remembered per container
        # so that switching between open files/variables and coming back
        # doesn't re-ask "overwrite?" for a target already agreed on.
        self.ofile = None
        self.asked_about_overwrite_permission = False

    def __read_nc_file(self, levels_file):
        ncfile = Dataset(self.fname, "r")
        try:
            _, _, lat2d, lon2d = gridio.discover_coordinates(ncfile)
            variables = ncfile.variables

            if self.varname not in variables:
                raise KeyError("Variable '{0}' not found in {1}".format(self.varname, self.fname))

            datavar = variables[self.varname]
            if datavar.ndim != 2 or datavar.shape != lat2d.shape:
                raise ValueError("{0} does not share the grid's 2D shape {1}".format(
                    self.varname, lat2d.shape))

            self.dtype = datavar.dtype
            self.is_integer = np.issubdtype(self.dtype, np.integer)
            self.dimensions = datavar.dimensions
            self.long_name = getattr(datavar, "long_name", getattr(datavar, "description", ""))
            self.units = getattr(datavar, "units", "")

            self.data = np.asarray(datavar[:, :], dtype=(np.int32 if self.is_integer else np.float64))
            self.lats = lat2d
            self.lons = lon2d
            self.ny, self.nx = self.data.shape

            cell_widths = cell_heights = None
            wname = gridio.find_variable(variables, gridio.WIDTH_CANDIDATES)
            hname = gridio.find_variable(variables, gridio.HEIGHT_CANDIDATES)
            if wname is not None and hname is not None:
                w = variables[wname][:, :]
                h = variables[hname][:, :]
                if w.shape == self.data.shape and h.shape == self.data.shape:
                    cell_widths, cell_heights = w, h
            self.aspect = gridio.compute_physical_aspect(self.lats, self.lons, cell_widths, cell_heights)

            self.is_maskable = is_kmt_like(self.varname)

            self.level_depths = None
            if self.is_integer and self.is_maskable:
                try:
                    self.level_depths = gridio.load_level_bottom_depths(levels_file or self.fname)
                except (OSError, IOError):
                    self.level_depths = None
        finally:
            ncfile.close()

    # ---------------------------------------------------------- edit history
    @property
    def dirty(self):
        return len(self.undo_stack) > 0

    def beginGroup(self):
        self._group = []

    def endGroup(self):
        if self._group:
            self.undo_stack.append(self._group)
            self.redo_stack = []
        self._group = None

    def setValue(self, i, j, newval):
        """Set data[i,j] = newval. Returns True if the value actually changed."""
        old = self.data[i, j]
        if old == newval:
            return False
        self.data[i, j] = newval
        edit = Edit(i, j, old, newval)
        if self._group is not None:
            self._group.append(edit)
        else:
            self.undo_stack.append([edit])
            self.redo_stack = []
        return True

    def canUndo(self):
        return len(self.undo_stack) > 0

    def canRedo(self):
        return len(self.redo_stack) > 0

    def undo(self):
        if not self.undo_stack:
            return
        group = self.undo_stack.pop()
        for edit in reversed(group):
            self.data[edit.i, edit.j] = edit.old
        self.redo_stack.append(group)

    def redo(self):
        if not self.redo_stack:
            return
        group = self.redo_stack.pop()
        for edit in group:
            self.data[edit.i, edit.j] = edit.new
        self.undo_stack.append(group)

    def editCount(self):
        return sum(len(g) for g in self.undo_stack)

    # -------------------------------------------------------------- queries
    def valueAt(self, i, j):
        return self.data[i, j]

    def depthAt(self, i, j):
        if self.level_depths is None:
            return None
        level = int(self.data[i, j])
        if level <= 0:
            return 0.0
        level = min(level, len(self.level_depths))
        return float(self.level_depths[level - 1])

    def displayArray(self):
        if self.is_maskable:
            return np.ma.masked_equal(self.data, 0)
        return self.data

    def stats(self):
        arr = self.displayArray()
        if np.ma.is_masked(arr) or isinstance(arr, np.ma.MaskedArray):
            if arr.count() == 0:
                return (0, 0, 0)
            return (float(arr.min()), float(arr.max()), float(arr.mean()))
        return (float(arr.min()), float(arr.max()), float(arr.mean()))

    def changedCells(self):
        """(rows, cols, new_values) for every cell that differs from orig_data."""
        diff = self.data != self.orig_data
        rows, cols = np.nonzero(diff)
        return rows, cols, self.data[rows, cols]


class GridVarEditor(QMainWindow):

    def __init__(self, fnames=(), varname=None, levels_file=None):
        super(GridVarEditor, self).__init__(None)
        self.levels_file = levels_file

        self.open_files = []             # ordered list of file paths
        self.file_vars = {}              # fname -> list[GridVariable] (cached)
        self.containers = {}             # (fname, varname) -> DataContainer (cached, keeps undo history)
        self.last_var_for_file = {}      # fname -> last-viewed varname, for switching back
        self.current_fname = None

        self.painting = False
        self.eyedrop_pending = False
        self.rect_patch = None
        self.hover_marker = None
        self.select_marker = None
        self.selected_ij = None
        self._pan_start_pixel = None
        self._pan_start_lims = None
        self._pan_inv = None
        self._painting_stroke = False

        self.create_ui()

        fnames = [os.path.abspath(f) for f in fnames]
        if fnames:
            self.add_files(fnames, initial_var=varname)
        else:
            self.statusBar().showMessage('使用 文件 > 打开文件/打开目录 来加载 NetCDF 网格文件', 5000)

        self.setWindowTitle('GridVarEditor')
        self.resize(1300, 850)

    def _pick_default_variable(self, available_vars):
        for v in available_vars:
            if is_kmt_like(v.name):
                return v.name
        return available_vars[0].name

    # -------------------------------------------------------------- files
    def discover_vars_for_file(self, fname):
        if fname not in self.file_vars:
            ncfile = Dataset(fname, "r")
            try:
                _, _, lat2d, _ = gridio.discover_coordinates(ncfile)
                self.file_vars[fname] = gridio.discover_editable_variables(ncfile, lat2d.shape)
            finally:
                ncfile.close()
        return self.file_vars[fname]

    def add_files(self, paths, initial_var=None):
        """Add NetCDF files to the open-files list and switch to the first new one."""
        first_new = None
        skipped = []
        for path in paths:
            path = os.path.abspath(path)
            if path in self.open_files:
                continue
            try:
                available = self.discover_vars_for_file(path)
            except Exception as exc:
                skipped.append("{0}: {1}".format(os.path.basename(path), exc))
                continue
            if not available:
                skipped.append("{0}: 没有找到与网格形状匹配的 2D 变量".format(os.path.basename(path)))
                continue
            self.open_files.append(path)
            if first_new is None:
                first_new = path

        self.refresh_file_list()
        if skipped:
            QMessageBox.warning(self, "部分文件未能打开", "\n".join(skipped))
        if first_new is not None:
            self.select_file_in_list(first_new)
            self.switch_file(first_new, initial_var)

    def refresh_file_list(self):
        self.file_list.blockSignals(True)
        self.file_list.clear()
        for path in self.open_files:
            item = QListWidgetItem(os.path.basename(path))
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
            self.file_list.addItem(item)
        self.file_list.blockSignals(False)
        self.select_file_in_list(self.current_fname)

    def select_file_in_list(self, fname):
        self.file_list.blockSignals(True)
        for row in range(self.file_list.count()):
            item = self.file_list.item(row)
            if item.data(Qt.UserRole) == fname:
                self.file_list.setCurrentItem(item)
                break
        self.file_list.blockSignals(False)

    def open_files_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "打开文件", "", "NetCDF (*.nc);;All files (*)")
        if paths:
            self.add_files(paths)

    def open_directory_dialog(self):
        directory = QFileDialog.getExistingDirectory(self, "打开目录")
        if not directory:
            return
        paths = sorted(glob.glob(os.path.join(directory, "*.nc")))
        if not paths:
            QMessageBox.information(self, "打开目录", "该目录下没有找到 .nc 文件")
            return
        self.add_files(paths)

    def close_current_file(self):
        if self.current_fname is None:
            return
        fname = self.current_fname
        dirty_vars = [v.name for v in self.file_vars.get(fname, [])
                      if self.containers.get((fname, v.name)) is not None
                      and self.containers[(fname, v.name)].dirty]
        if dirty_vars:
            reply = QMessageBox.question(
                self, "未保存的修改",
                "{0} 中的变量 {1} 有未保存的修改，关闭文件将丢弃它们，确定继续吗？".format(
                    os.path.basename(fname), ", ".join(dirty_vars)),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                return

        self.open_files.remove(fname)
        for key in [k for k in self.containers if k[0] == fname]:
            del self.containers[key]
        self.file_vars.pop(fname, None)
        self.last_var_for_file.pop(fname, None)

        if self.open_files:
            self.refresh_file_list()
            self.switch_file(self.open_files[0])
        else:
            self.current_fname = None
            self.file_list.clear()
            self.var_combo.clear()
            self.fig.clf()
            self.canvas.draw_idle()
            self.setWindowTitle('GridVarEditor')
            self.statusBar().showMessage('所有文件已关闭', 3000)

    def on_file_selected(self, current, previous):
        if current is None:
            return
        fname = current.data(Qt.UserRole)
        if fname != self.current_fname:
            self.switch_file(fname)

    def switch_file(self, fname, varname=None):
        self.current_fname = fname
        available = self.discover_vars_for_file(fname)

        self.var_combo.blockSignals(True)
        self.var_combo.clear()
        self.var_combo.addItems([v.name for v in available])
        self.var_combo.blockSignals(False)

        varname = varname or self.last_var_for_file.get(fname) or self._pick_default_variable(available)
        self.load_variable(varname)

    # -------------------------------------------------------------------- UI
    def create_ui(self):
        splitter = QSplitter(Qt.Horizontal)

        # --- main canvas -----------------------------------------------
        canvas_widget = QWidget()
        canvas_layout = QVBoxLayout(canvas_widget)
        canvas_layout.setContentsMargins(0, 0, 0, 0)

        self.fig = plt.Figure(facecolor='w')
        self.canvas = FigureCanvas(self.fig)
        self.axes = self.fig.add_subplot(111)
        # Drop the Pan/Zoom toggle buttons: while active they swallow left
        # clicks for box-zoom/drag-pan instead of passing them to our own
        # click-to-select/paint handlers, which is a confusing trap. Panning
        # is handled directly by middle-mouse-drag in on_press/on_motion.
        self.toolbar = MapToolbar(self.canvas, canvas_widget)

        self.aspect_combo = QComboBox()
        self.aspect_combo.addItems(["铺满窗口（比例可变）", "方格网格", "近似物理比例"])
        self.aspect_combo.setToolTip("编辑视图使用行列索引；经纬度地图请用地图预览。显示比例不改变网格数据。")
        self.aspect_combo.currentIndexChanged.connect(self.apply_display_aspect)
        self.toolbar.addWidget(self.aspect_combo)
        preview_action = self.toolbar.addAction("地图预览 / 导出")
        preview_action.triggered.connect(self.open_map_preview)

        canvas_layout.addWidget(self.toolbar)
        canvas_layout.addWidget(self.canvas)
        splitter.addWidget(canvas_widget)

        self.canvas.mpl_connect('button_press_event', self.on_press)
        self.canvas.mpl_connect('button_release_event', self.on_release)
        self.canvas.mpl_connect('motion_notify_event', self.on_motion)
        self.canvas.mpl_connect('scroll_event', self.on_scroll)
        self.canvas.mpl_connect('key_press_event', self.on_key_press)
        # Needs keyboard focus (gained by clicking on it) to receive arrow
        # keys; while a sidebar QLineEdit is focused, arrow keys move its
        # text cursor as usual instead of the map selection.
        self.canvas.setFocusPolicy(Qt.StrongFocus)

        # --- sidebar ------------------------------------------------------
        sidebar = QWidget()
        sidebar.setMinimumWidth(300)
        side = QVBoxLayout(sidebar)

        # Open files
        files_box = QGroupBox("文件 Files")
        files_layout = QVBoxLayout(files_box)
        file_btn_row = QHBoxLayout()
        open_files_btn = QPushButton("打开文件...")
        open_files_btn.clicked.connect(self.open_files_dialog)
        open_dir_btn = QPushButton("打开目录...")
        open_dir_btn.clicked.connect(self.open_directory_dialog)
        file_btn_row.addWidget(open_files_btn)
        file_btn_row.addWidget(open_dir_btn)
        files_layout.addLayout(file_btn_row)

        self.file_list = QListWidget()
        self.file_list.setMaximumHeight(120)
        self.file_list.currentItemChanged.connect(self.on_file_selected)
        files_layout.addWidget(self.file_list)

        close_file_btn = QPushButton("关闭当前文件")
        close_file_btn.clicked.connect(self.close_current_file)
        files_layout.addWidget(close_file_btn)
        side.addWidget(files_box)

        # Variable picker
        var_box = QGroupBox("变量 Variable")
        var_form = QFormLayout(var_box)
        self.var_combo = QComboBox()
        self.var_combo.currentTextChanged.connect(self.on_variable_changed)
        var_form.addRow(self.var_combo)
        self.var_meta_label = QLabel("")
        self.var_meta_label.setWordWrap(True)
        var_form.addRow(self.var_meta_label)
        side.addWidget(var_box)

        # Minimap
        map_box = QGroupBox("总览 Overview (点击可跳转)")
        map_layout = QVBoxLayout(map_box)
        self.mini_fig = plt.Figure(figsize=(3, 2.2), facecolor='w')
        self.mini_canvas = FigureCanvas(self.mini_fig)
        self.mini_axes = self.mini_fig.add_subplot(111, projection=ccrs.PlateCarree())
        self.mini_fig.subplots_adjust(top=0.98, bottom=0.02, left=0.02, right=0.98)
        map_layout.addWidget(self.mini_canvas)
        side.addWidget(map_box)
        self.mini_canvas.mpl_connect('button_press_event', self.on_minimap_click)

        # Cursor / pixel info
        info_box = QGroupBox("光标信息 Cursor")
        info_form = QFormLayout(info_box)
        self.info_ij = QLabel("-")
        self.info_latlon = QLabel("-")
        self.info_value = QLabel("-")
        self.info_depth = QLabel("-")
        info_form.addRow("I, J:", self.info_ij)
        info_form.addRow("Lat, Lon:", self.info_latlon)
        info_form.addRow("当前值 Value:", self.info_value)
        self.info_depth_row_label = QLabel("水深 Depth (m):")
        info_form.addRow(self.info_depth_row_label, self.info_depth)
        side.addWidget(info_box)

        # Editing
        edit_box = QGroupBox("编辑 Edit")
        edit_layout = QVBoxLayout(edit_box)

        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("选中格新值:"))
        self.sel_value_edit = QLineEdit()
        self.sel_value_edit.setPlaceholderText("点击一个格子...")
        self.sel_value_edit.returnPressed.connect(self.commit_selected_value)
        sel_row.addWidget(self.sel_value_edit)
        apply_btn = QPushButton("应用 (Enter)")
        apply_btn.clicked.connect(self.commit_selected_value)
        sel_row.addWidget(apply_btn)
        edit_layout.addLayout(sel_row)

        brush_row = QHBoxLayout()
        self.paint_check = QCheckBox("画笔模式 Paint")
        self.paint_check.stateChanged.connect(self.on_paint_toggled)
        brush_row.addWidget(self.paint_check)
        brush_row.addWidget(QLabel("笔刷值:"))
        self.brush_value_edit = QLineEdit("0")
        brush_row.addWidget(self.brush_value_edit)
        pick_btn = QPushButton("取色 Pick")
        pick_btn.setToolTip("下一次点击地图将拾取该格的值作为笔刷值 (右键点击随时可拾取)")
        pick_btn.clicked.connect(self.arm_eyedropper)
        brush_row.addWidget(pick_btn)
        edit_layout.addLayout(brush_row)

        hint = QLabel("左键：选中/画笔涂抹　右键：拾取该格数值\n"
                       "中键拖动：平移地图　滚轮：缩放\n"
                       "方向键：在选中格的上下左右格间移动")
        hint.setStyleSheet("color: gray; font-size: 11px;")
        edit_layout.addWidget(hint)
        side.addWidget(edit_box)

        # Undo/redo + save
        hist_box = QGroupBox("历史 & 保存")
        hist_layout = QVBoxLayout(hist_box)
        btn_row = QHBoxLayout()
        self.undo_btn = QPushButton("撤销 Undo (Ctrl+Z)")
        self.undo_btn.clicked.connect(self.do_undo)
        self.redo_btn = QPushButton("重做 Redo (Ctrl+Shift+Z)")
        self.redo_btn.clicked.connect(self.do_redo)
        btn_row.addWidget(self.undo_btn)
        btn_row.addWidget(self.redo_btn)
        hist_layout.addLayout(btn_row)
        self.edit_count_label = QLabel("已修改 0 个格点")
        hist_layout.addWidget(self.edit_count_label)
        save_btn = QPushButton("保存 Save (Ctrl+S)")
        save_btn.clicked.connect(self.save_data)
        hist_layout.addWidget(save_btn)
        side.addWidget(hist_box)

        # Display
        disp_box = QGroupBox("显示 Display")
        disp_form = QFormLayout(disp_box)
        self.cmap_combo = QComboBox()
        self.cmap_combo.addItems(sorted(mpl.colormaps))
        self.cmap_combo.currentIndexChanged.connect(self.refresh_image)
        disp_form.addRow("配色 Colormap:", self.cmap_combo)
        self.stats_label = QLabel("-")
        self.stats_label.setWordWrap(True)
        disp_form.addRow("统计 min/max/mean:", self.stats_label)
        side.addWidget(disp_box)

        side.addStretch(1)
        self.sidebar_scroll = QScrollArea()
        self.sidebar_scroll.setWidgetResizable(True)
        self.sidebar_scroll.setWidget(sidebar)
        self.sidebar_scroll.setMinimumWidth(320)
        self.sidebar_scroll.setMaximumWidth(420)
        splitter.addWidget(self.sidebar_scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([960, 340])

        self.setCentralWidget(splitter)
        self.create_menu()
        self.create_shortcuts()
        self.statusBar().showMessage('GridVarEditor ready')

    def create_menu(self):
        file_menu = self.menuBar().addMenu("&File")
        open_action = QAction("&Open File(s)...", self, shortcut="Ctrl+O")
        open_action.triggered.connect(self.open_files_dialog)
        file_menu.addAction(open_action)
        opendir_action = QAction("Open &Directory...", self, shortcut="Ctrl+Shift+O")
        opendir_action.triggered.connect(self.open_directory_dialog)
        file_menu.addAction(opendir_action)
        close_action = QAction("&Close File", self, shortcut="Ctrl+W")
        close_action.triggered.connect(self.close_current_file)
        file_menu.addAction(close_action)
        file_menu.addSeparator()
        save_action = QAction("&Save", self, shortcut="Ctrl+S")
        save_action.triggered.connect(self.save_data)
        file_menu.addAction(save_action)
        saveas_action = QAction("Save &As...", self)
        saveas_action.triggered.connect(self.save_data_as)
        file_menu.addAction(saveas_action)

        edit_menu = self.menuBar().addMenu("&Edit")
        undo_action = QAction("&Undo", self, shortcut="Ctrl+Z")
        undo_action.triggered.connect(self.do_undo)
        edit_menu.addAction(undo_action)
        redo_action = QAction("&Redo", self, shortcut="Ctrl+Shift+Z")
        redo_action.triggered.connect(self.do_redo)
        edit_menu.addAction(redo_action)

        view_menu = self.menuBar().addMenu("&View")
        self.sidebar_action = QAction("显示控制栏", self, checkable=True, checked=True,
                                      shortcut="Ctrl+B")
        self.sidebar_action.toggled.connect(self.sidebar_scroll.setVisible)
        view_menu.addAction(self.sidebar_action)
        self.toolbar.addAction(self.sidebar_action)
        reset_action = QAction("Reset &Zoom", self, shortcut="Ctrl+0")
        reset_action.triggered.connect(self.reset_view)
        view_menu.addAction(reset_action)

        help_menu = self.menuBar().addMenu("&Help")
        about_action = QAction("&About", self, shortcut='F1')
        about_action.triggered.connect(self.on_about)
        help_menu.addAction(about_action)

    def create_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+Z"), self, self.do_undo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, self.do_redo)
        QShortcut(QKeySequence("Ctrl+Y"), self, self.do_redo)
        QShortcut(QKeySequence("Escape"), self, self.cancel_paint_mode)

    # ------------------------------------------------------------ variable
    def on_variable_changed(self, name):
        if not name or (self.current_fname is not None and name == self.dc.varname):
            return
        self.load_variable(name)

    def get_container(self, fname, varname):
        key = (fname, varname)
        if key not in self.containers:
            self.containers[key] = DataContainer(fname, varname, self.levels_file)
        return self.containers[key]

    def load_variable(self, varname):
        # Cached per (file, variable): switching away and back preserves
        # in-progress edits and undo history instead of discarding them.
        self.dc = self.get_container(self.current_fname, varname)
        self.last_var_for_file[self.current_fname] = varname
        self.selected_ij = None
        self.sel_value_edit.clear()

        self.var_combo.blockSignals(True)
        self.var_combo.setCurrentText(varname)
        self.var_combo.blockSignals(False)

        meta = "dtype: {0}   units: {1}\n{2}".format(
            self.dc.dtype, self.dc.units or "-", self.dc.long_name or "")
        self.var_meta_label.setText(meta)

        self.info_depth_row_label.setVisible(self.dc.level_depths is not None)
        self.info_depth.setVisible(self.dc.level_depths is not None)

        default_cmap = DEFAULT_MASKABLE_CMAP if self.dc.is_maskable else DEFAULT_CONTINUOUS_CMAP
        idx = self.cmap_combo.findText(default_cmap)
        self.cmap_combo.blockSignals(True)
        if idx >= 0:
            self.cmap_combo.setCurrentIndex(idx)
        self.cmap_combo.blockSignals(False)

        self.fig.clf()
        self.axes = self.fig.add_subplot(111)
        vmin, vmax = self._colorlimits()
        cmap = self._current_cmap()
        self.im = self.axes.imshow(self.dc.displayArray(), cmap=cmap, vmin=vmin, vmax=vmax,
                                    interpolation='nearest', origin='upper')
        self.axes.set_xlim(-0.5, self.dc.nx - 0.5)
        self.axes.set_ylim(self.dc.ny - 0.5, -0.5)
        # Physical aspect ratio (north-south / east-west) so the map looks
        # like a real map instead of squishing every grid cell into a square.
        self.apply_display_aspect()
        self.axes.set_xlabel('Column index (j)')
        self.axes.set_ylabel('Row index (i)')
        self.fig.subplots_adjust(left=0.075, right=0.94, top=0.97, bottom=0.085)
        self.colorbar_obj = self.fig.colorbar(self.im, ax=self.axes, fraction=0.046, pad=0.02)
        self.fig.tight_layout()

        # Both markers are drawn as 1x1 (data-unit) rectangles -- exactly the
        # footprint of one grid cell -- rather than fixed-size point markers,
        # so they scale correctly with zoom instead of drifting out of sync
        # with the actual cell size.
        self.hover_marker = Rectangle((-1, -1), 1, 1, fill=False,
                                       edgecolor='0.5', linewidth=1)
        self.hover_marker.set_visible(False)
        self.axes.add_patch(self.hover_marker)
        # Bold marker that stays on the last-clicked cell, so it's always
        # clear which cell is selected even after the mouse moves away.
        self.select_marker = Rectangle((-1, -1), 1, 1, fill=False,
                                        edgecolor='red', linewidth=2)
        self.select_marker.set_visible(False)
        self.axes.add_patch(self.select_marker)
        self.axes.callbacks.connect('xlim_changed', lambda ax: self.update_minimap_rect())
        self.axes.callbacks.connect('ylim_changed', lambda ax: self.update_minimap_rect())

        self.canvas.draw_idle()
        self.draw_minimap()
        self.update_stats()
        self.update_undo_redo_buttons()
        self.setWindowTitle('GridVarEditor - {0} [{1}]'.format(self.current_fname, varname))
        self.statusBar().showMessage('Editing variable: {0}'.format(varname), 3000)

    def _current_cmap(self):
        cmap = mpl.colormaps[self.cmap_combo.currentText()].copy()
        if self.dc.is_maskable:
            cmap.set_bad(LAND_COLOR)
        return cmap

    def _colorlimits(self):
        vmin, vmax = self.dc.stats()[0], self.dc.stats()[1]
        if vmax <= vmin:
            vmax = vmin + 1
        return vmin, vmax

    # --------------------------------------------------------------- render
    def apply_display_aspect(self, *_):
        if not hasattr(self, 'dc'):
            return
        aspect = ('auto', 1.0, self.dc.aspect)[self.aspect_combo.currentIndex()]
        self.axes.set_aspect(aspect, adjustable='box')
        self.canvas.draw_idle()

    def open_map_preview(self):
        if not hasattr(self, 'dc'):
            self.statusBar().showMessage('请先打开网格文件', 5000)
            return
        if getattr(self, 'map_preview', None) is not None:
            self.map_preview.close()
            self.map_preview.deleteLater()
            self.map_preview = None
        try:
            self.map_preview = MapPreview(self.dc.lons, self.dc.lats, self.dc.data,
                                          self.dc.varname, self.dc.units,
                                          self._current_cmap(), self.dc.is_maskable,
                                          os.path.basename(self.dc.fname), self)
        except ValueError as error:
            QMessageBox.warning(self, '无法预览地图', str(error))
            return
        self.map_preview.show()

    def refresh_image(self):
        self.im.set_data(self.dc.displayArray())
        self.im.set_cmap(self._current_cmap())
        vmin, vmax = self._colorlimits()
        self.im.set_clim(vmin, vmax)
        self.canvas.draw_idle()
        self.update_stats()
        self.update_undo_redo_buttons()

    def update_stats(self):
        vmin, vmax, vmean = self.dc.stats()
        fmt = "{0:d} / {1:d} / {2:.2f}" if self.dc.is_integer else "{0:.3f} / {1:.3f} / {2:.3f}"
        if self.dc.is_integer:
            self.stats_label.setText(fmt.format(int(vmin), int(vmax), vmean))
        else:
            self.stats_label.setText(fmt.format(vmin, vmax, vmean))
        self.edit_count_label.setText("已修改 {0} 个格点".format(self.dc.editCount()))

    def update_undo_redo_buttons(self):
        self.undo_btn.setEnabled(self.dc.canUndo())
        self.redo_btn.setEnabled(self.dc.canRedo())

    # --------------------------------------------------------------- minimap
    def draw_minimap(self):
        self.mini_axes.clear()
        self.mini_axes.gridlines(draw_labels=False, linewidth=0.3, color='gray', alpha=0.6)
        self.mini_axes.set_extent(
            [float(self.dc.lons.min()), float(self.dc.lons.max()),
             float(self.dc.lats.min()), float(self.dc.lats.max())],
            crs=ccrs.PlateCarree())
        background = self.dc.data if self.dc.is_maskable else (self.dc.data != 0).astype(float)
        step = max(1, int(max(self.dc.ny, self.dc.nx) / 300))
        # Bypass GeoAxes.pcolormesh's dateline-wrap splitting (fragile on
        # paleogeographic/regional grids); the axes are already plain
        # PlateCarree in degrees so no reprojection is needed.
        mpl.axes.Axes.pcolormesh(
            self.mini_axes, self.dc.lons[::step, ::step], self.dc.lats[::step, ::step],
            background[::step, ::step], cmap='Greys', shading='auto')
        self.rect_patch = None
        self.update_minimap_rect()

    def update_minimap_rect(self):
        if not hasattr(self, 'dc'):
            return
        x0, x1 = self.axes.get_xlim()
        y0, y1 = self.axes.get_ylim()
        si = int(np.clip(min(y0, y1), 0, self.dc.ny - 1))
        ei = int(np.clip(max(y0, y1), 0, self.dc.ny - 1))
        sj = int(np.clip(min(x0, x1), 0, self.dc.nx - 1))
        ej = int(np.clip(max(x0, x1), 0, self.dc.nx - 1))

        top = list(zip(self.dc.lons[si, sj:ej + 1], self.dc.lats[si, sj:ej + 1]))
        right = list(zip(self.dc.lons[si:ei + 1, ej], self.dc.lats[si:ei + 1, ej]))
        bottom = list(zip(self.dc.lons[ei, sj:ej + 1][::-1], self.dc.lats[ei, sj:ej + 1][::-1]))
        left = list(zip(self.dc.lons[si:ei + 1, sj][::-1], self.dc.lats[si:ei + 1, sj][::-1]))
        boundary = top + right + bottom + left
        if len(boundary) < 3:
            return
        lons, lats = zip(*boundary)

        if self.rect_patch is not None:
            self.rect_patch.remove()
        (self.rect_patch,) = self.mini_axes.plot(lons, lats, transform=ccrs.PlateCarree(),
                                                   color='crimson', linewidth=1.5)
        self.mini_canvas.draw_idle()

    def on_minimap_click(self, event):
        if self.current_fname is None or event.xdata is None or event.ydata is None:
            return
        lon_click, lat_click = event.xdata, event.ydata
        dist2 = (self.dc.lons - lon_click) ** 2 + (self.dc.lats - lat_click) ** 2
        i, j = np.unravel_index(np.argmin(dist2), dist2.shape)
        half = 40
        self.axes.set_xlim(j - half, j + half)
        self.axes.set_ylim(i + half, i - half)
        self.canvas.draw_idle()

    def reset_view(self):
        if self.current_fname is None:
            return
        self.axes.set_xlim(-0.5, self.dc.nx - 0.5)
        self.axes.set_ylim(self.dc.ny - 0.5, -0.5)
        self.canvas.draw_idle()

    # -------------------------------------------------------------- editing
    def cell_at_event(self, event):
        if event.xdata is None or event.ydata is None:
            return None
        j = int(np.floor(event.xdata + 0.5))
        i = int(np.floor(event.ydata + 0.5))
        if 0 <= i < self.dc.ny and 0 <= j < self.dc.nx:
            return i, j
        return None

    def on_press(self, event):
        if self.current_fname is None:
            return
        if event.button == 2:  # middle-mouse-drag: pan (works even if not yet inside axes limits)
            if event.inaxes != self.axes:
                return
            self._pan_start_pixel = (event.x, event.y)
            self._pan_start_lims = (self.axes.get_xlim(), self.axes.get_ylim())
            self._pan_inv = self.axes.transData.inverted()
            return

        if event.inaxes != self.axes:
            return
        cell = self.cell_at_event(event)
        if cell is None:
            return
        i, j = cell

        if event.button == 3 or self.eyedrop_pending:
            self.brush_value_edit.setText(self._format_value(self.dc.valueAt(i, j)))
            self.eyedrop_pending = False
            self.select_cell(i, j)
            return

        if event.button != 1:
            return

        self.select_cell(i, j)
        if self.painting:
            value = self._parse_brush_value()
            if value is not None:
                self.dc.beginGroup()
                if self.dc.setValue(i, j, value):
                    self._painting_stroke = True
                self.refresh_image()

    def on_motion(self, event):
        if self.current_fname is None:
            return
        if self._pan_start_pixel is not None and event.x is not None and event.y is not None:
            x0_data, y0_data = self._pan_inv.transform(self._pan_start_pixel)
            x1_data, y1_data = self._pan_inv.transform((event.x, event.y))
            dx, dy = x1_data - x0_data, y1_data - y0_data
            xlim0, ylim0 = self._pan_start_lims
            self.axes.set_xlim(xlim0[0] - dx, xlim0[1] - dx)
            self.axes.set_ylim(ylim0[0] - dy, ylim0[1] - dy)
            self.canvas.draw_idle()
            return

        if event.inaxes != self.axes:
            self.hover_marker.set_visible(False)
            self.canvas.draw_idle()
            return
        cell = self.cell_at_event(event)
        if cell is None:
            self.hover_marker.set_visible(False)
            self.canvas.draw_idle()
            return
        i, j = cell
        self.hover_marker.set_xy((j - 0.5, i - 0.5))
        self.hover_marker.set_visible(True)
        self.update_info_panel(i, j)

        # drag-painting: event.button reflects the currently held-down button
        if self.painting and event.button == 1:
            value = self._parse_brush_value()
            if value is not None and self.dc.setValue(i, j, value):
                self.refresh_image()
        self.canvas.draw_idle()

    def on_release(self, event):
        if event.button == 2:
            self._pan_start_pixel = None
            self._pan_start_lims = None
            self._pan_inv = None
            return
        if self._painting_stroke:
            self.dc.endGroup()
            self._painting_stroke = False
            self.update_undo_redo_buttons()

    def on_scroll(self, event):
        if event.inaxes != self.axes or event.xdata is None:
            return
        factor = 0.8 if event.button == 'up' else 1.25
        x0, x1 = self.axes.get_xlim()
        y0, y1 = self.axes.get_ylim()
        xdata, ydata = event.xdata, event.ydata

        new_x0 = xdata - (xdata - x0) * factor
        new_x1 = xdata + (x1 - xdata) * factor
        new_y0 = ydata - (ydata - y0) * factor
        new_y1 = ydata + (y1 - ydata) * factor

        self.axes.set_xlim(new_x0, new_x1)
        self.axes.set_ylim(new_y0, new_y1)
        self.canvas.draw_idle()

    def select_cell(self, i, j):
        self.selected_ij = (i, j)
        self.select_marker.set_xy((j - 0.5, i - 0.5))
        self.select_marker.set_visible(True)
        self.sel_value_edit.setText(self._format_value(self.dc.valueAt(i, j)))
        self.sel_value_edit.selectAll()
        self.update_info_panel(i, j)

    # ----------------------------------------------------- keyboard movement
    _ARROW_DELTAS = {
        'up': (-1, 0), 'down': (1, 0), 'left': (0, -1), 'right': (0, 1),
    }

    def on_key_press(self, event):
        if self.current_fname is None:
            return
        delta = self._ARROW_DELTAS.get(event.key)
        if delta is None:
            return
        if self.selected_ij is None:
            x0, x1 = self.axes.get_xlim()
            y0, y1 = self.axes.get_ylim()
            i = int(round((y0 + y1) / 2.0))
            j = int(round((x0 + x1) / 2.0))
        else:
            i, j = self.selected_ij
            i += delta[0]
            j += delta[1]
        i = int(np.clip(i, 0, self.dc.ny - 1))
        j = int(np.clip(j, 0, self.dc.nx - 1))
        self.select_cell(i, j)
        self.ensure_cell_visible(i, j)
        self.canvas.draw_idle()

    def ensure_cell_visible(self, i, j, margin=2):
        """Pan the view just enough to keep (i, j) on screen, if it isn't."""
        x0, x1 = self.axes.get_xlim()
        y0, y1 = self.axes.get_ylim()
        xlo, xhi = min(x0, x1), max(x0, x1)
        ylo, yhi = min(y0, y1), max(y0, y1)
        margin = min(margin, (xhi - xlo) / 2.0, (yhi - ylo) / 2.0)

        dx = dy = 0.0
        if j < xlo + margin:
            dx = j - (xlo + margin)
        elif j > xhi - margin:
            dx = j - (xhi - margin)
        if i < ylo + margin:
            dy = i - (ylo + margin)
        elif i > yhi - margin:
            dy = i - (yhi - margin)

        if dx or dy:
            self.axes.set_xlim(x0 + dx, x1 + dx)
            self.axes.set_ylim(y0 + dy, y1 + dy)

    def update_info_panel(self, i, j):
        self.info_ij.setText("{0}, {1}".format(i, j))
        self.info_latlon.setText("{0:.3f}, {1:.3f}".format(self.dc.lats[i, j], self.dc.lons[i, j]))
        self.info_value.setText(self._format_value(self.dc.valueAt(i, j)))
        if self.dc.level_depths is not None:
            depth = self.dc.depthAt(i, j)
            self.info_depth.setText("{0:.1f}".format(depth) if depth is not None else "n/a")

    def _format_value(self, v):
        return "{0:d}".format(int(v)) if self.dc.is_integer else "{0:.4f}".format(float(v))

    def _parse_brush_value(self):
        return self._parse_value(self.brush_value_edit.text())

    def _parse_value(self, text):
        try:
            v = float(text)
        except (TypeError, ValueError):
            return None
        if self.dc.is_integer:
            v = int(round(v))
        if self.dc.level_depths is not None and (v < 0 or v > len(self.dc.level_depths)):
            return None
        return v

    def commit_selected_value(self):
        if self.selected_ij is None:
            self.statusBar().showMessage('先在地图上点击一个格子', 2000)
            return
        value = self._parse_value(self.sel_value_edit.text())
        if value is None:
            QMessageBox.critical(self, "Invalid Value", "输入的值无效或超出范围")
            return
        i, j = self.selected_ij
        self.dc.beginGroup()
        changed = self.dc.setValue(i, j, value)
        self.dc.endGroup()
        if changed:
            self.refresh_image()
            self.update_info_panel(i, j)
            self.statusBar().showMessage('({0},{1}) -> {2}'.format(i, j, value), 2000)

    def on_paint_toggled(self, state):
        self.painting = state == Qt.Checked

    def cancel_paint_mode(self):
        self.paint_check.setChecked(False)

    def arm_eyedropper(self):
        self.eyedrop_pending = True
        self.statusBar().showMessage('在地图上点击以拾取该格数值', 3000)

    def do_undo(self):
        if self.current_fname is None:
            return
        self.dc.undo()
        self.refresh_image()

    def do_redo(self):
        if self.current_fname is None:
            return
        self.dc.redo()
        self.refresh_image()

    # ------------------------------------------------------------- saving
    def save_data(self):
        if self.current_fname is None:
            self.statusBar().showMessage('还没有打开任何文件', 2000)
            return
        if self.dc.ofile is None:
            fileroot, fileext = os.path.splitext(self.dc.fname)
            if not fileext:
                fileext = ".nc"
            self.dc.ofile = "{0}_{1}_edited{2}".format(fileroot, self.dc.varname, fileext)
        self._do_save(self.dc.ofile)

    def save_data_as(self):
        if self.current_fname is None:
            return
        ofile, _ = QFileDialog.getSaveFileName(self, "Save As", self.dc.ofile or self.dc.fname, "NetCDF (*.nc)")
        if ofile:
            self.dc.ofile = ofile
            self.dc.asked_about_overwrite_permission = True  # user explicitly chose this path
            self._do_save(ofile, force_clobber=True)

    def _do_save(self, ofile, force_clobber=False):
        if not self.dc.asked_about_overwrite_permission or force_clobber:
            clobber = force_clobber
            if os.path.exists(ofile) and not force_clobber:
                reply = QMessageBox.question(
                    self, 'Saving file...', "文件 {0} 已存在，覆盖吗？".format(ofile),
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if reply != QMessageBox.Yes:
                    self.statusBar().showMessage('Save cancelled', 2000)
                    return
                clobber = True
            gridio.clone_netcdf(self.dc.fname, ofile, clobber=clobber)
            self.dc.asked_about_overwrite_permission = True

        ncfile = Dataset(ofile, "a")
        ncfile.variables[self.dc.varname][:, :] = self.dc.data
        ncfile.close()

        rows, cols, values = self.dc.changedCells()
        changes = np.column_stack([rows, cols, values]) if rows.size else np.zeros((0, 3))
        gridio.append_change_log(ofile, self.dc.varname, self.dc.orig_data, changes)

        self.statusBar().showMessage('Saved to file: {0}'.format(ofile), 3000)

    def on_about(self):
        QMessageBox.about(self, "About",
                           "GridVarEditor\n\n可视化编辑 POP 海洋模式网格上的 2D 字段 "
                           "(KMT 层数索引、水深/地形、区域掩码等)，支持同时打开多个文件/整个目录并切换。\n\n"
                           "鼠标滚轮缩放、中键拖动平移，左键点选/画笔编辑，右键取色，方向键在格子间移动。")

    def closeEvent(self, event):
        dirty = [(fname, varname) for (fname, varname), dc in self.containers.items() if dc.dirty]
        if dirty:
            detail = "\n".join("{0} [{1}]".format(os.path.basename(f), v) for f, v in dirty)
            reply = QMessageBox.question(
                self, 'Message', "以下变量有未保存的修改，确定退出吗？\n\n{0}".format(detail),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.Yes:
                event.accept()
            else:
                event.ignore()


def main():
    parser = argparse.ArgumentParser(description='View and edit 2D fields on POP ocean grid file(s)')
    parser.add_argument('fname', type=str, nargs='*',
                         help='name of one or more NetCDF grid files to open (more can be added '
                              'from the app via File > Open File(s)/Open Directory)')
    parser.add_argument('-d', '--dir', type=str, default=None,
                         help='open every *.nc file in this directory')
    parser.add_argument('-v', '--var', type=str, default=None,
                         help='name of the 2D variable to edit initially (can be changed in the app)')
    parser.add_argument('--levels', type=str, default=None,
                         help='optional NetCDF gridinfo file supplying z_w/dz, used to show the '
                              'water depth (m) that a KMT-style level index corresponds to')
    args = parser.parse_args()

    fnames = list(args.fname)
    if args.dir:
        fnames.extend(sorted(glob.glob(os.path.join(args.dir, "*.nc"))))

    app = QApplication(sys.argv)
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "topoicon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    mw = GridVarEditor(fnames, args.var, levels_file=args.levels)
    mw.show()
    mw.raise_()
    app.exec_()


if __name__ == "__main__":
    main()
