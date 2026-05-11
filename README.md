# MI-CSBE

MI-CSBE (Component Shape Bulk Exporter) is a Blender addon for batch-exporting component VB0 position buffers from control-object custom property states.

The intended workflow is:

1. Export the base state with EFMI-Tools.
2. Select the control Empty that drives shape keys through custom properties.
3. Scan the custom properties in CSBE.
4. Choose the property values and component IDs to export.
5. Export only the variant VB0 buffers needed for later blending.

## Status

This addon is currently tested only with the EFMI-Tools export backend. The UI still contains backend plumbing for other pipelines, but non-EFMI exports should be considered experimental until tested.

The addon is intended to stay consistent within one EFMI-Tools version cycle. Export the base buffers and all CSBE variant buffers with the same EFMI-Tools version. If EFMI changes its extracted metadata format or exporter internals, re-extract/re-import the model sources with that EFMI version before exporting variants.

CSBE performs a small EFMI compatibility check before export. For EFMI-Tools 0.4.3 and newer, it checks that the configured `Metadata.json` uses format version 3 or newer and stops early with a clear message if the sources are outdated.

## Requirements

- Blender 5.1.x
- EFMI-Tools installed and enabled
- A scene configured for EFMI-Tools export
- A control object, usually an Empty, with numeric custom properties driving shape keys
- Matching EFMI source data for the EFMI version used for export

Recommended EFMI-Tools settings used during testing:

- Apply All Modifiers
- Mirror Mesh
- Ignore Hidden Collections
- Ignore Muted Shape Keys
- Add Missing Vertex Groups
- Fill Missing Mesh Data

## Installation

Install this folder as a Blender addon, or zip the folder and install it through Blender's addon preferences.

After enabling the addon, open the 3D View sidebar and use the `CSBE` tab.

## Basic Use

1. Configure EFMI-Tools as usual and verify a normal base export works.
2. Confirm the CSBE pipeline status reports the expected EFMI version.
3. In CSBE, select the control object.
4. Press the scan button to load numeric custom properties.
5. For each property, set the values to export, for example `0, 1`.
6. Optionally set a shorter output name and component list, for example `2` or `0, 3, 8, 9`.
7. Choose an output directory or let CSBE use the EFMI output path.
8. Run `Export VB0 Batch`.

By default, CSBE logs only the current export pass and written files. Enable `Verbose Console Logging` if you need the older per-object driver and temporary-copy diagnostics.

Output names follow this pattern:

```text
Component<ID>_VB0_<Name>.buf
Component<ID>_VB0_<Name>_<Value>.buf
```

## Credits

Created by Kazeyako.

Repository: https://github.com/Kazenochi/MI-CSBE

MI-CSBE uses EFMI-Tools as the tested export backend and is designed to automate variant buffer exports around an existing EFMI-Tools workflow.

## Disclaimer

This is a workflow addon for an existing modding pipeline. Always keep backups of your Blender file and exported buffers. Currently, only the EFMI export path has been tested.
