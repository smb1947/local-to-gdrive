# Local handoff schemas

These JSON documents are created by the agent from actual plugin responses. They are inputs to the local helper, not cloud tool signatures. Never fill IDs, hashes, completeness, or verification fields from assumptions. Use a private temporary folder outside the source.

## Capabilities

```json
{
  "provider": "google-drive",
  "account_id": "profile-id-from-plugin",
  "operations": {
    "list_children": true,
    "get_metadata": true,
    "create_folder": true,
    "upload_binary": true,
    "update_binary": true,
    "read_binary": true
  }
}
```

For a mirror, also set `move_items` and `read_revision` from the current tool schemas. Mirror mode does not require `read_binary`. Validate the connection with the profile tool. Raw bytes can be provided through a harness-managed file bridge. If an operation is unavailable, set it false. `preflight` will stop before transfer.

## Folder context

```json
{
  "provider": "google-drive",
  "account_id": "profile-id-from-plugin",
  "folders": [
    {"id": "codex-id", "name": "Codex", "parent_id": "root", "kind": "folder"},
    {"id": "sample-project-id", "name": "sample-project", "parent_id": "codex-id", "kind": "folder"}
  ]
}
```

Include the full verified ancestry from Codex to the destination. Normalize the first parent to `root` only after comparing its actual parent ID with My Drive root metadata. For a nested destination, include every ancestor. `bind sample-project --context context.json` saves the IDs, account, and destination path.

## Inventories and plan

`scan sample-project --output local.json` creates the local manifest. Do not edit its contents.

A complete normalized remote inventory has the following structure.

```json
{
  "context": {"provider": "google-drive", "account_id": "profile-id-from-plugin", "folders": []},
  "complete": true,
  "files": [
    {"path": "report.txt", "id": "report-id", "kind": "file", "size": 9, "md5": "f82e520080791c534f980613240385c2"},
    {"path": "docs", "id": "docs-id", "kind": "folder"}
  ]
}
```

Replace the illustrative empty `folders` with the full verified context above. Paths are relative to the mapped destination. Include recursively listed folders as `kind=folder` and files as `kind=file`. Convert provider sizes to integers. In mirror mode, include the current `revision_id` for each existing file that matches a local file. Remote MD5 is only needed for copy-only plans.

```bash
python3 <skill>/scripts/drive_sync.py plan --local local.json --remote remote.json --output plan.json
```

The resulting plan contains `run_id`, mapping IDs, actions, source sizes and hashes, and any comparison errors. `upload` means create missing bytes; `update` means replace bytes under the existing `remote_id`; `skip` means no cloud write. An update must not create a second file.

## Raw response fingerprint

Save the structured response returned by the plugin's complete raw-file fetch to a private JSON file, including `file_size_bytes`, `mime_type`, and `file_uri.download_url`. Use the following command to hash that artifact.

```bash
python3 <skill>/scripts/drive_sync.py fingerprint --raw-response raw.json --output digest.json
```

It returns only `size` and `md5`. It never calls Drive APIs, needs no credentials, and does not log the artifact URL. Inline `b64_string` is accepted when actually returned by the plugin. A zero-byte file is valid if the original byte count is zero. Missing size evidence, truncation, native Google document exports, or an unusable artifact are failures.

## Post-transfer receipts

```json
{
  "run_id": "the-actual-run-id-from-plan",
  "files": [
    {
      "path": "report.txt",
      "status": "verified",
      "context": {
        "provider": "google-drive",
        "account_id": "profile-id-from-plugin",
        "folders": [
          {"id": "codex-id", "name": "Codex", "parent_id": "root", "kind": "folder"},
          {"id": "sample-project-id", "name": "sample-project", "parent_id": "codex-id", "kind": "folder"}
        ]
      },
      "remote": {
        "id": "observed-file-id",
        "name": "report.txt",
        "parent_id": "sample-project-id",
        "size": 9,
        "revision_id": "observed-current-revision-id"
      }
    }
  ]
}
```

For a mirror receipt, use the confirmed write response, fresh metadata, and the new `revision_id` in place of remote MD5. Use a fresh complete-byte fingerprint only for copy-only receipts. For nested files, extend the context through the actual parent folder. For failed actions, provide `path`, `status="failed"`, and `error`. No receipt is needed for skipped files. A missing receipt is a failure, not evidence of success.

