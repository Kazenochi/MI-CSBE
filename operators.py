"""
CSBE — Component Shape Bulk Exporter  |  operators.py

Architecture
------------
Backend dispatch is driven by scene.csbe.backend (an EnumProperty the user
sets explicitly — see properties.py).  Adding a new game pipeline requires:

  1. Add an entry to BACKEND_ITEMS in properties.py.
  2. Add the module lookup to BACKEND_MODULE_MAP below.
  3. Add a scene-settings lookup to _get_tool_settings().
  4. Implement _<name>_export_one() following the EFMI / XXMI patterns.
  5. Add a dispatch case in CSBE_OT_ExportBatch.execute().

EFMI integration
----------------
* ModExporter(context, cfg, excluded_buffers)   — regular class, not dataclass
* export_mod() handles everything including Metadata.json loading and cleanup
* We subclass ModExporter dynamically and override write_files() so only
  Component<id>_VB0 buffers are written, with the shape suffix.
* A _EfmiCfgProxy wraps efmi_tools_settings with export-specific overrides
  (partial_export=True, write_ini=False, etc.) without touching real settings.

XXMI integration
----------------
* ModExporter is a dataclass; ~19 constructor parameters
* We call generate_buffers() directly (skipping write_files() entirely) and
  manually write only the Position / VB0 entries from files_to_write.
"""

import re
import sys
import math
import traceback
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import bpy
import numpy
from bpy.props import EnumProperty
from bpy.types import Context, Operator

from . import ADDON_REPO_URL, bl_info

# ---------------------------------------------------------------------------
# Backend module map
# ---------------------------------------------------------------------------
# Maps backend key → (sys.modules suffix to search, required attribute name)
# to locate the exporter module at runtime.
BACKEND_MODULE_MAP: dict[str, tuple[str, str]] = {
    "EFMI": ("blender_export.blender_export", "ModExporter"),
    "XXMI": ("migoto.exporter",               "ModExporter"),
    # "MYGAME": ("mygame_tools.exporter", "ModExporter"),
}

# EFMI buffer-type names to exclude when exporting VB0 only.
_EFMI_EXCLUDED_FOR_VB0: list[str] = [
    "Index",
    "Blend",
    "Vector",
    "Color",
    "TexCoord",
    "ShapeKeyOffset",
    "ShapeKeyVertexId",
    "ShapeKeyVertexOffset",
]

# Custom-property key prefixes/names that are internal to Blender or other
# addons and should not appear in the property scan.
_SKIP_EXACT    = {"_RNA_UI"}
_SKIP_PREFIXES = ("_", "cycles", "cycles_visibility")
_XXMI_PREFIX   = "3DMigoto:"
_GITHUB_ZIP_URLS = (
    f"{ADDON_REPO_URL}/archive/refs/heads/main.zip",
    f"{ADDON_REPO_URL}/archive/refs/heads/master.zip",
)


# ---------------------------------------------------------------------------
# Runtime helpers — module & settings lookup
# ---------------------------------------------------------------------------

def _find_module(suffix: str, required_attr: str):
    """Return the first sys.modules value whose key ends with *suffix* and
    that exposes *required_attr*, or None."""
    for key, mod in sys.modules.items():
        if key.endswith(suffix) and hasattr(mod, required_attr):
            return mod
    return None


def _get_exporter_module(backend: str):
    """Return the exporter module for *backend*, or None if not loaded."""
    entry = BACKEND_MODULE_MAP.get(backend)
    if entry is None:
        return None
    return _find_module(*entry)


def _get_tool_settings(backend: str):
    """Return the tool's scene PropertyGroup for *backend*, or None."""
    scene = bpy.context.scene
    if backend == "EFMI":
        return getattr(scene, "efmi_tools_settings", None)
    if backend == "XXMI":
        return getattr(scene, "xxmi", None)
    # ── Add new backends here ──────────────────────────────────────────
    return None


def check_backend(backend: str) -> tuple[bool, str]:
    """
    Check whether *backend* is fully available.

    Returns (ok, human-readable status string).
    """
    mod = _get_exporter_module(backend)
    if mod is None:
        names = {
            "EFMI": "EFMI-Tools",
            "XXMI": "XXMI-Tools",
        }
        return False, f"{names.get(backend, backend)} addon not found in session"

    cfg = _get_tool_settings(backend)
    if cfg is None:
        return False, f"Scene settings for {backend} not found (addon may need reload)"

    return True, "loaded"


# ---------------------------------------------------------------------------
# Common helpers
# ---------------------------------------------------------------------------

