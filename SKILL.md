---
name: local-to-gdrive
description: Mirror local folders from Documents to My Drive/Codex. Preview changes or manage saved destinations. Set up Google Drive when needed.
license: MIT
metadata:
  version: "0.1.0"
  repository: "https://github.com/smb1947/local-to-gdrive"
  maintainer: "Shankar Binjawadgi"
  compatibility: "Requires Codex, Python 3.9 or later, and access to the Google Drive plugin."
---

# Local to GDrive

Show each step in chat as it happens. If blocked, say where it stopped, why, and what changed.

## Step 1 · Check Drive

- Check the Drive profile. If the plugin is missing, start its official install flow. In Codex, show the exact catalog result through Plugin Management. Enable or reconnect as needed and verify the profile again.
- **Show** whether Drive is connected and what setup was needed.

## Step 2 · Confirm the folders

- **Source.** Run `scope` to check the local folder under `~/Documents`. If it’s missing, show the expected path and stop.
- **Destination.** Reuse the saved Drive folder if its ID resolves to the expected folder. If the ID is missing or Drive returns not found, look up the expected path and create any missing folders. For a preview, report what would be created. Report permission or connection errors as errors. See [destination checks](references/connector.md#root-and-ancestry).
- **Show** both paths and whether the destination was found, created, or proposed.

## Step 3 · Check the helper

- Confirm `scripts/drive_sync.py` and its bundled tests exist. With Python 3.9+, run `preflight --mirror` using the discovered Drive capabilities, including current revision IDs.
- **Show** whether the helper and connector checks passed. See [connector tools](references/connector.md) and [helper commands](references/handoff.md).

## Step 4 · Plan the copy

- Run `scan --mirror`, get a fresh Drive listing, and run `plan --mirror`. Compare folder paths, local checksums, and current Drive revision IDs. Skip dot-prefixed paths and zero-byte files. Include empty folders.
- Skip a file if its local checksum and Drive revision match the saved pair. Upload once if there is no pair. See [revision tracking](references/handoff.md#revision-tracking).
- **Show** new uploads, updates, unchanged files, empty files skipped from the source, folders to create, items to recover, and any errors.
- For a copy, include `--record-run` in the plan command to save the two trees and copy plan to a local file. See [run logs](references/handoff.md#run-logs).
- For a preview, stop after showing the plan. A preview makes no Drive or mapping changes.

## Step 5 · Copy and verify

- Create missing folders and copy only new or changed files. Confirm the upload response, file metadata, and revision before moving obsolete items to recovery outside the destination. Follow the [mirror execution rules](references/handoff.md#mirror-execution).
- Save the local checksum and the new Drive revision after a successful upload. Do not download Drive file contents.
- **Show** progress from verified work, for example “120 of 300 transfers verified (40%).” Report folder creation and recovery separately when relevant.
- Append a checkpoint when execution starts, after each folder, transfer, or recovery batch, and if execution stops early. Use the same plan file for each checkpoint.
- Run `verify-mirror` against a fresh Drive inventory. Report verified copies, unchanged files, skipped empty files, recovered items, failures, and the destination and recovery links when applicable. Say **Mirror check complete** only if verification passes.
- Show the run log path if it was created. Report any logging errors separately from the copy result.

For local mapping requests, run `mappings` or `unmap <source>` without connecting to Drive.