```bash
python3 <skill>/scripts/drive_sync.py finish --plan plan.json --receipts receipts.json
```

The helper checks mapping identity, account, folder IDs, file names, parent ancestry, original ID on updates, local file stability, byte size, and the observed revision for mirror receipts. It records only verified results. It cannot prove that a fabricated JSON receipt came from a plugin, so the harness must preserve provenance from actual tool responses.

## Revision tracking

The mirror compares the current local MD5 and Drive revision against the saved pair indexed by Drive account and file ID. The planner reads the index automatically. With no pair or a change on either side, upload the local file or replace the existing file in place.

Get the current revision from the successful write response if available, otherwise read it immediately through `list_file_revisions`. After an update, retry a stale revision result before accepting the receipt. Require the new revision, expected byte size, and confirmed parent. If they cannot be confirmed, report a transfer failure. `finish` saves accepted pairs. Use fresh revisions in the final inventory. Mirror mode makes no content downloads and trusts the successful upload response.

## Mirror execution

1. Run `scope <source> [--destination <path-relative-to-Codex>]` to resolve the paths without writing. Check the destination in Drive and show both paths before planning. Keep handoffs in a private temporary directory outside the source. Verify the saved account, folder IDs, and ancestry. If the expected destination is missing, resolve or create it by parent ID, then bind the verified IDs for a mirror run. Ask about ambiguous names or a different destination path. Keep the scope inside the mapped destination. Stop if another saved destination is nested in it or resolves to the same Drive folder. For an unbound preview, compare the verified trees directly. A confirmed absent destination means all included folders and files are new.
2. Run `preflight --mirror --capabilities <file>` with `move_items` and `read_revision` set from discovered connector schemas. Add `--mirror` to both `scan` and `plan`. The output adds `create_folders` (including empty folders), `recover_before_copy` (file/folder conflicts), and `recover_after_copy` (obsolete items). Recovering a folder covers its entire subtree. Ignore dot-prefixed paths on both sides. The plan lists excluded zero-byte source files in `ignored_empty` and leaves their Drive counterparts alone. It also ignores Drive-only zero-byte files. A zero-byte Drive file at an included nonempty source path is an update. Include its observed revision ID and numeric size. Empty folders remain in scope. A recovered visible folder retains all its children in recovery. Stop on incomplete listings, ambiguous paths, or comparison errors before applying any plan action.
3. For recovery, create a unique run folder outside the mirror, in the same account's My Drive. Preserve each item's relative path. Recheck the source scan and verify the item's current ID, parent and ancestry before moving it. Before moving a folder, re-list its subtree and compare it with the planned inventory. Verify the new recovery parent and removal from the old parent. Use a metadata-only move and never permanently delete items. Report the recovery link and moved paths if a run stops partway.
4. Move only blocking type conflicts before copying. Create planned folders in parent order. Use raw uploads and replace changed bytes under the existing Drive ID. Confirm every transfer with fresh metadata and the current revision, and run `finish` with observed receipts. Check uncertain operations before retrying. If any transfer fails or the source scan changes, stop before moving obsolete items.
5. After recovery, re-list the complete destination and refresh matching file sizes and revision IDs. Run `verify-mirror --plan <plan.json> --remote <fresh-remote.json>`. It rescans the source and checks both trees using the same exclusions. Report a completed mirror only if verification succeeds. Report copied, unchanged, recovered, and failed items with the destination and recovery links when applicable. The helper's `finish` result verifies transfers only.

## Persisted state

All state is per user under `~/.config/codex2gdrive/`.

| File | Purpose |
| --- | --- |
| `connector-mappings.json` | Source paths mapped to connected account and stable Drive folder IDs |
| `connector-checksums.sqlite3` | Local checksums paired with Drive revisions, indexed by account and file ID |
| `connector-maintenance.json` | Daily dependency check, test attempt and passing-test times |
| `connector-maintenance.lock` | Serializes daily tests across concurrent invocations |
| `connector-sync.log` | Timestamped JSON lines for mapping changes, tests, per-file verification, and run results |
| `last-connector-run.json` | Most recent verified summary and per-file failures |

Legacy rclone mappings and OAuth client files are not read or migrated automatically. Rebind through plugin metadata on first use. Existing cloud files remain untouched.
