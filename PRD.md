# Local to GDrive PRD

## Problem

People who keep project folders locally want an up-to-date copy in Google Drive. Recopying the whole folder takes time, and moving or renaming a local folder can leave an obsolete copy in Drive.

## Existing solutions and gaps

- **Google Drive web uploads** work for one-off copies. On repeat uploads, the user has to decide what to upload again, remove obsolete items, and check that the destination matches. [Google Drive upload guide](https://support.google.com/drive/answer/2424368)
- **Google Drive for desktop** syncs changes as you work, whether you stream files on demand or keep a full local copy. In a Codex workspace, that can send drafts, logs, and other intermediate files to Drive, adding network traffic. Hitting Drive's upload or download limits can delay sync. [Google Drive sync guide](https://support.google.com/drive/answer/13401938) · [Google Drive troubleshooting](https://support.google.com/drive/answer/2565956)
- **rclone** supports incremental one-way sync, dry runs, and a backup directory. It covers much of the same workflow, with users configuring the Drive connection and choosing paths and flags themselves. [rclone sync](https://rclone.org/commands/rclone_sync/) · [rclone configuration and flags](https://rclone.org/docs/)

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
