"""
CSBE - Component Shape Bulk Exporter
====================================

Blender addon that batch-exports EFMI VB0 position buffers for multiple
CustomProperty states on a control Empty.

For each enabled property/value pair the addon sets the property, updates the
dependency graph, and runs EFMI-Tools while writing only the selected VB0
buffers with a shape-identifying suffix.
"""

bl_info = {
    "name": "CSBE - Component Shape Bulk Exporter",
    "author": "Kazeyako",
    "version": (1, 3, 2),
    "blender": (5, 1, 0),
    "location": "View3D > Sidebar > CSBE",
    "description": (
        "Batch-exports VB0 Position buffers for CustomProperty variants on a "
        "Control Empty, using EFMI-Tools or XXMI-Tools as the backend."
    ),
    "warning": "Requires EFMI-Tools or XXMI-Tools to be installed and enabled.",
    "category": "Import-Export",
}

ADDON_REPO_URL = "https://github.com/Kazenochi/MI-CSBE"

from . import operators, properties, ui


def register() -> None:
    properties.register()
    operators.register()
    ui.register()


def unregister() -> None:
    ui.unregister()
    operators.unregister()
    properties.unregister()