def get_user_custom_props(obj) -> dict:
    """Return {name: current_value} for user-defined numeric custom props."""
    props: dict = {}
    for key in obj.keys():
        if key in _SKIP_EXACT:
            continue
        if any(key.startswith(p) for p in _SKIP_PREFIXES):
            continue
        if key.startswith(_XXMI_PREFIX):
            continue
        val = obj[key]
        if isinstance(val, (bool, int, float)):
            props[key] = val
    return props


def _format_value(value: float) -> str:
    """Format a float for use in a filename (no '.' or '-')."""
    if value == int(value):
        return str(int(value))
    return str(value).replace(".", "p").replace("-", "m")


def _coerce_value(original, requested: float):
    """Cast *requested* to the same Python type as *original*."""
    if isinstance(original, bool):
        return bool(int(requested))
    if isinstance(original, int):
        return int(requested)
    return float(requested)


def _make_filename(buf_stem: str, label: str, value: float,
                   multi_value: bool) -> str:
    """Build the output filename for one VB0 export."""
    if multi_value:
        return f"{buf_stem}_VB0_{label}_{_format_value(value)}.buf"
    return f"{buf_stem}_VB0_{label}.buf"


def _parse_components(raw: str) -> set[int] | None:
    """Parse a comma-separated component ID string into a set of ints.
    Returns None when the string is empty (meaning: export all components)."""
    raw = raw.strip()
    if not raw:
        return None
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids if ids else None


def _resolve_output_dir(settings, backend: str, tool_cfg) -> Path | None:
    """Return the resolved output Path, or None if nothing is configured."""
    if settings.output_dir.strip():
        return Path(settings.output_dir.strip())
    if backend == "EFMI":
        raw = getattr(tool_cfg, "mod_output_folder", "")
    elif backend == "XXMI":
        raw = getattr(tool_cfg, "destination_path", "")
    else:
        raw = ""
    return Path(raw) if raw.strip() else None


# ---------------------------------------------------------------------------
# Pre-bake helpers — evaluated positions → base mesh
# ---------------------------------------------------------------------------

def _efmi_prebake_objects(context: Context, collection) -> list:
    """
    For every non-TEMP mesh in *collection*:
      1. Read vertex positions from the evaluated depsgraph (driven state).
      2. Write those positions back to the base-mesh vertex data.
      3. Mute every non-Basis shape key so it contributes zero when EFMI
         evaluates the temporary copies it creates.

    This guarantees that when EFMI's copy_object() duplicates the mesh RNA,
    the copy already contains the correct driven vertex positions in its base
    mesh, independent of whether shape-key driver evaluation runs correctly
    on the TEMP objects.

    Returns a list of backup dicts for restoration by _efmi_restore_objects().
    """
    # Ensure the depsgraph is fully up-to-date before reading evaluated state.
    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    backups: list = []

    for obj in collection.all_objects:
        if obj.type != 'MESH' or obj.data is None:
            continue
        if obj.name.startswith('TEMP_'):
            continue

        eval_obj = obj.evaluated_get(depsgraph)

        # If evaluated_get returns the same object the object is not in the
        # depsgraph (e.g. excluded from the view layer).  In that case we
        # cannot read its driven positions, so we skip it entirely — EFMI
        # will handle it with whatever RNA state it currently has.
        if eval_obj is obj:
            print(f"CSBE prebake: {obj.name} — NOT in depsgraph, skipped")
            continue

        eval_mesh = eval_obj.to_mesh()

        n_base = len(obj.data.vertices)
        n_eval = len(eval_mesh.vertices)

        if n_base != n_eval:
            eval_obj.to_mesh_clear()
            print(f"CSBE prebake: {obj.name} — vertex count mismatch "
                  f"({n_base} vs {n_eval}), skipped")
            continue

        if n_base == 0:
            eval_obj.to_mesh_clear()
            continue

        # Backup original base coords
        orig_coords = numpy.empty(n_base * 3, dtype=numpy.float32)
        obj.data.vertices.foreach_get('co', orig_coords)

        # Copy evaluated coords to base mesh
        eval_coords = numpy.empty(n_eval * 3, dtype=numpy.float32)
        eval_mesh.vertices.foreach_get('co', eval_coords)

        # Diagnostic: shape key values help reveal whether the property drives them
        if obj.data.shape_keys:
            active_sks = [(kb.name, f"{kb.value:.4f}")
                          for kb in obj.data.shape_keys.key_blocks[1:]
                          if kb.value != 0.0]
            if active_sks:
                print(f"CSBE prebake: {obj.name} — active shape keys: "
                      + ", ".join(f"{n}={v}" for n, v in active_sks))

        eval_obj.to_mesh_clear()

        # Diagnostic: show first vertex position so we can verify it changes
        # between export passes.
        print(f"CSBE prebake: {obj.name} — vert0 eval=("
              f"{eval_coords[0]:.5f},{eval_coords[1]:.5f},{eval_coords[2]:.5f})"
              f" base=({orig_coords[0]:.5f},{orig_coords[1]:.5f},{orig_coords[2]:.5f})")

        obj.data.vertices.foreach_set('co', eval_coords)
        obj.data.update()

        # Mute non-Basis shape keys so they don't re-apply during EFMI eval
        muted_now: list = []
        if obj.data.shape_keys:
            for i, kb in enumerate(obj.data.shape_keys.key_blocks):
                if i == 0:           # Basis — always skip
                    continue
                if not kb.mute:
                    kb.mute = True
                    muted_now.append(kb)

        backups.append({
            'obj':        obj,
            'orig_coords': orig_coords,
            'muted_now':  muted_now,
        })

    return backups


