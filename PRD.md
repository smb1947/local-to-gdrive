# Local to GDrive PRD

## Problem

People who keep project folders locally want an up-to-date copy in Google Drive. Recopying the whole folder takes time, and moving or renaming a local folder can leave an obsolete copy in Drive.

## Existing solutions and gaps

| Solution | What it does well | Gap for this use case |
| --- | --- | --- |
| **Google Drive web uploads** | Good for one-off copies. | On repeat uploads, users have to pick which files to upload again, remove obsolete items, and check that the destination matches. Uploading the whole folder again is simpler, but retransferring unchanged files wastes bandwidth. [Google Drive upload guide](https://support.google.com/drive/answer/2424368) |
| **Google Drive for desktop** | Syncs changes automatically with streamed files or a full local copy. | **Latency.** In streaming mode, opening an uncached file can require a download, which can slow work in the Codex workspace. [Drive for desktop on macOS](https://support.google.com/drive/answer/12178485)<br>**Rate limits.** Frequent edits and generated files can add background sync traffic. If that traffic hits Drive's upload or download limits, sync can be delayed. [Google Drive troubleshooting](https://support.google.com/drive/answer/2565956) |
| **rclone** | Supports incremental one-way sync, dry runs, and a backup directory. | Users configure the Drive connection and choose paths and flags themselves. [rclone sync](https://rclone.org/commands/rclone_sync/) · [rclone configuration and flags](https://rclone.org/docs/) |

## Goal

Let a Codex user keep their workspace local while they iterate, then ask for a verified, one-way mirror in Google Drive. Repeat runs transfer only new or changed files.

## User flow

1. **Connect Drive.** Check the Google Drive plugin and guide the user through setup if needed.
2. **Confirm both folders.** Show the source and destination. By default, a folder under `~/Documents` goes to `My Drive/Codex/<folder-name>`. If the source is missing, show the expected path and stop. If the destination is missing, create it when applying the copy.
3. **Check the helper.** Confirm the local helper and required Drive operations are available.
4. **Plan the copy.** Show new, changed, unchanged, and skipped file counts, plus folders to create and items to move to recovery. A preview stops here without writing to Drive or changing saved destinations.
5. **Copy and verify.** Apply the plan, show progress, and check a fresh Drive listing before reporting the mirror complete. If a run fails, report where it stopped and what changed.

## Mirror rules

- Skip hidden paths and zero-byte files. Preserve visible empty folders.
- Skip an unchanged file when its local checksum and current Drive revision ID match the saved pair. Verify transfers with Drive metadata and revision IDs without downloading file contents.
- Move obsolete items to a recovery folder outside the mirror.

## Acceptance checks

| Scenario | Expected result |
| --- | --- |
| First copy | All eligible files and folders appear in the destination. |
| Repeat with no changes | Zero file transfers. |
| Edit one file | Exactly one file is updated. |
| Rename a folder | The new folder appears, and the old one moves to recovery. |
| Preview | The plan is shown with zero Drive or destination-mapping changes. |
| Upload or verification fails | The result identifies the failure and reports the mirror as incomplete. |

GitHub Actions tests the helper using temporary folders and simulated Drive responses. A separate live smoke test would verify skill discovery and uploads through the Google Drive plugin.

## Out of scope

Two-way sync, scheduled backups, and syncing folders outside Documents.
