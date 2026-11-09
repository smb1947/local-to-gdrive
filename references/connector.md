# Google Drive connector

Discover schemas on each harness. Tool prefixes and available operations can differ. These operations were verified in this Codex harness, including raw upload and replacement of file bytes. Map equivalent tools on another harness only after reading its schemas.

| Operation | Current tool suffix | Relevant arguments |
| --- | --- | --- |
| Connected identity | `google_drive_get_profile` | No arguments. Use returned account `id` for mapping identity |
| Root metadata | `google_drive_get_file_metadata` | `fileId="root"`, `fields="id,name,mimeType,parents"` |
| Folder search | `google_drive_search` | `item_type="folder"`, `special_filter_query_str`, optional opaque `page_token` |
| Direct children | `google_drive_list_folder` | `url` is a folder URL or literal `root`, `top_k` is a scan bound |
| Create folder | `google_drive_create_folder` | `name`, `parent_folder` as ID or literal `root` |
| File metadata | `google_drive_get_file_metadata` | `fileId`, `fields="id,name,mimeType,size,md5Checksum,parents,trashed"` |
| Current revision | `google_drive_list_file_revisions` | `fileId`; use observed `currentRevisionId` for the [revision tracking](handoff.md#revision-tracking) |
| Original bytes | `google_drive_fetch` | File `url`, `download_raw_file=true`, `include_base64=false` |
| Raw upload | `google_drive_upload_file` | `file_uri` as an absolute workspace path through the harness bridge, `file_name`, actual `parent_folder_id`, optional raw `mime_type` |
| Raw replacement | `google_drive_update_file` | Existing `fileId`, `file_uri` as an absolute workspace path, optional raw `mime_type` |
| Move to recovery | `google_drive_update_file` | Existing `fileId`, `addParents` set to verified recovery parent ID, `removeParents` set to verified current parent ID |

Despite conflicting legacy prose in the tool's file-reference description, the current schema explicitly accepts absolute workspace paths through the harness file bridge. This was verified with a synthetic file. Do not construct a connector file-reference object or base64 argument yourself when the schema requires a path.

Do not use `import_document`, `create_file`, or Google Docs/Sheets/Slides edits to represent raw file backups. Do not use `copy_file` for a local upload. Never use delete or sharing tools in ordinary copy runs.

## Root and ancestry

Check the connected account and resolve saved folder IDs directly. Verify each returned folder's name, type, and parent against the expected path. A matching saved ID is enough to reuse the folder. An ID can still resolve when a folder is in Trash. No separate Trash check is required for a saved ID.

If there is no saved ID or Drive returns not found, look up the expected folder name under its parent, starting at My Drive root. Use a parent-scoped query such as `'root' in parents and name = 'Codex' and trashed = false`. Escape apostrophes and backslashes in user-provided names. Reuse a unique match or create a missing folder in copy mode. If several folders match, ask which to use. Permission errors, timeouts, malformed responses, and incomplete searches do not establish that a folder is missing.

Normalize My Drive's actual root ID to `root` in the first context entry. Retain the actual IDs for the rest of the path. Bind a replacement ID only after verifying the same destination path and account. A preview can report missing folders without creating them or changing the mapping.

## Complete listings

`list_folder` is bounded by `top_k`; `fetch` on a folder is limited to 100 direct children. Neither is a recursive inventory. Recurse explicitly and do not use text search to establish that arbitrary binary files are absent. With `list_folder`, use the documented bound, inspect any truncation indicators, and treat reaching the bound as incomplete. If results are explicitly partial or the tool cannot establish completeness, stop before uploads. Where paginated metadata search supports the relevant file types, preserve `next_page_token` exactly and continue even after an empty filtered page. Never guess page tokens or assume an image/document filter includes all raw file types.

## File verification

The current metadata tool omits the MD5 checksum. Mirror mode uses Drive revision IDs and local checksums, so it never fetches file contents. See [revision tracking](handoff.md#revision-tracking).

For copy-only helper workflows that require remote MD5, fetch the original file with `download_raw_file=true, include_base64=false` and run `fingerprint` on the raw response. The response's top-level `id` identifies the Drive file. Its `file_id` can identify the download artifact instead.
