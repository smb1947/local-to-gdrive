# Local to GDrive

[![Tests on main](https://github.com/smb1947/local-to-gdrive/actions/workflows/tests.yml/badge.svg?branch=main&event=push)](https://github.com/smb1947/local-to-gdrive/actions/workflows/tests.yml?query=branch%3Amain+event%3Apush)

A Codex skill that mirrors a local project folder to Google Drive on demand. Keep working locally, preview what will change, and transfer only new or changed files.

## Usage

1. Clone the repo.

   ```bash
   git clone https://github.com/smb1947/local-to-gdrive.git
   ```

2. Open the cloned folder in Codex and ask it to install the skill.

   > Install the skill in this folder as `local-to-gdrive` in my personal Codex skills directory.

3. In a new Codex turn, ask for a copy. Codex can select the installed skill automatically.

   > Copy `sample-project` to Google Drive.

   You can also give Codex the full path to a folder under Documents. Codex passes the Documents-relative path to the helper.

   > Copy `/Users/example/Documents/sample-project` to Google Drive.

   To review the plan first, ask Codex to preview the copy.

## Why this skill

Keeping a local project current in Drive takes more than uploading it once. Repeat uploads can resend unchanged files, and local renames can leave old folders behind. This skill gives Codex users an on-demand, one-way mirror with a preview and a final check.

| Solution | What it does well | Gap for this use case |
| --- | --- | --- |
| **Google Drive web uploads** | Good for one-off copies. | **High friction**<br>On repeat uploads, you have to pick what to upload, remove old files, and check the result.<br>**Wasted bandwidth**<br>Uploading the whole folder again can resend files that haven’t changed. [Google Drive upload guide](https://support.google.com/drive/answer/2424368) |
| **Google Drive for desktop** | Syncs changes automatically. | **Latency**<br>In streaming mode, opening an uncached file can require a download, which can slow work in the Codex workspace. [Drive for desktop on macOS](https://support.google.com/drive/answer/12178485)<br>**Rate limits**<br>Frequent edits and generated files can add background sync traffic. If that traffic hits Drive's upload or download limits, sync can be delayed. [Google Drive troubleshooting](https://support.google.com/drive/answer/2565956) |
| **rclone** | Supports incremental one-way sync, dry runs, and a backup directory. | **Technical setup**<br>As a command-line tool, rclone requires you to configure the Drive connection and choose paths and flags.<br>**Codex integration**<br>Using rclone as a Codex skill requires a separate wrapper. [rclone sync](https://rclone.org/commands/rclone_sync/) · [rclone configuration and flags](https://rclone.org/docs/) |

## Defaults and scope

- **Source** is a folder under `~/Documents`. **Destination** is `My Drive/Codex/<source-folder>`.
- Hidden paths, including `.git`, and zero-byte files are skipped. Visible empty folders are mirrored.
- The skill covers on-demand, one-way copies. Two-way sync, scheduled backups, and sources outside Documents are out of scope.

## How it works

1. **Connect Drive.** Check the Google Drive plugin and guide setup if needed.
2. **Resolve both folders.** Show both paths. Stop with the expected path if the source is missing. Create a missing destination when applying the copy.
3. **Check the helper.** Verify Python 3.9+ and the required Drive operations.
4. **Plan the copy.** Show what will be copied, updated, skipped, created, or moved to recovery. A preview leaves Drive and saved mappings untouched.
5. **Copy and verify.** Show progress, move obsolete items to recovery outside the mirror, and verify the result against a fresh Drive listing. If a run fails, report where it stopped and what changed.

See [SKILL.md](SKILL.md) for the full workflow.

## How repeat copies work

After a successful transfer, the skill saves the local checksum and current Drive revision ID in a local SQLite cache keyed by Drive account and file ID. When both values match on the next run, the file is skipped. A missing pair or a change on either side triggers a transfer. Verification uses Drive metadata and revision IDs without downloading file contents.

| Scenario | Expected result |
| --- | --- |
| First copy | All eligible files and folders appear in the destination. |
| Repeat with no changes | Zero file transfers. |
| Edit one file | Exactly one file is updated. |
| Rename a folder | The new folder appears, and the old one moves to recovery. |
| Preview | The plan is shown with zero Drive or destination-mapping changes. |
| Upload or verification fails | The result identifies the failure and reports the mirror as incomplete. |

## Tests

Run `python3 -m unittest discover -s tests` from the repository root. The tests create temporary local folders, supply simulated Drive responses, and check the real helper’s results.

GitHub Actions runs the suite on pushes to `main` and pull requests targeting it. The badge shows the latest push result on `main`, and each run shows the test counts and logs. A separate live test would verify skill discovery and real uploads through the Google Drive plugin.
