# =============================================================================
# 代码名称：POP 海洋网格 NetCDF 读写工具函数 gridio
# 代码作者：何悠
# 联系方式：tr1vial@foxmail.com
# 当前版本：v1.0.0
# 版本日期：2026-08-14
# 主要功能：发现 POP 网格文件的经纬度坐标与可编辑二维变量、计算显示纵横
#           比、读取垂向层深表、克隆 NetCDF 文件并写入修改记录，供
#           GridVarEditor.py 调用。
# 使用方法：参见 README.md
# =============================================================================
"""
gridio.py

Shared helpers for reading and writing POP ocean grid files (curvilinear
2D T/U-grid NetCDF files such as the ones produced by CESM/POP tools and
stored under origin/ and work/ in this repository). Used by GridVarEditor.py.

Python 3 only.
"""

import time
import numpy as np
from netCDF4 import Dataset


# Candidate names for the 2D latitude/longitude coordinate variables found
# on POP T-grid or U-grid files. Order matters: first match wins.
LAT_CANDIDATES = ["TLAT", "ULAT"]
LON_CANDIDATES = ["TLONG", "ULONG", "ULON"]

# Candidate names for a 1D vertical level table giving the depth (in
# centimeters, per POP convention) of the bottom of each level. Used to
# translate a KMT-style "deepest level index" into an actual water depth.
LEVEL_BOTTOM_DEPTH_CANDIDATES = ["z_w_bot", "z_w"]
LEVEL_THICKNESS_CANDIDATES = ["dz"]

# Candidate names for grid cell metrics (T-cell east-west/north-south
# physical size), used only to give the map a realistic aspect ratio instead
# of stretching every cell into a square.
WIDTH_CANDIDATES = ["HTN", "HUW"]
HEIGHT_CANDIDATES = ["HTE", "HUS"]


class GridVariable(object):
    """Describes one 2D variable in the file that is a candidate for editing."""

    def __init__(self, name, dtype, dimensions, long_name, units):
        self.name = name
        self.dtype = dtype
        self.dimensions = dimensions
        self.long_name = long_name
        self.units = units

    def __str__(self):
        bits = [self.name]
        if self.long_name:
            bits.append("({0})".format(self.long_name))
        if self.units:
            bits.append("[{0}]".format(self.units))
        return " ".join(bits)


def find_variable(variables, candidates):
    """Return the first name in `candidates` that exists in `variables`, else None."""
    for name in candidates:
        if name in variables:
            return name
    return None


def discover_coordinates(ncfile):
    """
    Locate the 2D latitude/longitude coordinate arrays in a POP grid file.

    RETURNS
        (lat_name, lon_name, lat2d, lon2d)
    RAISES
        KeyError if no recognized pair of 2D coordinate variables is found.
    """
    variables = ncfile.variables
    lat_name = find_variable(variables, LAT_CANDIDATES)
    lon_name = find_variable(variables, LON_CANDIDATES)
    if lat_name is None or lon_name is None:
        raise KeyError(
            "Could not find 2D latitude/longitude coordinate variables. "
            "Tried lat: {0}, lon: {1}. Available variables: {2}".format(
                LAT_CANDIDATES, LON_CANDIDATES, ", ".join(variables.keys())))

    lat2d = variables[lat_name][:, :]
    lon2d = variables[lon_name][:, :]
    if lat2d.shape != lon2d.shape:
        raise ValueError("{0} and {1} do not have matching shapes".format(lat_name, lon_name))
    return lat_name, lon_name, lat2d, lon2d


def discover_editable_variables(ncfile, grid_shape):
    """
    Return a list of GridVariable objects for every 2D variable in the file
    whose shape matches `grid_shape`. Coordinate/metric helper variables are
    not excluded on purpose -- editing ULAT/HTN etc. is unusual but not
    forbidden, and filtering them out silently could hide the variable the
    user actually wants.
    """
    out = []
    for name, var in ncfile.variables.items():
        if var.ndim != 2:
            continue
        if var.shape != grid_shape:
            continue
        out.append(GridVariable(
            name=name,
            dtype=var.dtype,
            dimensions=var.dimensions,
            long_name=getattr(var, "long_name", getattr(var, "description", "")),
            units=getattr(var, "units", "")))
    out.sort(key=lambda v: v.name.lower())
    return out


def compute_physical_aspect(lat2d, lon2d, cell_widths, cell_heights):
    """
    Return the approximate physical (north-south / east-west) size ratio for
    a grid, so a plotted view in (row, col) index space can use a matplotlib
    axes aspect ratio that looks like a real map instead of stretching every
    grid cell into a square.

    This is a single representative value for the whole grid (the median
    over all cells), which is only exact for a locally uniform patch -- POP
    grids can be strongly distorted approaching the pole(s) of their
    displaced grid, so treat this as "looks roughly right", not precise.
    """
    if cell_widths is not None and cell_heights is not None:
        widths = np.asarray(cell_widths, dtype=float)
        heights = np.asarray(cell_heights, dtype=float)
        valid = np.isfinite(widths) & (widths > 0) & np.isfinite(heights) & (heights > 0)
        if np.any(valid):
            return float(np.median(heights[valid]) / np.median(widths[valid]))

    lats = np.asarray(lat2d, dtype=float)
    lons = np.asarray(lon2d, dtype=float)
    if lats.shape[0] < 2 or lats.shape[1] < 2:
        return 1.0

    dx = _great_circle_distance(lats[:, :-1], lons[:, :-1], lats[:, 1:], lons[:, 1:])
    dy = _great_circle_distance(lats[:-1, :], lons[:-1, :], lats[1:, :], lons[1:, :])
    dx = dx[np.isfinite(dx) & (dx > 0)]
    dy = dy[np.isfinite(dy) & (dy > 0)]
    if dx.size == 0 or dy.size == 0:
        return 1.0
    return float(np.median(dy) / np.median(dx))