def _efmi_restore_objects(backups: list) -> None:
    """Undo every change made by _efmi_prebake_objects()."""
    for b in backups:
        obj = b['obj']
        obj.data.vertices.foreach_set('co', b['orig_coords'])
        obj.data.update()
        for kb in b['muted_now']:
            kb.mute = False
    if backups:
        print(f"CSBE restore: {len(backups)} object(s) restored")


def _find_efmi_object_merger_module():
    """Return EFMI's object_merger module once EFMI-Tools is loaded."""
    return _find_module("blender_export.object_merger", "ObjectMerger")


def _shape_key_driver_values(obj, frame: float) -> dict[str, float]:
    """Resolve source shape-key value drivers without relying on temp objects."""
    shape_keys = getattr(obj.data, "shape_keys", None)
    anim_data = getattr(shape_keys, "animation_data", None)
    if shape_keys is None or anim_data is None:
        return {}

    values: dict[str, float] = {}
    key_name_pattern = re.compile(r'key_blocks\["(.+)"\]\.value')

    safe_names = {
        "abs": abs,
        "bool": bool,
        "clamp": lambda x, a=0.0, b=1.0: max(a, min(b, x)),
        "float": float,
        "frame": frame,
        "int": int,
        "max": max,
        "min": min,
        "pow": pow,
        "round": round,
    }
    for name in dir(math):
        if not name.startswith("_"):
            safe_names[name] = getattr(math, name)
    safe_names.update(getattr(bpy.app, "driver_namespace", {}))

    for fcurve in anim_data.drivers:
        match = key_name_pattern.fullmatch(fcurve.data_path)
        if not match:
            continue
        key_name = match.group(1)
        driver = fcurve.driver
        namespace = dict(safe_names)

        for var in driver.variables:
            value = 0.0
            if var.type == "SINGLE_PROP" and var.targets:
                target = var.targets[0]
                if target.id and target.data_path:
                    try:
                        array_match = re.fullmatch(
                            r'\["(.+)"\]\[(\d+)\]', target.data_path
                        )
                        if array_match:
                            prop_name, index = array_match.groups()
                            value = target.id[prop_name][int(index)]
                        else:
                            value = target.id.path_resolve(target.data_path)
                    except Exception as exc:
                        print(
                            f"CSBE driver: failed to resolve {target.id.name}."
                            f"{target.data_path}: {exc}"
                        )
            else:
                try:
                    value = fcurve.evaluate(frame)
                except Exception:
                    value = 0.0
            namespace[var.name] = value

        try:
            expression = driver.expression.strip() or "0"
            values[key_name] = float(eval(expression, {"__builtins__": {}}, namespace))
        except Exception as exc:
            try:
                values[key_name] = float(fcurve.evaluate(frame))
            except Exception:
                print(
                    f"CSBE driver: failed to evaluate {obj.name} shape key "
                    f"{key_name}: {exc}"
                )

    return values


def _freeze_shape_key_drivers_on_copy(source_obj, copied_obj, frame: float) -> None:
    """Copy current source driver results into the temp object's key values."""
    driven_values = _shape_key_driver_values(source_obj, frame)
    shape_keys = getattr(copied_obj.data, "shape_keys", None)
    if shape_keys is None:
        return

    active = []
    for key_block in shape_keys.key_blocks:
        if key_block.name in driven_values:
            key_block.value = driven_values[key_block.name]
        if key_block.name != "Basis" and key_block.value != 0.0:
            active.append(f"{key_block.name}={key_block.value:.4f}")

    if shape_keys.animation_data:
        shape_keys.animation_data_clear()

    if active:
        print(f"CSBE frozen shape keys: {source_obj.name}: " + ", ".join(active))


