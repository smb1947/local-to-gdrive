# Local to GDrive

[![Tests on main](https://github.com/smb1947/local-to-gdrive/actions/workflows/tests.yml/badge.svg?branch=main&event=push)](https://github.com/smb1947/local-to-gdrive/actions/workflows/tests.yml?query=branch%3Amain+event%3Apush)

A Codex skill that mirrors a local folder to Google Drive. It copies new or changed files and can preview the plan before making changes. Current version is `v0.1.0`.

## Usage

1. Clone the repo.

   ```bash
   git clone https://github.com/smb1947/local-to-gdrive.git
   ```

2. Open the cloned folder in Codex and ask it to install the skill.

   > Install the skill in this folder as `local-to-gdrive` in my personal Codex skills directory.

3. In a new Codex turn, ask to copy a folder under `~/Documents`. Codex can select the installed skill automatically.

   > Copy `sample-project` to Google Drive.

   To specify an absolute path, replace the example below with your own path under Documents.

   > Copy `/Users/example/Documents/sample-project` to Google Drive.

   Codex should pass the path relative to Documents (`sample-project` here) to the helper.

See [SKILL.md](SKILL.md) for the full workflow.

## Defaults

- **Source** is a folder under `~/Documents`.
- **Destination** is `My Drive/Codex/<source-folder>`. For example, `~/Documents/sample-project` mirrors to `My Drive/Codex/sample-project`.
- **Excluded** are dot-prefixed files and folders (including `.git`) and zero-byte files. Visible empty folders are mirrored.

## How it works

1. **Connect Drive.** Check the Google Drive plugin and set it up if needed.
2. **Resolve both folders.** Stop if the source is missing. Find or create the destination and report both paths.
3. **Check the helper.** Verify Python 3.9+ and the required Drive operations.
4. **Preview the plan.** Compare both trees and report new, changed, unchanged, and obsolete items. A preview makes no changes in Drive.
5. **Copy and verify.** Copy new or changed files and move obsolete items to recovery outside the mirror. Recheck the destination before reporting success.

## Revision cache

After a successful transfer, the skill stores the local file checksum and current Drive revision ID in a local SQLite cache keyed by Drive account and file ID. On the next run, it skips the transfer when both values still match. A missing pair or a change on either side triggers an upload or update. The mirror uses Drive metadata and revision IDs for verification and skips content downloads.

## Tests

Run `python3 -m unittest discover -s tests` from the repository root.

Each test creates a temporary local source and supplies synthetic Drive inventories, revision IDs, or transfer receipts as needed. The real Python helper computes the copy plan and verifies the simulated results. Tests clean up their temporary files automatically and run without Codex, the Drive plugin, or Google credentials.

GitHub Actions runs this suite on an Ubuntu runner for pushes to `main` and pull requests targeting it. The badge shows the latest push result on `main`. Open a workflow run to see its test counts and logs.