def _great_circle_distance(lat1, lon1, lat2, lon2):
    """Vectorized angular great-circle distance (in radians) with longitude wrapping."""
    radians = np.pi / 180.0
    dlat = (lat2 - lat1) * radians
    dlon = ((lon2 - lon1 + 180.0) % 360.0 - 180.0) * radians
    lat1 = lat1 * radians
    lat2 = lat2 * radians
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2.0) ** 2
    a = np.clip(a, 0.0, 1.0)
    return 2.0 * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))


def load_level_bottom_depths(fname):
    """
    Load a 1D array giving the depth (in meters, positive down) of the
    bottom of each vertical level, from a POP grid/gridinfo file. This is
    what turns a KMT-style level index into an actual water depth.

    Tries z_w_bot / z_w (cumulative depth to bottom of level, centimeters in
    POP convention) first, and falls back to integrating dz (level
    thickness, centimeters).

    RETURNS a 1D numpy array in meters, or None if no level table is found.
    """
    ncfile = Dataset(fname, "r")
    try:
        variables = ncfile.variables
        name = find_variable(variables, LEVEL_BOTTOM_DEPTH_CANDIDATES)
        if name is not None:
            depths_cm = np.asarray(variables[name][:], dtype=float)
            return depths_cm / 100.0

        name = find_variable(variables, LEVEL_THICKNESS_CANDIDATES)
        if name is not None:
            dz_cm = np.asarray(variables[name][:], dtype=float)
            return np.cumsum(dz_cm) / 100.0

        return None
    finally:
        ncfile.close()


def clone_netcdf(fname, ofile, clobber=False):
    """
    Make a byte-for-byte structural copy of a NetCDF file (all dimensions,
    variables, data and attributes), writing it out as NETCDF4_CLASSIC. This
    is used so that editors can save their changes into a new file without
    disturbing the original.
    """
    src = Dataset(fname, "r")
    dst = Dataset(ofile, "w", clobber=clobber, format="NETCDF4_CLASSIC")
    try:
        dst.setncatts({k: src.getncattr(k) for k in src.ncattrs()})

        for dimname, dim in src.dimensions.items():
            dst.createDimension(dimname, None if dim.isunlimited() else len(dim))

        for varname, var in src.variables.items():
            fill_value = getattr(var, "_FillValue", None)
            newvar = dst.createVariable(varname, var.dtype, var.dimensions,
                                         fill_value=fill_value, zlib=True, complevel=4)
            attrs = {k: var.getncattr(k) for k in var.ncattrs() if k != "_FillValue"}
            newvar.setncatts(attrs)
            newvar[:] = var[:]
    finally:
        src.close()
        dst.close()


def append_change_log(ofile, varname, orig_data, changes):
    """
    Write the original (pre-edit) data for `varname` plus the log of
    individual cell edits into an already-cloned output file, following the
    convention used by the older KMTEditor/TopoEditor tools:
        original_<varname> : the untouched data, for reference/diffing
        changes_<varname>  : an (N, 3) array of (i, j, new_value) rows

    ARGUMENTS
        ofile     - path to the NetCDF file to update (already created by clone_netcdf)
        varname   - name of the edited variable
        orig_data - the full 2D array as it was before any edits (already in
                    the file's native i/j orientation)
        changes   - an (N, 3) array of (i, j, new_value) rows describing every edit
    """
    ncfile = Dataset(ofile, "a")
    try:
        original_name = "original_{0}".format(varname)
        changes_name = "changes_{0}".format(varname)
        changes_dim = "changes_{0}".format(varname)

        srcvar = ncfile.variables[varname]

        if original_name not in ncfile.variables:
            ovar = ncfile.createVariable(original_name, srcvar.dtype, srcvar.dimensions, zlib=True)
            ovar.description = "Original {0} data, before edits made by GridVarEditor.py".format(varname)
            ovar[:, :] = orig_data

        if changes.shape[0] > 0:
            if changes_dim not in ncfile.dimensions:
                ncfile.createDimension(changes_dim, None)
            if "rc" not in ncfile.dimensions:
                ncfile.createDimension("rc", 3)

            if changes_name not in ncfile.variables:
                cvar = ncfile.createVariable(changes_name, "f8", (changes_dim, "rc"))
                cvar.description = "Edits made to {0} by GridVarEditor.py: (i, j, new_value)".format(varname)
            else:
                cvar = ncfile.variables[changes_name]
            cvar[:, :] = changes

        ncfile.history = "Modified by GridVarEditor.py"
        ncfile.edited_variable = varname
        ncfile.modified = time.ctime()
    finally:
        ncfile.close()
