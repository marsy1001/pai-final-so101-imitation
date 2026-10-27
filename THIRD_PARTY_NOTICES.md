# Third-party notices

## LeRobot `RobotKinematics` (vendored)
- File: `src/so101_demo/vendor/lerobot_kinematics.py`
- Source: https://github.com/huggingface/lerobot/blob/5aa74557f84c54d4b458f8b9643c5aa2982acfed/src/lerobot/model/kinematics.py
  (revision `5aa74557f84c54d4b458f8b9643c5aa2982acfed`)
- Copyright 2025 The HuggingFace Inc. team. Licensed under the Apache License,
  Version 2.0: `LICENSES/Apache-2.0.txt`
- Modification (2026-09-19): the `lerobot.utils.import_utils.require_package`
  dependency was replaced by a plain import check so the file has no LeRobot
  internal dependency. Everything else is unchanged.

## SO-ARM100 simulation assets (fetched, not committed)
- `scripts/fetch-so101.sh` downloads `Simulation/SO101/` of
  https://github.com/TheRobotStudio/SO-ARM100 at revision
  `eecbe3e0a9ebb23e25ad7b2759b03884c6660903` into `assets/so101/` (git-ignored).
  Only `assets/so101/MANIFEST.sha256` is tracked.
