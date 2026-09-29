"""Auto-discovered widget extensions.

Any Python module dropped in this directory (or a symlink to one) with
`register_widget(...)` or `register_preset(...)` calls at import time
will show up in the + Widget menu automatically. No edits to
main_window.py required.

See `bl_gui.widget_registry` for the API and examples.
"""
