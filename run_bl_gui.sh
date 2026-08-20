#!/bin/bash
# Activate the pystream conda env and launch the beamline GUI.
# Usage: ./run_bl_gui.sh [edit]
set -euo pipefail

# `conda activate` is a shell function defined by conda.sh. Even if
# CONDA_EXE is already in the environment, a fresh bash (this script)
# does NOT have the function — so we always source conda.sh before
# activating anything.

sourced=0
# Prefer the base of whichever conda is on PATH.
if command -v conda >/dev/null 2>&1; then
    base=$(conda info --base 2>/dev/null || true)
    if [ -n "$base" ] && [ -r "$base/etc/profile.d/conda.sh" ]; then
        # shellcheck disable=SC1090,SC1091
        source "$base/etc/profile.d/conda.sh"
        sourced=1
    fi
fi
if [ "$sourced" -eq 0 ]; then
    for p in \
        "$HOME/miniconda3/etc/profile.d/conda.sh" \
        "$HOME/conda/anaconda/etc/profile.d/conda.sh" \
        "/home/beams/AMITTONE/miniconda3/etc/profile.d/conda.sh" \
        "/home/beams/USERTXM/conda/anaconda/etc/profile.d/conda.sh" \
        "/APSshare/anaconda3/etc/profile.d/conda.sh" \
        "/opt/miniconda3/etc/profile.d/conda.sh"; do
        if [ -r "$p" ]; then
            # shellcheck disable=SC1090,SC1091
            source "$p"
            sourced=1
            break
        fi
    done
fi
if [ "$sourced" -eq 0 ]; then
    echo "ERROR: could not find conda.sh to source. Set CONDA_PREFIX or install conda." >&2
    exit 1
fi

conda activate pystream

# Args: ./run_bl_gui.sh [edit] [LAYOUT_NAME]
#   ./run_bl_gui.sh                 -> default layout, view mode
#   ./run_bl_gui.sh edit            -> default layout, edit mode
#   ./run_bl_gui.sh MyGui           -> load ~/.bl_gui/MyGui.json (create if missing), view
#   ./run_bl_gui.sh edit MyGui      -> same, edit mode
#   ./run_bl_gui.sh MyGui.json edit -> either order works
# 'edit' is a keyword; the other argument (if any) is treated as the layout name.
_edit=""
_layout=""
for a in "$@"; do
    if [ "$a" = "edit" ]; then
        _edit="edit"
    else
        _layout="$a"
    fi
done
if [ -z "$_layout" ]; then
    _layout="bl32id.json"
fi
exec bl_gui "$_layout" ${_edit:+edit}
