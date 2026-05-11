import bpy
from bpy.types import Panel, UIList

from . import ADDON_REPO_URL, bl_info
from .operators import check_backend, get_efmi_compatibility_note


class CSBE_UL_PropList(UIList):
    """One row per CustomProperty in the batch list."""

    bl_idname = "CSBE_UL_PropList"

    def draw_item(self, context, layout, data, item, icon,
                  active_data, active_propname, index=0, flt_flag=0):
        if self.layout_type in {"DEFAULT", "COMPACT"}:
            row = layout.row(align=True)
            row.prop(item, "enabled", text="")
            row.label(text=item.prop_name, icon="DRIVER")
            sub = row.row(align=True)
            sub.scale_x = 0.9
            sub.prop(item, "values", text="")
        elif self.layout_type == "GRID":
            layout.alignment = "CENTER"
            layout.label(text=item.prop_name)

    def filter_items(self, context, data, propname):
        items = getattr(data, propname)
        return ([self.bitflag_filter_item] * len(items), list(range(len(items))))


class VIEW3D_PT_CSBEExport(Panel):
    """CSBE panel — placed in the EFMI Tools N-panel tab."""

    bl_space_type  = "VIEW_3D"
    bl_region_type = "UI"
    bl_category    = "CSBE"
    bl_label       = "Component Shape Bulk Export"
    bl_options     = {"DEFAULT_CLOSED"}

    def draw_header(self, context):
        self.layout.label(text="", icon="EXPORT")

    def draw(self, context):
        layout   = self.layout
        settings = context.scene.csbe

        # ── Pipeline selector ─────────────────────────────────────────────
        box = layout.box()
        col = box.column(align=True)
        col.label(text="Export Pipeline", icon="SCENE")
        col.prop(settings, "backend", text="")

        # Backend availability status for the selected pipeline
        ok, status_msg = check_backend(settings.backend)
        status_row = col.row()
        if ok:
            status_row.label(text=f"Status: {status_msg}", icon="CHECKMARK")
        else:
            status_row.alert = True
            status_row.label(text=status_msg, icon="ERROR")

        if settings.backend == "EFMI" and ok:
            cfg = getattr(context.scene, "efmi_tools_settings", None)
            level, note = get_efmi_compatibility_note(cfg)
            note_row = col.row()
            if level == "ERROR":
                note_row.alert = True
                note_row.label(text=note, icon="ERROR")
            else:
                note_row.label(text=note, icon="INFO")

        layout.separator(factor=0.5)

        # ── Control Object ────────────────────────────────────────────────
        box = layout.box()
        col = box.column(align=True)
        col.label(text="Control Object", icon="EMPTY_AXIS")
        row = col.row(align=True)
        row.prop(settings, "control_obj", text="")
        row.operator("csbe.scan_properties", text="", icon="FILE_REFRESH")

        if settings.control_obj is not None and not settings.prop_list:
            col.label(text="Press  ⟳  to scan properties.", icon="INFO")

        # ── Property List ─────────────────────────────────────────────────
        box = layout.box()
        col = box.column(align=True)
        col.label(text="Properties to Export", icon="PROPERTIES")

        if not settings.prop_list:
            col.label(text="No properties loaded yet.", icon="INFO")
        else:
            # Informal column headers
            header = col.row(align=False)
            header.scale_y = 0.65
            header.label(text="  On   Name")
            header.label(text="Values (comma-separated)")

            col.template_list(
                "CSBE_UL_PropList", "",
                settings, "prop_list",
                settings, "active_prop_index",
                rows=4, maxrows=8,
            )

            # List action row
            row = col.row(align=True)
            row.operator("csbe.remove_prop",  text="", icon="REMOVE")
            op = row.operator("csbe.move_prop", text="", icon="TRIA_UP")
            op.direction = "UP"
            op = row.operator("csbe.move_prop", text="", icon="TRIA_DOWN")
            op.direction = "DOWN"
            row.separator()
            op = row.operator("csbe.select_all", text="", icon="CHECKBOX_HLT")
            op.action = "ENABLE"
            op = row.operator("csbe.select_all", text="", icon="CHECKBOX_DEHLT")
            op.action = "DISABLE"
            op = row.operator("csbe.select_all", text="", icon="ARROW_LEFTRIGHT")
            op.action = "INVERT"

            # Active-item detail editor
            if 0 <= settings.active_prop_index < len(settings.prop_list):
                active = settings.prop_list[settings.active_prop_index]
                detail = col.box().column(align=True)
                detail.label(text=f"Selected: {active.prop_name}", icon="DRIVER")
                detail.prop(active, "values", text="Values")
                detail.label(text="e.g.  '0, 1'  or  '0, 0.5, 1'", icon="INFO")
                detail.separator(factor=0.5)
                detail.prop(active, "output_name", text="Output Name")
                if not active.output_name.strip():
                    detail.label(text=f"→ using property name: {active.prop_name}",
                                 icon="INFO")
                detail.separator(factor=0.5)
                detail.prop(active, "components", text="Components")
                if not active.components.strip():
                    detail.label(text="→ exporting all components", icon="INFO")
                else:
                    detail.label(text=f"→ only: {active.components.strip()}",
                                 icon="CHECKMARK")

        # ── Output ────────────────────────────────────────────────────────
        box = layout.box()
        col = box.column(align=True)
        col.label(text="Output", icon="FILE_FOLDER")
        col.prop(settings, "output_dir", text="")

        # Fallback path preview — backend-aware
        if not settings.output_dir.strip():
            if settings.backend == "EFMI":
                cfg = getattr(context.scene, "efmi_tools_settings", None)
                fallback = getattr(cfg, "mod_output_folder", "") if cfg else ""
            elif settings.backend == "XXMI":
                cfg = getattr(context.scene, "xxmi", None)
                fallback = getattr(cfg, "destination_path", "") if cfg else ""
            else:
                fallback = ""
            if fallback.strip():
                col.label(text=f"→ {fallback}", icon="INFO")
            else:
                col.label(text="(no fallback path set in active tool)", icon="ERROR")

        col.separator()
        col.prop(settings, "restore_after_export")
        col.prop(settings, "verbose_logging")

        # Naming convention hint — backend-aware
        hint = box.box().column(align=True)
        hint.scale_y = 0.7
        hint.label(text="Output filenames:", icon="BLANK1")
        if settings.backend == "EFMI":
            hint.label(text="  Single value → Component0_VB0_<Name>.buf")
            hint.label(text="  Multi  value → Component0_VB0_<Name>_0.buf")
        else:
            hint.label(text="  Single value → …Position_VB0_<Name>.buf")
            hint.label(text="  Multi  value → …Position_VB0_<Name>_0.buf")
        hint.label(text="  <Name> = Output Name if set, else property name", icon="BLANK1")

        # ── Export button ─────────────────────────────────────────────────
        layout.separator()
        any_enabled = any(i.enabled for i in settings.prop_list)
        ready = (
            ok
            and settings.control_obj is not None
            and bool(settings.prop_list)
            and any_enabled
        )

        col = layout.column(align=True)
        col.enabled = ready
        col.scale_y = 1.6
        col.operator("csbe.export", text="Export VB0 Batch", icon="PLAY")

        if not ready:
            sub = layout.column(align=True)
            sub.scale_y = 0.75
            if not ok:
                sub.label(text=status_msg, icon="ERROR")
            elif settings.control_obj is None:
                sub.label(text="Select a control object first.", icon="ERROR")
            elif not settings.prop_list:
                sub.label(text="Scan the control object to load properties.",
                          icon="ERROR")
            else:
                sub.label(text="Enable at least one property.", icon="ERROR")

        layout.separator(factor=0.75)
        about_box = layout.box()
        header = about_box.row(align=True)
        icon = "TRIA_DOWN" if settings.show_about else "TRIA_RIGHT"
        header.prop(settings, "show_about", text="", icon=icon, emboss=False)
        header.label(text="Credits and Updates", icon="INFO")

        if settings.show_about:
            col = about_box.column(align=True)
            version = ".".join(str(v) for v in bl_info.get("version", ()))
            col.label(text=f"{bl_info.get('name', 'CSBE')} v{version}")
            col.label(text="Created by Kazeyako")
            col.label(text="Uses EFMI-Tools as the EFMI export backend")
            col.label(text="Keep base and variant exports on the same EFMI version")

            row = col.row(align=True)
            row.operator("csbe.open_repo", text="Open GitHub", icon="URL")
            row.operator("csbe.patch_from_github", text="Patch from GitHub",
                         icon="IMPORT")

            status = settings.update_status.strip()
            if status:
                col.separator(factor=0.5)
                col.label(text=status, icon="INFO")

            repo = col.column(align=True)
            repo.scale_y = 0.7
            repo.label(text=ADDON_REPO_URL, icon="BLANK1")


_CLASSES = (
    CSBE_UL_PropList,
    VIEW3D_PT_CSBEExport,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
