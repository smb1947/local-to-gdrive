# Contributor guide

- **Where to edit.** `SKILL.md` defines the workflow, `scripts/drive_sync.py` implements the local helper, and `references/` documents the connector and helper inputs. Update the README when user-facing behavior changes.
- **Preserve mirror behavior.** Previews are read-only. Skip hidden paths and zero-byte files, keep visible empty folders, and use Drive revision IDs with local checksums to detect changes without downloading file contents.
- **Protect privacy.** Use synthetic paths and IDs in public examples and tests. Keep personal paths, Drive IDs, mappings, caches, and run logs out of Git.
- **Verify locally.** Run `python3 -m unittest discover -s tests` and `git diff --check`. Use temporary folders and simulated Drive inventories. Run live Drive tests only when explicitly requested.
