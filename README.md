# GridVarEditor

可视化编辑 CESM/POP 海洋模式曲线网格上的二维字段（KMT 有效层数、水深/地形 `elevation`、区域掩码 `REGION_MASK` 等）的桌面工具。地图查看器风格的交互：整张网格一次性显示，鼠标滚轮缩放、中键拖动平移，左键点选/画笔编辑，支持同时打开多个文件并随时切换而不丢失修改。

这是 [cesmGUITools](../README.md) 仓库中旧版 `KMTEditor.py`/`TopoEditor.py`（Python 2.7 + PyQt4 + Basemap）的重新实现，基于 Python 3 + PyQt5 + Cartopy，独立成一个自包含的子项目，可以单独打包分发。

## 功能特性

- 打开任意与 POP 网格坐标（`TLAT`/`ULAT` + `TLONG`/`ULONG`/`ULON`）形状一致的 2D NetCDF 变量进行编辑，不限定于某一个固定变量
- 整张网格一次性显示，鼠标滚轮缩放、中键拖动平移，按真实物理比例显示（不是被拉伸成正方形网格）
- 左键点选单格输入新值，或开启"画笔模式"拖拽连续涂抹；右键取色；方向键在相邻格间移动选中位置
- 完整的撤销/重做（`Ctrl+Z`/`Ctrl+Shift+Z`），以"一次操作"（含一次拖拽涂抹）为单位
- 支持同时打开多个文件，侧栏文件列表随时切换；切换文件/变量不会丢弃未保存的修改
- 编辑 KMT 式层数索引变量时，若提供层深表（`z_w`/`dz`），自动实时显示对应的实际水深（米）
- 保存时不覆盖原文件，另存为新文件并附带 `original_<var>`/`changes_<var>` 修改记录，可追溯

## 安装

推荐 Python 3.11（3.9+ 均可），用标准的 `venv` + `pip` 即可，不依赖任何额外的包管理器：

```bash
cd GridVarEditor
python -m venv .venv

# Windows
.venv\Scripts\activate
# Linux / macOS
source .venv/bin/activate

pip install -r requirements.txt
```

会安装好 `netCDF4`、`numpy`、`matplotlib`、`PyQt5`（锁定 5.15.11 + Qt5 5.15.2，避免解析到没有预编译包的新版本）、`cartopy`。

如果更喜欢用 conda 管理环境，也可以 `conda create -n gridvareditor python=3.11` 后在该环境里执行同样的 `pip install -r requirements.txt`。

