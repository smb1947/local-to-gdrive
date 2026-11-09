# Local to GDrive

A Codex skill that mirrors a local folder to Google Drive. It copies new or changed files and can preview the plan before making changes. Current version is `v0.1.0`.

## Usage

1. Clone the repo.

   ```bash
   git clone https://github.com/smb1947/local-to-gdrive.git
   ```

2. Open the cloned folder in Codex and ask it to install the skill.

   > Install the skill in this folder as `local-to-gdrive` in my personal Codex skills directory.

3. In a new Codex turn, try this with a folder you have under `~/Documents`.

   > Use `$local-to-gdrive` to copy `sample-project` to Google Drive.

See [SKILL.md](SKILL.md) for the full workflow.

## How it works

1. **Connect Drive.** Check the Google Drive plugin and set it up if needed.
2. **Resolve both folders.** Stop if the source is missing. Find or create the destination and report both paths.
3. **Check the helper.** Verify Python 3.9+ and the required Drive operations.
4. **Preview the plan.** Compare both trees and report new, changed, unchanged, and obsolete items. A preview makes no changes in Drive.
5. **Copy and verify.** Copy new or changed files and move obsolete items to recovery outside the mirror. Recheck the destination before reporting success.

## Defaults

- **Source** is a folder under `~/Documents`.
- **Destination** is `My Drive/Codex/<source-folder>`. For example, `~/Documents/sample-project` mirrors to `My Drive/Codex/sample-project`.
- **Excluded** are dot-prefixed files and folders (including `.git`) and zero-byte files. Visible empty folders are mirrored.

## Revision cache

After a successful transfer, the skill stores the local file checksum and current Drive revision ID in a local SQLite cache keyed by Drive account and file ID. On the next run, it skips the transfer when both values still match. A missing pair or a change on either side triggers an upload or update. The mirror uses Drive metadata and revision IDs for verification and skips content downloads.

## Tests

Run `python3 -m unittest discover -s tests` from the repository root. Tests build synthetic sources in temporary directories, simulate Drive inventories, and clean up automatically.
