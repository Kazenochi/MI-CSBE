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

## Requirements

- Blender 5.1.x
- EFMI-Tools installed and enabled
- A scene configured for EFMI-Tools export
- A control object, usually an Empty, with numeric custom properties driving shape keys

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
2. In CSBE, select the control object.
3. Press the scan button to load numeric custom properties.
4. For each property, set the values to export, for example `0, 1`.
5. Optionally set a shorter output name and component list, for example `2` or `0, 3, 8, 9`.
6. Choose an output directory or let CSBE use the EFMI output path.
7. Run `Export VB0 Batch`.

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