def _make_efmi_evaluated_copy_object(context: Context, original_copy_object):
    """
    Build a replacement for EFMI's object_merger.copy_object.

    EFMI normally copies the original mesh datablock, then applies modifiers on
    the temp object. Driver-controlled shape-key values can fail to resolve on
    those temp copies. This replacement copies the mesh with shape keys intact,
    resolves source driver values from the current control-property state, and
    freezes those values on the temp copy before EFMI converts it.
    """

    def _copy_object_evaluated(copy_context, obj, name=None, collection=None):
        if obj.type != 'MESH':
            return original_copy_object(copy_context, obj, name=name, collection=collection)

        context.view_layer.update()
        new_obj = obj.copy()
        new_obj.data = obj.data.copy()
        new_obj.data.name = f"{obj.data.name}_CSBE_Copy"
        new_obj.animation_data_clear()
        _freeze_shape_key_drivers_on_copy(obj, new_obj, context.scene.frame_current)
        if name:
            new_obj.name = name
        if collection:
            collection.objects.link(new_obj)
        new_obj.select_set(False)

        print(
            f"CSBE evaluated copy: {obj.name} -> {new_obj.name} "
            f"({len(new_obj.data.vertices)} vertices)"
        )
        return new_obj

    return _copy_object_evaluated


# ---------------------------------------------------------------------------
# EFMI backend
# ---------------------------------------------------------------------------

class _EfmiCfgProxy:
    """
    Wraps efmi_tools_settings with export-specific overrides, without
    mutating the real Blender PropertyGroup.  Supports both __getattr__
    and __setattr__ so that export_mod() can safely write to cfg.
    """

    def __init__(self, real_cfg, output_dir: Path, **overrides):
        object.__setattr__(self, "_real", real_cfg)
        object.__setattr__(self, "_ov", {
            # Core overrides for a clean VB0-only export pass
            "partial_export": True,
            "write_ini": False,
            "copy_textures": False,
            "allow_export_without_lods": True,
            "remove_temp_object": True,
            "custom_template_live_update": False,
            # Route ModExporter's internal mkdir calls to output_dir
            # (our write_files() override ignores meshes_path anyway)
            "mod_output_folder": str(output_dir),
            # Force baking of all modifiers and shape keys on temp objects.
            # EFMI copies temp objects retaining their drivers, so
            # bpy.ops.object.convert() evaluates those drivers against the
            # current scene state (after frame_set) and bakes the correct
            # driven positions into the mesh before the join step.
            "apply_all_modifiers": True,
            **overrides,
        })

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        ov = object.__getattribute__(self, "_ov")
        if name in ov:
            return ov[name]
        return getattr(object.__getattribute__(self, "_real"), name)

    def __setattr__(self, name: str, value):
        object.__getattribute__(self, "_ov")[name] = value


def _make_efmi_exporter_cls(ModExporter, output_dir: Path,
                             label: str, value: float,
                             multi_value: bool,
                             allowed_components: set[int] | None):
    """
    Dynamically subclass EFMI's ModExporter (found at runtime) and override
    write_files() to write only Component<id>_VB0 buffers with the shape
    suffix, directly into *output_dir*.

    allowed_components: set of component IDs to include, or None for all.
    label: string used in the output filename (custom name or property name).
    """

    class _CSBEExporter(ModExporter):
        _csbe_written: int = 0

        def build_merged_object(self_inner, component_id=-1):  # noqa: N805
            if allowed_components is not None and component_id not in allowed_components:
                exporter_module = sys.modules[ModExporter.__module__]
                return exporter_module.MergedObject(
                    object=None,
                    components=[],
                    shapekeys=exporter_module.MergedObjectShapeKeys(),
                    skeleton_type=self_inner.skeleton_type,
                )
            return super().build_merged_object(component_id)

        def write_files(self_inner):  # noqa: N805
            self_inner._csbe_written = 0
            for buf_name, buf_data in self_inner.buffers.items():
                m = re.match(r"Component(\d+)_VB0$", buf_name)
                if not m:
                    continue
                comp_id = int(m.group(1))
                if allowed_components is not None and comp_id not in allowed_components:
                    continue
                stem = f"Component{comp_id}"
                filename = _make_filename(stem, label, value, multi_value)
                out_path = output_dir / filename
                with open(out_path, "wb") as f:
                    f.write(buf_data.get_bytes())
                print(f"CSBE: wrote {out_path.name}")
                self_inner._csbe_written += 1

    return _CSBEExporter