也可以选用 [uv](https://docs.astral.sh/uv/)（更快，且带锁定版本的 `uv.lock`），效果等价，在本目录下执行一次：

```bash
cd GridVarEditor
uv sync
```

`.python-version` 固定使用 Python 3.11，`uv.lock` 中已包含 Linux（manylinux）平台的预编译包，同一份锁文件在 Windows/Linux/macOS 下都可以直接 `uv sync`。

### Linux 环境

PyQt5 在 Linux 上以无头方式运行会缺少 Qt 的 `xcb` 平台插件依赖，`cartopy` 依赖 GEOS/PROJ 的系统库，需要先装好系统依赖。

Debian/Ubuntu：

```bash
sudo apt update
sudo apt install -y \
    libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 \
    libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 \
    libdbus-1-3 libfontconfig1 libxrender1 libxi6 \
    libgeos-dev libproj-dev proj-data
```

RHEL/CentOS/Fedora（`dnf`，包名可能因发行版版本略有差异）：

```bash
sudo dnf install -y \
    mesa-libGL mesa-libEGL libxkbcommon libxkbcommon-x11 \
    xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm \
    dbus-libs fontconfig libXrender libXi \
    geos-devel proj-devel proj-data
```

也可以直接运行 `scripts/setup_linux.sh`，自动识别 `apt`/`dnf` 安装好系统依赖，再用 `venv` + `pip` 装好 Python 依赖（检测到 `uv` 时也可选用它代替）：

```bash
cd GridVarEditor
bash scripts/setup_linux.sh
```

系统依赖装好后，安装 Python 依赖并运行的方式与其他平台一致（`venv` + `pip`，或替换成 `uv sync` / `uv run`）：

```bash
cd GridVarEditor
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python GridVarEditor.py --dir ../work
```

远程服务器/SSH 场景下需要显示窗口，本工具是本地 GUI 程序，需要一个可用的图形显示环境，可选：

- 通过 `ssh -X`/`ssh -Y` 转发 X11 后在远程执行（网络延迟大时体验较差）；
- 或用 VNC/NoMachine 等远程桌面方案，在远程图形会话中运行；
- 纯无显示环境（如 CI）无法运行本工具（PyQt5 GUI 依赖真实或虚拟显示，若只需验证依赖能否装好/程序能否导入，可用 `xvfb-run python GridVarEditor.py` 配合 `Xvfb` 虚拟显示）。

## 使用

先激活虚拟环境（`source .venv/bin/activate` 或 Windows 下 `.venv\Scripts\activate`），再用 `python` 运行（用 uv 管理环境的话把下面的 `python GridVarEditor.py` 换成 `uv run GridVarEditor.py` 即可，无需手动激活）：

```bash
# 打开单个文件
python GridVarEditor.py ../work/gridkmt_PIdefault_260804.nc

# 一次打开多个文件
python GridVarEditor.py ../work/*.nc

# 打开整个目录下的所有 .nc 文件
python GridVarEditor.py --dir ../work

# 不带参数启动，之后用 File > Open 打开
python GridVarEditor.py
```

命令行参数：

| 参数 | 说明 |
| --- | --- |
| `fname ...` | 要打开的一个或多个 NetCDF 文件路径（可选，也可以启动后再打开） |
| `-d, --dir DIR` | 打开目录下所有 `*.nc` 文件 |
| `-v, --var VAR` | 启动时默认编辑的变量名（之后可在侧栏随时切换） |
| `--levels FILE` | 提供一个包含 `z_w`/`dz` 垂向层深表的 gridinfo 文件，用于把 KMT 层数索引换算成实际水深（米） |

## 操作说明

| 操作 | 效果 |
| --- | --- |
| 左键点击格子 | 选中该格（红框标记，跟随缩放精确对齐单元格大小），侧栏"选中格新值"框显示当前值 |
| 输入新值后回车 / 点"应用" | 提交对选中格的修改 |
| 勾选"画笔模式"后左键点击/拖动 | 用侧栏"笔刷值"连续涂抹多个格子（一次拖拽算一个撤销单元） |
| 右键点击格子 | 取色：把该格的值读入笔刷值（无需先勾选画笔模式） |
| 方向键（上下左右） | 在已选中格的上下左右相邻格间移动选中位置，视图会自动小幅平移使其保持可见 |
| 鼠标滚轮 | 以光标位置为中心缩放 |
| 中键拖动 | 平移地图 |
| 侧栏"总览"小地图点击 | 跳转主视图到该经纬度附近 |
| `Ctrl+Z` / `Ctrl+Shift+Z` | 撤销 / 重做 |
| `Ctrl+S` | 保存为 `原文件名_变量名_edited.nc`，不覆盖原文件 |
| `Ctrl+O` / `Ctrl+Shift+O` | 打开文件 / 打开目录 |
| 侧栏"变量"下拉框 | 切换当前编辑的变量（同文件内） |
| 侧栏"文件"列表 | 切换当前正在编辑的文件（支持同时打开多个文件） |

## 项目结构

```text
GridVarEditor/
├── README.md              # 本文件
├── requirements.txt        # pip 依赖声明（venv + pip 安装方式）
├── pyproject.toml          # uv 依赖声明（可选的 uv 安装方式）
├── uv.lock                 # uv 锁定的依赖版本（可选）
├── .python-version         # 固定 Python 3.11（供 uv 使用）
├── GridVarEditor.py        # 主程序（GUI + 交互 + 数据模型）
├── gridio.py                # 与 NetCDF/POP 网格相关的无状态工具函数
├── resources/
│   └── topoicon.png        # 窗口图标（可选，缺失也不影响运行）
├── scripts/
│   └── setup_linux.sh      # Linux 系统依赖安装 + uv sync 一体化脚本
└── docs/
    └── 技术文档.md          # 实现过程的详细技术记录
```

`GridVarEditor.py` 和同目录下的 `gridio.py` 是仅有的两个源码文件，直接 `import gridio` 即可，不依赖仓库中的其他任何代码——整个 `GridVarEditor/` 文件夹可以完整拷贝到别处独立使用。

## 保存格式

保存时不会覆盖原文件：先把整个 NetCDF 文件原样克隆一份，写入编辑后的变量数据，再附加两个辅助变量方便追溯——`original_<var>`（编辑前的原始数据）和 `changes_<var>`（形状 `(N, 3)` 的 `(i, j, new_value)` 修改记录）。

## 已知限制

- 显示纵横比是整张网格的单一近似值（取格点物理尺寸或经纬度大圆距离的全局中位数），不是逐格精确的地图投影；跨越很大纬度范围的网格在高纬区域会有可见形变。
- "是否需要陆地掩膜/换算水深"是基于变量名的启发式判断（变量名含 `kmt`/`kmu` 子串），不是读取语义属性。
- 多文件缓存没有内存上限，长时间打开大量大网格文件会持续占用内存。
