#!/usr/bin/env bash
# 一次性安装 GridVarEditor 在 Linux 上运行所需的系统依赖（Qt xcb 平台插件、GEOS/PROJ），
# 然后安装 Python 依赖：有 uv 就用 uv sync，否则退回 venv + pip。
# 支持 Debian/Ubuntu (apt) 和 RHEL/CentOS/Fedora (dnf)。
set -euo pipefail

cd "$(dirname "$0")/.."

if command -v apt >/dev/null 2>&1; then
    sudo apt update
    sudo apt install -y \
        libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 \
        libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
        libxcb-randr0 libxcb-render-util0 libxcb-shape0 libxcb-xinerama0 \
        libdbus-1-3 libfontconfig1 libxrender1 libxi6 \
        libgeos-dev libproj-dev proj-data
elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y \
        mesa-libGL mesa-libEGL libxkbcommon libxkbcommon-x11 \
        xcb-util-cursor xcb-util-image xcb-util-keysyms xcb-util-renderutil xcb-util-wm \
        dbus-libs fontconfig libXrender libXi \
        geos-devel proj-devel proj-data
else
    echo "未识别的包管理器，请手动安装 Qt xcb 平台插件依赖与 GEOS/PROJ 开发库后重新运行本脚本。" >&2
    exit 1
fi

if command -v uv >/dev/null 2>&1; then
    uv sync
    echo "完成（使用 uv）。运行示例：uv run GridVarEditor.py --dir ../work"
else
    python3 -m venv .venv
    # shellcheck disable=SC1091
    source .venv/bin/activate
    pip install -r requirements.txt
    echo "完成（使用 venv + pip）。运行示例：source .venv/bin/activate && python GridVarEditor.py --dir ../work"
fi