def _efmi_export_one(context: Context, tool_cfg, output_dir: Path,
                     prop_name: str, value: float, multi_value: bool,
                     ModExporter, label: str,
                     allowed_components: set[int] | None) -> int:
    """Run one EFMI export pass; return number of VB0 files written.

    During this pass EFMI's temp-object duplication is redirected through an
    evaluated mesh copy so driver-controlled shape keys are already baked into
    the temporary mesh before EFMI merges and serializes it.
    """
    merger_mod = _find_efmi_object_merger_module()
    if merger_mod is None:
        raise RuntimeError("EFMI object_merger module not found in session.")

    original_copy_object = merger_mod.copy_object
    merger_mod.copy_object = _make_efmi_evaluated_copy_object(
        context, original_copy_object
    )
    original_active = context.view_layer.objects.active
    original_selected = list(context.selected_objects)

    try:
        for obj in original_selected:
            if obj.name in bpy.data.objects:
                obj.select_set(False)
        context.view_layer.objects.active = None

        proxy = _EfmiCfgProxy(tool_cfg, output_dir)
        ExporterCls = _make_efmi_exporter_cls(
            ModExporter, output_dir, label, value, multi_value, allowed_components
        )
        exporter = ExporterCls(context, proxy, _EFMI_EXCLUDED_FOR_VB0)
        exporter.export_mod()
    finally:
        merger_mod.copy_object = original_copy_object
        for obj in list(context.selected_objects):
            if obj.name in bpy.data.objects:
                obj.select_set(False)
        for obj in original_selected:
            if obj.name in bpy.data.objects:
                obj.select_set(True)
        if original_active and original_active.name in bpy.data.objects:
            context.view_layer.objects.active = original_active
        context.view_layer.update()

    return getattr(exporter, '_csbe_written', 0)


# ---------------------------------------------------------------------------
# XXMI backend
# ---------------------------------------------------------------------------

def _is_xxmi_vb0(path: Path) -> bool:
    """True for XXMI VB0/Position .buf files (not Blend / Texcoord)."""
    if path.suffix != ".buf":
        return False
    stem = path.stem
    return not (stem.endswith("Blend") or stem.endswith("Texcoord"))


def _xxmi_export_one(context: Context, operator: Operator,
                     tool_cfg, output_dir: Path,
                     prop_name: str, value: float, multi_value: bool,
                     ModExporter, label: str,
                     allowed_components: set[int] | None) -> int:
    """Run one XXMI export pass; return number of VB0 files written."""
    ds_mod = _find_module("migoto.datastructures", "GameEnum")
    if ds_mod is None:
        raise RuntimeError("migoto.datastructures.GameEnum not found in session.")
    GameEnum = ds_mod.GameEnum

    mod_exporter = ModExporter(
        context=context,
        operator=operator,
        dump_path=Path(tool_cfg.dump_path),
        destination=output_dir,
        game=GameEnum[tool_cfg.game],
        ignore_hidden=tool_cfg.ignore_hidden,
        only_selected=tool_cfg.only_selected,
        no_ramps=tool_cfg.no_ramps,
        copy_textures=False,
        ignore_duplicate_textures=False,
        credit="",
        outline_optimization=tool_cfg.outline_optimization,
        apply_modifiers=tool_cfg.apply_modifiers_and_shapekeys,
        normalize_weights=tool_cfg.normalize_weights,
        write_buffers=True,
        write_ini=False,
    )
    mod_exporter.generate_buffers()

    written = 0
    for path, data in mod_exporter.files_to_write.items():
        if not isinstance(data, numpy.ndarray):
            continue
        if not _is_xxmi_vb0(path):
            continue
        # Filter by component ID if specified
        if allowed_components is not None:
            m = re.match(r"Component(\d+)", path.stem)
            if m and int(m.group(1)) not in allowed_components:
                continue
        filename = _make_filename(path.stem, label, value, multi_value)
        out_path = output_dir / filename
        data.tofile(out_path)
        print(f"CSBE: wrote {out_path.name}")
        written += 1

    mod_exporter.cleanup()
    return written


# ── Add new backend export functions here ─────────────────────────────────
# def _mygame_export_one(context, tool_cfg, output_dir, prop_name, value,
#                        multi_value, ModExporter) -> int: ...


