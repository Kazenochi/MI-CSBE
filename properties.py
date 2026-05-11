import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Object, PropertyGroup


# ---------------------------------------------------------------------------
# Backend registry
# ---------------------------------------------------------------------------
# To add support for a new game pipeline, append an entry here.
# The key ('EFMI', 'XXMI', …) is used throughout operators.py to dispatch
# to the correct export code.
#
# Format: (identifier, display_name, description)
BACKEND_ITEMS = [
    (
        "EFMI",
        "EFMI-Tools  (Metadata.json)",
        "New-game pipeline — reads Metadata.json and Component X.fmt from the "
        "object source folder.  Writes Component<id>_VB0_<prop>.buf",
    ),
    (
        "XXMI",
        "XXMI-Tools  (hash.json)",
        "Legacy-game pipeline — reads hash.json from the dump folder.  "
        "Writes <ComponentName>Position_VB0_<prop>.buf",
    ),
    # ── Add future pipelines here ──────────────────────────────────────
    # ("MYGAME", "MyGame-Tools  (config.json)", "Description …"),
]


# ---------------------------------------------------------------------------
# Property groups
# ---------------------------------------------------------------------------

class CSBEShapePropItem(PropertyGroup):
    """One custom property entry in the batch export list."""

    prop_name: StringProperty(name="Property Name", default="")
    enabled: BoolProperty(name="Export this property", default=True)
    values: StringProperty(
        name="Values",
        description=(
            "Comma-separated values to export for this property.\n"
            "Example: '0, 1'  or  '0, 0.5, 1'\n"
            "Single value → property name only in filename.\n"
            "Multiple values → value appended to filename."
        ),
        default="0, 1",
    )
    output_name: StringProperty(
        name="Output Name",
        description=(
            "Label used in the output filename instead of the property name.\n"
            "Example: 'MouthOpen' → Component0_VB0_MouthOpen.buf\n"
            "Leave empty to use the property name as-is."
        ),
        default="",
    )
    components: StringProperty(
        name="Components",
        description=(
            "Comma-separated component IDs to export for this property.\n"
            "Example: '0, 2' exports only Component0_VB0 and Component2_VB0.\n"
            "Leave empty to export all components."
        ),
        default="",
    )


class CSBESettings(PropertyGroup):
    """Scene-level settings for CSBE — Component Shape Bulk Exporter."""

    # ── Pipeline selector ────────────────────────────────────────────────
    backend: EnumProperty(
        name="Export Pipeline",
        description=(
            "Select the export pipeline that matches the game you are modding.\n"
            "Each pipeline expects a different folder structure and produces\n"
            "differently named output files."
        ),
        items=BACKEND_ITEMS,
        default="EFMI",
    )

    # ── Control object ───────────────────────────────────────────────────
    control_obj: PointerProperty(
        type=Object,
        name="Control Object",
        description=(
            "Object whose CustomProperties act as shape-variant switches "
            "(typically an Empty with driver targets)"
        ),
    )

    # ── Property list ────────────────────────────────────────────────────
    prop_list: CollectionProperty(type=CSBEShapePropItem, name="Property List")
    active_prop_index: IntProperty(name="Active Property Index", default=0, min=0)

    # ── Output ───────────────────────────────────────────────────────────
    output_dir: StringProperty(
        name="Output Directory",
        description=(
            "Folder where VB0 .buf files are written.\n"
            "Leave empty to fall back to the active tool's configured output path."
        ),
        default="",
        subtype="DIR_PATH",
    )

    restore_after_export: BoolProperty(
        name="Restore Properties After Export",
        description=(
            "Reset all modified CustomProperties to their original values "
            "when the export finishes (or fails)."
        ),
        default=True,
    )

    verbose_logging: BoolProperty(
        name="Verbose Console Logging",
        description=(
            "Print detailed per-object driver and temporary-copy diagnostics "
            "to the Blender system console"
        ),
        default=False,
    )

    show_about: BoolProperty(
        name="Show Credits and Update",
        description="Show addon credits, repository link, and update controls",
        default=False,
    )

    update_status: StringProperty(
        name="Update Status",
        description="Last GitHub patch/update status",
        default="",
    )


def register() -> None:
    bpy.utils.register_class(CSBEShapePropItem)
    bpy.utils.register_class(CSBESettings)
    bpy.types.Scene.csbe = PointerProperty(type=CSBESettings)


def unregister() -> None:
    del bpy.types.Scene.csbe
    bpy.utils.unregister_class(CSBESettings)
    bpy.utils.unregister_class(CSBEShapePropItem)
