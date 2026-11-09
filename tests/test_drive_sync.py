import base64
import contextlib
from datetime import datetime, timedelta, timezone
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import drive_sync as app


def context(account='account-1', destination='stories-id'):
    return {'provider': 'google-drive', 'account_id': account, 'folders': [
        {'id': 'codex-id', 'name': 'Codex', 'parent_id': 'root', 'kind': 'folder'},
        {'id': destination, 'name': 'stories', 'parent_id': 'codex-id', 'kind': 'folder'}]}


def capabilities():
    return {'provider': 'google-drive', 'account_id': 'account-1', 'operations': {
        key: True for key in ('list_children', 'get_metadata', 'create_folder', 'upload_binary', 'update_binary',
                              'read_binary', 'read_revision')}}


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.source = self.home / 'Documents/stories'
        self.source.mkdir(parents=True)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(Path, 'home', return_value=self.home))
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(contextlib.redirect_stderr(io.StringIO()))

    def write(self, path='report.txt', content=b'Version 1'):
        file = self.source / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(content)
        return file

    def plan(self, files=None, *, mirror=False):
        app.bind('stories', context())
        return app.build_plan(app.scan('stories', mirror=mirror),
                              {'context': context(), 'complete': True, 'files': files or []},
                              mirror=mirror)

    def receipt(self, plan, path='report.txt', ident='report-id'):
        action = next(a for a in plan['actions'] if a['path'] == path)
        chain = context()
        if '/' in path:
            chain['folders'].append({'id': 'docs-id', 'name': Path(path).parent.name,
                                     'parent_id': 'stories-id', 'kind': 'folder'})
        return {'run_id': plan['run_id'], 'files': [{
            'path': path, 'status': 'verified', 'context': chain,
            'remote': {'id': ident, 'name': Path(path).name, 'parent_id': chain['folders'][-1]['id'],
                       'size': action['size'], 'md5': action['md5'], 'revision_id': 'r1'}}]}

    def test_relative_paths_and_nested_names(self):
        self.assertEqual(app.relative('./stories/'), 'stories')
        self.assertEqual(app.relative('easy peasy recipes'), 'easy peasy recipes')
        self.assertEqual(app.relative('work/notes'), 'work/notes')

    def test_invalid_paths_rejected(self):
        for name in ('', '.', '/tmp', '../stories', 'stories/../other', 'remote:folder', 'x\\y'):
            with self.subTest(name=name), self.assertRaises(app.SyncError):
                app.relative(name)

    def test_missing_or_nonfolder_source_rejected(self):
        self.write()
        for name in ('missing', 'stories/report.txt'):
            with self.assertRaises(app.SyncError):
                app.source_folder(name)

    def test_scan_binary_hidden_nested_and_skip_links(self):
        self.write('.hidden', b'\x00\xff')
        self.write('docs/note.txt', b'note')
        (self.source/'linked').symlink_to(self.home)
        manifest = app.scan('stories')
        self.assertEqual([f['path'] for f in manifest['files']], ['.hidden', 'docs/note.txt'])
        self.assertIn('linked', manifest['skipped_links_or_special'])

    def test_source_symlink_rejected(self):
        (self.home/'Documents/alias').symlink_to(self.source)
        with self.assertRaises(app.SyncError):
            app.scan('alias')

    def test_git_metadata_never_hashed_or_planned(self):
        self.write('.git/objects/private-object', b'git object')
        self.write('nested/repo/.git/config', b'git config')
        self.write('worktree/.git', b'gitdir: /elsewhere')
        self.write('.gitignore', b'build/')
        self.write('.github/workflows/check.yml', b'name: check')
        self.write('report.txt', b'normal file')
        original = app.file_snapshot
        def snapshot(path):
            self.assertNotIn('.git', path.relative_to(self.source).parts)
            return original(path)
        with patch.object(app, 'file_snapshot', side_effect=snapshot):
            manifest = app.scan('stories')
            plan = self.plan()
        self.assertEqual({item['path'] for item in manifest['files']},
                         {'.gitignore', '.github/workflows/check.yml', 'report.txt'})
        self.assertEqual(set(manifest['ignored_git_metadata']),
                         {'.git', 'nested/repo/.git', 'worktree/.git'})
        self.assertEqual({item['path'] for item in plan['actions']},
                         {'.gitignore', '.github/workflows/check.yml', 'report.txt'})

    def test_explicit_source_inside_git_is_rejected(self):
        self.write('.git/objects/object')
        for source in ('stories/.git', 'stories/.git/objects'):
            with self.subTest(source=source), self.assertRaisesRegex(app.SyncError, 'Git metadata'):
                app.scan(source)

    def test_persist_mapping_without_credentials(self):
        app.bind('stories', context())
        value = app.mappings()['mappings']['stories']
        self.assertEqual(value['destination_id'], 'stories-id')
        self.assertEqual(value['destination_path'], 'stories')
        self.assertNotIn('token', value)
        self.assertNotIn('stories/child', app.mappings()['mappings'])

    def test_root_outside_codex_and_broken_ancestry_rejected(self):
        for field, value in [('name', 'Backups'), ('parent_id', 'elsewhere')]:
            data = context()
            data['folders'][0][field] = value
            with self.assertRaises(app.SyncError):
                app.bind('stories', data)

    def test_new_files_are_upload_actions(self):
        self.write()
        self.assertEqual(self.plan()['actions'][0]['action'], 'upload')

    def test_same_size_changes_are_updates(self):
        self.write(content=b'Version 2')
        old_hash = app.hashlib.md5(b'Version 1').hexdigest()
        plan = self.plan([{'path':'report.txt','kind':'file','id':'old-id','size':9,'md5':old_hash}])
        self.assertEqual(plan['actions'][0]['action'], 'update')
        self.assertEqual(plan['actions'][0]['remote_id'], 'old-id')

    def test_unchanged_files_skip_even_if_timestamp_changes(self):
        file = self.write()
        import os
        os.utime(file, (100, 100))
        entry = {'path':'report.txt','kind':'file','id':'id','size':9,'md5':app.md5(file)}
        self.assertEqual(self.plan([entry])['actions'][0]['action'], 'skip')

    def test_destination_only_files_are_never_deleted(self):
        self.write()
        extra = {'path':'archive','kind':'file','id':'archive-id','size':1,'md5':'0'*32}
        plan = self.plan([extra])
        self.assertEqual([a['path'] for a in plan['actions']], ['report.txt'])
        self.assertNotIn('delete', [a['action'] for a in plan['actions']])

    def test_mirror_copies_new_folder_and_recovers_old_folder_once(self):
        # Source contains ABC Two/report.txt, unchanged.txt, and an empty folder named empty.
        # Drive contains ABC One/report.txt and an identical unchanged.txt.
        # Expect the two missing folders to be created and the new report uploaded. Skip unchanged.txt
        # and schedule ABC One for recovery as a single folder, including its contents.
        self.write('ABC Two/report.txt')
        self.write('unchanged.txt')
        (self.source / 'empty').mkdir()
        app.checksum_cache('remember', {'account_id': 'account-1', 'files': [
            {'file_id': 'same-file', 'revision_id': 'r1', 'revision_after': 'r1',
             'size': 9, 'md5': app.md5(self.source / 'unchanged.txt')}]})
        plan = self.plan([
            {'path': 'ABC One', 'kind': 'folder', 'id': 'old-folder'},
            {'path': 'ABC One/report.txt', 'kind': 'file', 'id': 'old-file'},
            {'path': 'unchanged.txt', 'kind': 'file', 'id': 'same-file',
             'size': 9, 'revision_id': 'r1'},
        ], mirror=True)
        self.assertEqual(plan['create_folders'], ['ABC Two', 'empty'])
        self.assertEqual(plan['recover_after_copy'], [
            {'path': 'ABC One', 'kind': 'folder', 'remote_id': 'old-folder'}])
        self.assertEqual({a['path']: a['action'] for a in plan['actions']},
                         {'ABC Two/report.txt': 'upload', 'unchanged.txt': 'skip'})

    def test_mirror_recovers_blocking_file_but_keeps_excluded_path(self):
        # Source contains docs/note.txt and a symlink named alias pointing to docs/.
        # Drive contains a file named docs and a real folder alias/ containing note.txt.
        # Expect the blocking docs file to go to recovery before docs/ is created.
        # The source symlink is excluded, so the Drive alias/ subtree stays in place.
        self.write('docs/note.txt')
        (self.source / 'alias').symlink_to(self.source / 'docs', target_is_directory=True)
        plan = self.plan([
            {'path': 'docs', 'kind': 'file', 'id': 'blocking-file'},
            {'path': 'alias', 'kind': 'folder', 'id': 'excluded-folder'},
            {'path': 'alias/note.txt', 'kind': 'file', 'id': 'excluded-file'},
        ], mirror=True)
        self.assertEqual(plan['recover_before_copy'], [
            {'path': 'docs', 'kind': 'file', 'remote_id': 'blocking-file'}])
        self.assertEqual(plan['recover_after_copy'], [])
        self.assertEqual(plan['create_folders'], ['docs'])
        self.assertEqual(plan['actions'][0]['action'], 'upload')

    def test_scope_resolves_default_and_rejects_conflicting_saved_destination(self):
        # The source is an existing ~/Documents/stories folder with no saved mapping yet.
        # Expect My Drive/Codex/stories by default and My Drive/Codex/other when requested.
        # Once stories is bound to its default, switching to other requires an explicit rebind.
        self.assertEqual(app.scope('stories')['destination_path'], 'My Drive/Codex/stories')
        self.assertEqual(app.scope('stories', 'other')['destination_path'], 'My Drive/Codex/other')
        app.bind('stories', context())
        with self.assertRaises(app.SyncError):
            app.scope('stories', 'other')

    def test_mirror_preflight_requires_move_support(self):
        # The connector supports listing and copying files but initially cannot move them.
        # Expect mirror preflight to reject it until it can move obsolete Drive items to recovery.
        available = capabilities()
        with self.assertRaisesRegex(app.SyncError, 'move_items'):
            app.capability_check(available, mirror=True)
        available['operations']['move_items'] = True
        del available['operations']['read_binary']
        self.assertTrue(app.capability_check(available, mirror=True))
        del available['operations']['read_revision']
        with self.assertRaisesRegex(app.SyncError, 'read_revision'):
            app.capability_check(available, mirror=True)

    def test_mirror_uses_resolved_destination_without_requiring_trash_status(self):
        # A matching folder ID and parent are enough to plan even when the
        # connector omits the Trash flag or reports that the folder is in Trash.
        app.bind('stories', context())
        manifest = app.scan('stories', mirror=True)
        for folder_fields in ({}, {'trashed': True}):
            chain = context()
            chain['folders'][0].update(folder_fields)
            with self.subTest(folder_fields=folder_fields):
                plan = app.build_plan(manifest, {'context': chain, 'complete': True, 'files': []}, mirror=True)
                self.assertEqual(plan['mapping']['destination_id'], 'stories-id')

    def test_scope_reports_the_expected_path_for_a_missing_source(self):
        with self.assertRaisesRegex(app.SyncError, 'Source folder not found: .*Documents/missing'):
            app.scope('missing')

    def test_mirror_baselines_use_local_hash_and_drive_revision_without_remote_md5(self):
        # A same-size Drive file with no saved pair is updated once. A matching
        # saved pair skips later runs. Same-size changes on either side trigger an update.
        self.write()
        entry = {'path': 'report.txt', 'id': 'report-id', 'kind': 'file', 'size': 9, 'revision_id': 'r0'}
        plan = self.plan([entry], mirror=True)
        self.assertEqual(plan['actions'][0]['action'], 'update')
        receipt = self.receipt(plan)
        observed = receipt['files'][0]['remote']
        del observed['md5']
        observed['revision_id'] = 'r0'
        self.assertEqual(app.finish(plan, receipt)['status'], 'failed')
        observed['revision_id'] = 'r1'
        self.assertEqual(app.finish(plan, receipt)['status'], 'complete')
        self.assertEqual(self.plan([{**entry, 'revision_id': 'r1'}], mirror=True)['actions'][0]['action'], 'skip')
        self.assertEqual(self.plan([{**entry, 'revision_id': 'r2'}], mirror=True)['actions'][0]['action'], 'update')
        self.write(content=b'Version 2')
        self.assertEqual(self.plan([{**entry, 'revision_id': 'r1'}], mirror=True)['actions'][0]['action'], 'update')

    def test_checksum_cache_reuses_only_the_matching_account_file_revision_and_size(self):
        # Two files share a revision label. The cache must distinguish their file IDs
        # and reject lookups from another account or for a changed revision or size.
        first = {'file_id': 'report', 'revision_id': 'r1', 'size': 9}
        second = {**first, 'file_id': 'notes'}
        request = {'account_id': 'account-1', 'files': [first, second]}
        self.assertEqual(app.checksum_cache('lookup', request), {'hits': {}})
        entries = [{**row, 'revision_after': 'r1', 'md5': digest}
                   for row, digest in zip(request['files'], ['a' * 32, 'b' * 32])]
        self.assertEqual(app.checksum_cache('remember', {**request, 'files': entries}), {'stored': 2})
        hits = app.checksum_cache('lookup', request)['hits']
        self.assertEqual(hits['report']['md5'], 'a' * 32)
        self.assertEqual(hits['notes']['md5'], 'b' * 32)
        self.assertEqual(app.checksum_cache('lookup', {**request, 'account_id': 'other'}), {'hits': {}})
        for changed in ({**first, 'revision_id': 'r2'}, {**first, 'size': 10}):
            self.assertEqual(app.checksum_cache('lookup', {**request, 'files': [changed]}), {'hits': {}})

    def test_checksum_cache_rejects_a_revision_change_and_replaces_stale_records(self):
        # A read of r1 ends after Drive switched to r2. Its checksum must never be cached.
        # A stable read of r2 can replace the earlier verified r1 record.
        first = {'file_id': 'report', 'revision_id': 'r1', 'size': 9, 'md5': 'a' * 32}
        request = {'account_id': 'account-1', 'files': [{**first, 'revision_after': 'r2'}]}
        with self.assertRaisesRegex(app.SyncError, 'revision changed'):
            app.checksum_cache('remember', request)
        self.assertEqual(app.checksum_cache('lookup', request), {'hits': {}})
        app.checksum_cache('remember', {**request, 'files': [{**first, 'revision_after': 'r1'}]})
        second = {**first, 'revision_id': 'r2', 'revision_after': 'r2', 'md5': 'b' * 32}
        app.checksum_cache('remember', {**request, 'files': [second]})
        self.assertEqual(app.checksum_cache('lookup', request), {'hits': {}})
        self.assertEqual(app.checksum_cache('lookup', {**request, 'files': [second]})['hits']['report']['md5'],
                         'b' * 32)

    def test_final_mirror_checks_contents_folders_and_obsolete_paths(self):
        # Source contains ABC Two/report.txt and an empty folder named empty.
        # The first Drive inventory has a same-size report with an unrecorded revision, an
        # obsolete ABC One/ folder, and no empty/ folder. Expect all three differences.
        # The second inventory fixes those differences. Expect the verifier and CLI to pass.
        file = self.write('ABC Two/report.txt')
        (self.source / 'empty').mkdir()
        plan = self.plan(mirror=True)
        new_folder = {'path': 'ABC Two', 'kind': 'folder', 'id': 'new-folder'}
        new_file = {'path': 'ABC Two/report.txt', 'kind': 'file', 'id': 'new-file',
                    'size': file.stat().st_size, 'revision_id': 'r1'}
        old_folder = {'path': 'ABC One', 'kind': 'folder', 'id': 'old-folder'}
        remote = {'context': context(), 'complete': True,
                  'files': [new_folder, new_file, old_folder]}
        result = app.verify_mirror(plan, remote)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual({item['path'] for item in result['differences']},
                         {'ABC One', 'ABC Two/report.txt', 'empty'})
        plan_file, remote_file = self.home / 'plan.json', self.home / 'remote.json'
        app.write_json(plan_file, plan)
        app.write_json(remote_file, remote)
        command = ['verify-mirror', '--plan', str(plan_file), '--remote', str(remote_file)]
        self.assertEqual(app.main(command), 1)
        receipt = self.receipt(plan, 'ABC Two/report.txt', 'new-file')
        del receipt['files'][0]['remote']['md5']
        self.assertEqual(app.finish(plan, receipt)['status'], 'complete')
        remote['files'] = [new_folder, new_file, {'path': 'empty', 'kind': 'folder', 'id': 'empty-folder'}]
        self.assertEqual(app.verify_mirror(plan, remote), {'status': 'complete', 'differences': []})
        app.write_json(remote_file, remote)
        self.assertEqual(app.main(command), 0)

    def test_mirror_rejects_destination_with_another_mapping_inside_it(self):
        # Source stories/nested/ contains report.txt and already has its own saved mapping.
        # Its Drive destination, My Drive/Codex/stories/nested, sits inside the parent mirror.
        # Expect planning for stories to stop before it can alter the separately mapped folder.
        self.write('nested/report.txt')
        nested = context()
        nested['folders'].append({'name': 'nested', 'kind': 'folder',
                                  'id': 'nested-id', 'parent_id': 'stories-id'})
        app.bind('stories/nested', nested)
        with self.assertRaisesRegex(app.SyncError, 'overlaps'):
            self.plan(mirror=True)

    def test_mirror_ignores_dotfiles_and_hidden_subtrees_on_both_sides(self):
        # Source contains .env, .git/config, docs/.cache/note.txt, and docs/.DS_Store.
        # Drive contains docs/.remote and .drive-only/note.txt.
        # The visible tree on both sides is just docs/. Expect no uploads or recovery moves.
        # A new dotfile added during the run should still let final verification pass.
        self.write('.env')
        self.write('.git/config')
        self.write('docs/.cache/note.txt')
        self.write('docs/.DS_Store')
        manifest = app.scan('stories', mirror=True)
        self.assertEqual(manifest['files'], [])
        self.assertEqual(manifest['folders'], ['docs'])
        remote_files = [
            {'path': 'docs', 'kind': 'folder', 'id': 'docs'},
            {'path': '.drive-only', 'kind': 'folder', 'id': 'hidden-folder'},
            {'path': '.drive-only/note.txt', 'kind': 'file', 'id': 'hidden-note'},
            {'path': 'docs/.remote', 'kind': 'file', 'id': 'hidden-file'},
        ]
        plan = self.plan(remote_files, mirror=True)
        self.assertEqual(plan['actions'], [])
        self.assertEqual(plan['recover_after_copy'], [])
        self.write('.added-during-sync')
        remote = {'context': context(), 'complete': True, 'files': remote_files}
        self.assertEqual(app.verify_mirror(plan, remote)['status'], 'complete')

    def test_mirror_finish_detects_a_new_visible_folder_before_recovery(self):
        # The source and Drive destination are both empty when the mirror is planned.
        # A new visible empty folder appears in the source before transfer verification.
        # Expect finish to reject the stale plan before any obsolete Drive items are moved.
        plan = self.plan(mirror=True)
        (self.source / 'new-empty-folder').mkdir()
        with self.assertRaisesRegex(app.SyncError, 'Source inventory changed'):
            app.finish(plan, {'run_id': plan['run_id'], 'files': []})

    def test_mirror_skips_empty_files_and_updates_an_empty_drive_copy(self):
        # Source has an empty draft, a folder holding only an empty marker, and a full report.
        # Drive has an older nonempty draft, an empty Drive-only file, and an empty report.
        # Expect the draft and Drive-only file to stay in place, the folder to be created,
        # and the report to be updated under its existing Drive ID.
        self.write('draft.md', b'')
        self.write('notes/marker', b'')
        report = self.write('report.txt')
        remote_files = [
            {'path': 'draft.md', 'kind': 'file', 'id': 'old-draft', 'size': 7},
            {'path': 'drive-only.txt', 'kind': 'file', 'id': 'drive-only', 'size': 0},
            {'path': 'report.txt', 'kind': 'file', 'id': 'report-id', 'size': 0, 'revision_id': 'r0'},
        ]
        plan = self.plan(remote_files, mirror=True)
        self.assertEqual(plan['ignored_empty'], ['draft.md', 'notes/marker'])
        self.assertEqual(plan['create_folders'], ['notes'])
        self.assertEqual(plan['recover_after_copy'], [])
        self.assertEqual(plan['errors'], [])
        self.assertEqual([(a['path'], a['action'], a['remote_id']) for a in plan['actions']],
                         [('report.txt', 'update', 'report-id')])
        self.assertEqual(app.finish(plan, self.receipt(plan))['status'], 'complete')
        remote_files[2].update(size=report.stat().st_size, revision_id='r1')
        remote_files.append({'path': 'notes', 'kind': 'folder', 'id': 'notes-folder'})
        remote = {'context': context(), 'complete': True, 'files': remote_files}
        self.assertEqual(app.verify_mirror(plan, remote)['status'], 'complete')

    def test_missing_hash_is_per_file_failure(self):
        self.write()
        plan = self.plan([{'path':'report.txt','kind':'file','id':'id','size':9}])
        self.assertEqual(len(plan['errors']), 1)
        self.assertEqual(plan['actions'], [])

    def test_file_folder_conflicts_fail(self):
        self.write('docs/note.txt')
        plan = self.plan([{'path':'docs','kind':'file','id':'id','size':1,'md5':'0'*32}])
        self.assertEqual(len(plan['errors']), 1)

    def test_duplicates_and_partial_listings_rejected(self):
        app.bind('stories', context())
        duplicate = {'path':'x','kind':'file','id':'id','size':0,'md5':'0'*32}
        for remote in ({'context':context(),'complete':False,'files':[]},
                       {'context':context(),'complete':True,'files':[duplicate,duplicate]}):
            with self.assertRaises(app.SyncError):
                app.build_plan(app.scan('stories'), remote)

    def test_account_switch_and_same_name_different_id_rejected(self):
        app.bind('stories', context())
        for other in (context(account='another'), context(destination='another-id')):
            with self.assertRaises(app.SyncError):
                app.build_plan(app.scan('stories'), {'context':other,'complete':True,'files':[]})

    def test_stale_manifest_rejected(self):
        self.write()
        app.bind('stories', context())
        manifest = app.scan('stories')
        self.write(content=b'edited')
        with self.assertRaises(app.SyncError):
            app.build_plan(manifest, {'context':context(),'complete':True,'files':[]})

    def test_verified_upload_is_recorded(self):
        self.write()
        plan = self.plan()
        result = app.finish(plan, self.receipt(plan))
        self.assertEqual(result['counts']['uploaded'], 1)
        self.assertEqual(result['status'], 'complete')
        self.assertTrue((app.state_dir()/'last-connector-run.json').is_file())

    def test_missing_receipt_is_failure(self):
        self.write()
        plan = self.plan()
        result = app.finish(plan, {'run_id':plan['run_id'],'files':[]})
        self.assertEqual(result['counts']['failed'], 1)
        self.assertEqual(result['counts']['uploaded'], 0)

    def test_duplicate_instead_of_inplace_update_is_failure(self):
        self.write(content=b'Version 2')
        old = {'path':'report.txt','kind':'file','id':'old-id','size':9,'md5':'0'*32}
        plan = self.plan([old])
        result = app.finish(plan, self.receipt(plan, ident='new-id'))
        self.assertEqual(result['counts']['failed'], 1)

    def test_wrong_hash_name_account_or_parent_receipt_is_failure(self):
        self.write()
        plan = self.plan()
        for field, value in [('md5','0'*32),('name','wrong.txt'),('parent_id','wrong')]:
            receipt = self.receipt(plan)
            receipt['files'][0]['remote'][field] = value
            self.assertEqual(app.finish(plan, receipt)['counts']['failed'], 1)
        receipt = self.receipt(plan)
        receipt['files'][0]['context']['account_id'] = 'wrong'
        self.assertEqual(app.finish(plan, receipt)['counts']['failed'], 1)

    def test_nested_parent_ancestry_is_verified(self):
        self.write('docs/note.txt')
        plan = self.plan()
        self.assertEqual(app.finish(plan, self.receipt(plan, 'docs/note.txt'))['counts']['uploaded'], 1)
        receipt = self.receipt(plan, 'docs/note.txt')
        receipt['files'][0]['context']['folders'][1]['id'] = 'other-stories'
        receipt['files'][0]['context']['folders'][2]['parent_id'] = 'other-stories'
        self.assertEqual(app.finish(plan, receipt)['counts']['failed'], 1)

    def test_source_changed_after_plan_is_not_success(self):
        self.write()
        plan = self.plan()
        receipt = self.receipt(plan)
        self.write(content=b'edited during copy')
        self.assertEqual(app.finish(plan, receipt)['counts']['failed'], 1)

    def test_raw_plugin_bytes_are_fingerprinted(self):
        content = b'\x00\xff binary\n'
        result = app.fingerprint({'mime_type':'application/octet-stream', 'file_size_bytes':len(content),
                                  'b64_string':base64.b64encode(content).decode()})
        self.assertEqual(result, {'size':len(content),'md5':app.hashlib.md5(content).hexdigest()})

    def test_signed_artifact_stream_is_fingerprinted(self):
        content = b'raw data'
        with patch.object(app, 'urlopen', return_value=io.BytesIO(content)):
            result = app.fingerprint({'mime_type':'application/octet-stream','file_size_bytes':len(content),
                                      'file_uri':{'download_url':'https://artifact.example/temporary'}})
        self.assertEqual(result['md5'], app.hashlib.md5(content).hexdigest())

    def test_native_exports_truncation_and_missing_raw_data_rejected(self):
        for response in ({'mime_type':'application/vnd.google-apps.document'},
                         {'file_size_bytes':5,'b64_string':'YQ=='},
                         {'file_size_bytes':1}, {'file_size_bytes':1,'b64_string':'invalid!'}):
            with self.assertRaises(app.SyncError):
                app.fingerprint(response)

    def test_readonly_plugin_fails_before_tests(self):
        data = capabilities()
        data['operations']['update_binary'] = False
        with patch.object(app, 'run_tests') as tests:
            with self.assertRaises(app.SyncError):
                app.preflight(data)
        tests.assert_not_called()

    def test_daily_tests_run_once_and_next_day_again(self):
        date = datetime(2026,10,22,18,tzinfo=timezone(timedelta(hours=-7)))
        with patch.object(app,'now',return_value=date), patch.object(app,'run_tests') as tests:
            app.preflight(capabilities())
            app.preflight(capabilities())
            self.assertEqual(tests.call_count, 1)
            with patch.object(app,'now',return_value=date+timedelta(days=1)):
                app.preflight(capabilities())
            self.assertEqual(tests.call_count, 2)
        state = app.read_json(app.state_dir()/'connector-maintenance.json')
        self.assertNotIn('packages_updated_at', state)
        self.assertIn('harness', state['dependency_status'])

    def test_failed_tests_are_retried(self):
        with patch.object(app,'run_tests',side_effect=app.SyncError('failure')):
            with self.assertRaises(app.SyncError):
                app.preflight(capabilities())
        self.assertEqual(app.read_json(app.state_dir()/'connector-maintenance.json')['test_status'], 'failed')
        with patch.object(app,'run_tests') as tests:
            self.assertTrue(app.preflight(capabilities())['ready'])
            tests.assert_called_once()

    def test_cli_scan_bind_plan_finish(self):
        self.write()
        context_file, manifest, remote, plan_file, receipts = [self.home/name for name in ('context.json','local.json','remote.json','plan.json','receipts.json')]
        app.write_json(context_file, context())
        self.assertEqual(app.main(['bind','stories','--context',str(context_file)]), 0)
        self.assertEqual(app.main(['scan','stories','--output',str(manifest)]), 0)
        app.write_json(remote, {'context':context(),'complete':True,'files':[]})
        self.assertEqual(app.main(['plan','--local',str(manifest),'--remote',str(remote),'--output',str(plan_file)]), 0)
        app.write_json(receipts, self.receipt(app.read_json(plan_file)))
        self.assertEqual(app.main(['finish','--plan',str(plan_file),'--receipts',str(receipts)]), 0)

    def test_unmap_does_not_delete_files(self):
        self.write()
        app.bind('stories', context())
        self.assertEqual(app.main(['unmap','stories']), 0)
        self.assertTrue((self.source/'report.txt').exists())
        self.assertEqual(app.mappings()['mappings'], {})

    def test_no_credentials_or_direct_drive_network_needed_for_planning(self):
        self.write()
        with patch.object(app,'urlopen',side_effect=AssertionError('network forbidden')), patch('subprocess.run',side_effect=AssertionError('process forbidden')):
            plan = self.plan()
            self.assertEqual(plan['actions'][0]['action'], 'upload')


if __name__ == '__main__':
    unittest.main()