def _dbg_face_shapekeys(context, col, tag: str) -> None:
    """Print shape key .value (RNA, driver-evaluated) for Component 2 objects."""
    if col is None:
        return
    import re as _re
    for obj in col.all_objects:
        if obj.type != 'MESH' or obj.data is None:
            continue
        m = _re.search(r'component[_ -]*(\d+)', obj.name, _re.IGNORECASE)
        if not m or int(m.group(1)) != 2:
            continue
        if not obj.data.shape_keys:
            continue
        active = [(kb.name, kb.value)
                  for kb in obj.data.shape_keys.key_blocks[1:]
                  if kb.value != 0.0]
        if active:
            print(f"CSBE dbg [{tag}] {obj.name} SK: "
                  + ", ".join(f"{n}={v:.4f}" for n, v in active))
        else:
            print(f"CSBE dbg [{tag}] {obj.name} — all SK values = 0")


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class CSBE_OT_ScanProperties(Operator):
    """Scan the control object and populate the property list."""

    bl_idname = "csbe.scan_properties"
    bl_label = "Scan Properties"
    bl_description = (
        "Read all user-defined numeric CustomProperties from the selected "
        "control object and populate the export list"
    )
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context: Context):
        settings = context.scene.csbe
        obj = settings.control_obj
        if obj is None:
            self.report({"ERROR"}, "No control object selected.")
            return {"CANCELLED"}

        user_props = get_user_custom_props(obj)
        if not user_props:
            self.report(
                {"WARNING"},
                f"No user-defined numeric CustomProperties found on '{obj.name}'.",
            )
            return {"CANCELLED"}

        # Preserve user edits for properties that are already in the list.
        existing: dict[str, tuple[bool, str]] = {
            item.prop_name: (item.enabled, item.values)
            for item in settings.prop_list
        }

        settings.prop_list.clear()
        for name in sorted(user_props.keys()):
            item = settings.prop_list.add()
            item.prop_name = name
            if name in existing:
                item.enabled, item.values = existing[name]
            else:
                item.enabled = True
                item.values = "0, 1"

        settings.active_prop_index = 0
        n = len(user_props)
        self.report(
            {"INFO"},
            f"Found {n} propert{'y' if n == 1 else 'ies'} on '{obj.name}'.",
        )
        return {"FINISHED"}


