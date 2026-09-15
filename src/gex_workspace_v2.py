"""Fresh loader for the full E*TRADE GEX workspace.

This module has a new import name so a long-lived Streamlit worker cannot reuse
the old one-line GEX scaffold from sys.modules. On first import it explicitly
reloads src.gex_ui from the current file on disk and exports its renderer.
"""

from __future__ import annotations

import importlib

import src.gex_ui as _gex_ui

importlib.invalidate_caches()
_gex_ui = importlib.reload(_gex_ui)
render_gex = _gex_ui.render_gex