class CSBE_OT_ExportBatch(Operator):
    """Batch-export VB0 Position buffers for all enabled property/value combos."""

    bl_idname = "csbe.export"
    bl_label = "Export VB0 Batch"
    bl_description = (
        "For each enabled property and each listed value: set the property, "
        "re-evaluate the scene, and export the VB0 Position buffer with a "
        "_VB0_<PropertyName> suffix using the selected pipeline"
    )
    bl_options = {"REGISTER"}

    def execute(self, context: Context):
        settings  = context.scene.csbe
        backend   = settings.backend

        # ── Backend validation ────────────────────────────────────────────
        ok, msg = check_backend(backend)
        if not ok:
            self.report({"ERROR"}, f"Pipeline '{backend}' not available: {msg}")
            return {"CANCELLED"}

        exporter_mod = _get_exporter_module(backend)
        tool_cfg     = _get_tool_settings(backend)
        ModExporter  = exporter_mod.ModExporter

        # Pipeline-specific preflight checks
        if backend == "XXMI":
            if not getattr(tool_cfg, "game", ""):
                self.report({"ERROR"}, "Please select a game in XXMI Tools.")
                return {"CANCELLED"}
            if not getattr(tool_cfg, "dump_path", ""):
                self.report({"ERROR"}, "XXMI dump path is not set.")
                return {"CANCELLED"}

        # ── Common validation ─────────────────────────────────────────────
        control_obj = settings.control_obj
        if control_obj is None:
            self.report({"ERROR"}, "No control object selected.")
            return {"CANCELLED"}

        enabled_items = [
            item for item in settings.prop_list
            if item.enabled and item.prop_name and item.values.strip()
        ]
        if not enabled_items:
            self.report({"ERROR"}, "No properties enabled for export.")
            return {"CANCELLED"}

        output_dir = _resolve_output_dir(settings, backend, tool_cfg)
        if not output_dir:
            self.report(
                {"ERROR"},
                "No output directory configured. Fill in 'Output Directory' "
                "or set the output path in the active pipeline's settings.",
            )
            return {"CANCELLED"}

        if backend == "XXMI":
            dump = Path(getattr(tool_cfg, "dump_path", ""))
            if output_dir.resolve() == dump.resolve():
                self.report({"ERROR"},
                            "Output directory must differ from the XXMI dump path.")
                return {"CANCELLED"}

        output_dir.mkdir(parents=True, exist_ok=True)

        # ── Save original property values ─────────────────────────────────
        originals: dict[str, object] = {
            item.prop_name: control_obj.get(item.prop_name)
            for item in enabled_items
        }

        exported_total = 0
        errors: list[str] = []

        try:
            for prop_item in enabled_items:
                prop_name = prop_item.prop_name

                try:
                    raw_values = [
                        float(v.strip())
                        for v in prop_item.values.split(",")
                        if v.strip()
                    ]
                except ValueError:
                    msg = (
                        f"Property '{prop_name}': cannot parse values "
                        f"'{prop_item.values}' — expected comma-separated numbers."
                    )
                    errors.append(msg)
                    self.report({"WARNING"}, msg)
                    continue

                if not raw_values:
                    continue

                multi_value = len(raw_values) > 1
                orig = originals[prop_name]
                label = prop_item.output_name.strip() or prop_name
                allowed_components = _parse_components(prop_item.components)

                # Zero out all other enabled properties so each export file
                # represents exactly one isolated property change.
                for other in enabled_items:
                    if other.prop_name != prop_name:
                        control_obj[other.prop_name] = _coerce_value(
                            originals[other.prop_name], 0
                        )

                for value in raw_values:
                    log_label = f"{prop_name}={value}"
                    try:
                        # Set property, then trigger TWO evaluation passes:
                        # 1. frame_set  — re-runs the dependency graph for the
                        #    current frame so keyframed drivers see the new value.
                        # 2. redraw_timer DRAW_WIN_SWAP — forces a real viewport
                        #    redraw cycle.  Blender 5.x blocks Python-scripted
                        #    drivers from executing inside an operator's depsgraph
                        #    pass (anim_sys.cc:4138 "unreachable" bug).  The
                        #    redraw runs *outside* that lock so scripted drivers
                        #    can evaluate correctly, updating kb.value in RNA.
                        control_obj[prop_name] = _coerce_value(orig, value)
                        context.scene.frame_set(context.scene.frame_current)
                        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)

                        # Diagnostic: print shape key values on Component 2
                        # face objects right after property set + frame_set,
                        # to verify drivers are reacting.
                        _dbg_face_shapekeys(context, col=getattr(
                            _get_tool_settings(backend), 'component_collection', None
                        ), tag=f"{prop_name}={value}")

                        # Dispatch to selected pipeline
                        if backend == "EFMI":
                            n = _efmi_export_one(
                                context, tool_cfg, output_dir,
                                prop_name, value, multi_value, ModExporter,
                                label, allowed_components,
                            )
                        elif backend == "XXMI":
                            n = _xxmi_export_one(
                                context, self, tool_cfg, output_dir,
                                prop_name, value, multi_value, ModExporter,
                                label, allowed_components,
                            )
                        # ── Add new pipelines here ─────────────────────
                        # elif backend == "MYGAME":
                        #     n = _mygame_export_one(...)
                        else:
                            raise RuntimeError(
                                f"No export implementation for backend '{backend}'."
                            )

                        exported_total += n

                    except Exception as exc:
                        msg = f"Error exporting {log_label}: {exc}"
                        errors.append(msg)
                        print(f"CSBE ERROR: {msg}")
                        traceback.print_exc()

        finally:
            # Restore original property values
            if settings.restore_after_export:
                for prop_name, orig_val in originals.items():
                    if orig_val is not None:
                        control_obj[prop_name] = orig_val
                context.view_layer.update()

        if errors:
            self.report(
                {"WARNING"},
                f"Export finished with {len(errors)} error(s) "
                f"({exported_total} file(s) written). Check the system console.",
            )
        else:
            self.report(
                {"INFO"},
                f"CSBE [{backend}]: {exported_total} file(s) written to '{output_dir}'.",
            )
        return {"FINISHED"}


class CSBE_OT_RemoveProp(Operator):
    bl_idname    = "csbe.remove_prop"
    bl_label     = "Remove Property"
    bl_description = "Remove the selected property from the export list"
    bl_options   = {"REGISTER", "UNDO"}

    def execute(self, context: Context):
        settings = context.scene.csbe
        idx = settings.active_prop_index
        if 0 <= idx < len(settings.prop_list):
            settings.prop_list.remove(idx)
            settings.active_prop_index = max(0, idx - 1)
        return {"FINISHED"}


class CSBE_OT_MoveProp(Operator):
    bl_idname    = "csbe.move_prop"
    bl_label     = "Move Property"
    bl_description = "Move the selected property up or down"
    bl_options   = {"REGISTER", "UNDO"}

    direction: EnumProperty(
        items=[("UP", "Up", ""), ("DOWN", "Down", "")],
        name="Direction",
        default="UP",
    )

    def execute(self, context: Context):
        settings = context.scene.csbe
        idx = settings.active_prop_index
        n   = len(settings.prop_list)
        if self.direction == "UP" and idx > 0:
            settings.prop_list.move(idx, idx - 1)
            settings.active_prop_index = idx - 1
        elif self.direction == "DOWN" and idx < n - 1:
            settings.prop_list.move(idx, idx + 1)
            settings.active_prop_index = idx + 1
        return {"FINISHED"}


class CSBE_OT_SelectAll(Operator):
    bl_idname    = "csbe.select_all"
    bl_label     = "Toggle Selection"
    bl_description = "Enable, disable, or invert all properties in the list"
    bl_options   = {"REGISTER", "UNDO"}

    action: EnumProperty(
        items=[
            ("ENABLE",  "Enable All",  ""),
            ("DISABLE", "Disable All", ""),
            ("INVERT",  "Invert",      ""),
        ],
        name="Action",
        default="INVERT",
    )

    def execute(self, context: Context):
        for item in context.scene.csbe.prop_list:
            if self.action == "ENABLE":
                item.enabled = True
            elif self.action == "DISABLE":
                item.enabled = False
            else:
                item.enabled = not item.enabled
        return {"FINISHED"}


class CSBE_OT_OpenRepo(Operator):
    bl_idname = "csbe.open_repo"
    bl_label = "Open GitHub Repository"
    bl_description = "Open the CSBE GitHub repository in your browser"

    def execute(self, context: Context):
        bpy.ops.wm.url_open(url=ADDON_REPO_URL)
        return {"FINISHED"}


def _copy_tree_files(src_dir: Path, dst_dir: Path) -> int:
    """Copy addon source files from src_dir into dst_dir."""
    copied = 0
    allowed_suffixes = {".py", ".md", ".txt"}
    for src in src_dir.rglob("*"):
        if not src.is_file():
            continue
        if "__pycache__" in src.parts:
            continue
        if src.suffix.lower() not in allowed_suffixes:
            continue

        rel = src.relative_to(src_dir)
        dst = dst_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    return copied


def _find_addon_source(root: Path) -> Path:
    """Find this addon package inside an extracted GitHub archive."""
    candidates = []
    for path in root.rglob("__init__.py"):
        package_dir = path.parent
        operators = package_dir / "operators.py"
        properties = package_dir / "properties.py"
        ui = package_dir / "ui.py"
        if operators.is_file() and properties.is_file() and ui.is_file():
            candidates.append(package_dir)

    for candidate in candidates:
        if candidate.name == Path(__file__).parent.name:
            return candidate
    if candidates:
        return candidates[0]
    raise RuntimeError("Downloaded archive does not contain a CSBE addon package.")


class CSBE_OT_PatchFromGithub(Operator):
    bl_idname = "csbe.patch_from_github"
    bl_label = "Patch Addon from GitHub"
    bl_description = (
        "Download the latest repository zip and replace this addon's source "
        "files. Restart or reload Blender after patching."
    )

    def execute(self, context: Context):
        settings = context.scene.csbe
        addon_dir = Path(__file__).resolve().parent

        try:
            with tempfile.TemporaryDirectory(prefix="csbe_update_") as tmp:
                tmp_dir = Path(tmp)
                zip_path = tmp_dir / "MI-CSBE.zip"
                last_error = None

                for url in _GITHUB_ZIP_URLS:
                    try:
                        urllib.request.urlretrieve(url, zip_path)
                        last_error = None
                        break
                    except Exception as exc:
                        last_error = exc
                if last_error is not None:
                    raise RuntimeError(f"Download failed: {last_error}")

                extract_dir = tmp_dir / "extract"
                with zipfile.ZipFile(zip_path, "r") as archive:
                    archive.extractall(extract_dir)

                source_dir = _find_addon_source(extract_dir)
                copied = _copy_tree_files(source_dir, addon_dir)
                if copied == 0:
                    raise RuntimeError("No addon source files were copied.")

            version = ".".join(str(v) for v in bl_info.get("version", ()))
            settings.update_status = (
                f"Patched {copied} file(s) from GitHub. Restart/reload Blender. "
                f"Previous running version: {version}"
            )
            self.report({"INFO"}, settings.update_status)
            return {"FINISHED"}

        except Exception as exc:
            settings.update_status = f"Patch failed: {exc}"
            self.report({"ERROR"}, settings.update_status)
            traceback.print_exc()
            return {"CANCELLED"}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_CLASSES = (
    CSBE_OT_ScanProperties,
    CSBE_OT_ExportBatch,
    CSBE_OT_RemoveProp,
    CSBE_OT_MoveProp,
    CSBE_OT_SelectAll,
    CSBE_OT_OpenRepo,
    CSBE_OT_PatchFromGithub,
)


def register() -> None:
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
