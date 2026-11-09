#!/usr/bin/env python3
"""Local planning and verification for the harness-driven local-to-gdrive skill.

Google Drive operations belong to the agent's connected plugin. This helper
never authenticates or calls Drive APIs. Its fingerprint command can stream
a short-lived raw-file artifact returned by the plugin to calculate MD5.
"""
import argparse
import base64
from contextlib import closing
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import uuid
from urllib.parse import urlparse
from urllib.request import urlopen


class SyncError(Exception):
    pass


def now():
    return datetime.now().astimezone()


def state_dir():
    return Path.home() / '.config/codex2gdrive'


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise SyncError(f'Cannot read JSON file {path}: {exc}') from exc


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def log_event(event, **details):
    folder = state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'connector-sync.log').open('a') as handle:
        handle.write(json.dumps({'time': now().isoformat(), 'event': event, **details}) + '\n')


def relative(value):
    if not isinstance(value, str) or not value or any(c in value for c in ('\\', ':', '\x00', '\n', '\r')):
        raise SyncError('Use a relative folder path such as sample-project or work/notes.')
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or str(path) == '.':
        raise SyncError('Paths must stay within their root and name a specific folder or file.')
    return str(path)


def source_folder(value):
    key = relative(value)
    if '.git' in PurePosixPath(key).parts:
        raise SyncError('Git metadata inside .git is excluded from copying.')
    root = Path.home() / 'Documents'
    path = root / key
    if not path.resolve().is_relative_to(root.resolve()):
        raise SyncError('Source must stay inside Documents.')
    current = root
    for part in PurePosixPath(key).parts:
        current = current / part
        if current.is_symlink():
            raise SyncError('Source folders may not traverse symbolic links.')
    if not path.is_dir():
        raise SyncError(f'Source folder not found: {path}')
    return key, path


def md5(path):
    digest = hashlib.md5(usedforsecurity=False)
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def file_snapshot(path):
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise SyncError('Not a regular file.')
    digest = md5(path)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise SyncError(f'Source changed while hashing: {path}')
    return {'size': after.st_size, 'md5': digest}


def fingerprint(response):
    """Hash complete raw bytes supplied by the plugin, without Drive credentials."""
    response = response.get('structuredContent', response)
    if response.get('mime_type', '').startswith('application/vnd.google-apps.'):
        raise SyncError('Native Google document exports cannot represent an exact raw-file copy.')
    expected = response.get('file_size_bytes')
    encoded = response.get('b64_string')
    digest, size = hashlib.md5(usedforsecurity=False), 0
    if encoded is not None:
        try:
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError) as exc:
            raise SyncError('Invalid raw-file base64 returned by plugin.') from exc
        digest.update(content)
        size = len(content)
    else:
        url = response.get('file_uri', {}).get('download_url')
        if not isinstance(url, str) or urlparse(url).scheme != 'https':
            raise SyncError('Plugin did not return a usable raw-file artifact.')
        # Use only the short-lived URL returned by the plugin's raw-file fetch.
        # It is never saved to state/logs or reused as a Drive credential.
        with urlopen(url, timeout=60) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(chunk)
                size += len(chunk)
    if expected is None or int(expected) != size:
        raise SyncError('Raw download size is missing or does not match the complete file.')
    return {'size': size, 'md5': digest.hexdigest()}


def scan(value, *, mirror=False):
    key, folder = source_folder(value)
    files, folders, skipped, ignored, empty = [], [], [], [], []
    def walk_error(exc):
        raise SyncError(f'Cannot scan source: {exc}')
    for directory, dirs, names in os.walk(folder, followlinks=False, onerror=walk_error):
        if Path(directory) != folder:
            folders.append(relative(Path(directory).relative_to(folder).as_posix()))
        for name in sorted(dirs[:]):
            path = Path(directory) / name
            if name == '.git' or (mirror and name.startswith('.')):
                dirs.remove(name)
                ignored.append(path.relative_to(folder).as_posix())
            elif path.is_symlink():
                dirs.remove(name)
                skipped.append(str(path.relative_to(folder)))
        dirs.sort()
        for name in sorted(names):
            path = Path(directory) / name
            key_path = path.relative_to(folder).as_posix()
            if name == '.git' or (mirror and name.startswith('.')):
                ignored.append(key_path)
                continue
            # Connector paths must be unambiguous and representable by the helper.
            relative(key_path)
            if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
                skipped.append(key_path)
                continue
            snapshot = file_snapshot(path)
            if mirror and snapshot['size'] == 0:
                empty.append(key_path)
                continue
            files.append({'path': key_path, 'local_path': str(path), **snapshot})
    ignored_key = 'ignored_hidden' if mirror else 'ignored_git_metadata'
    manifest = {'version': 1, 'source': key, 'source_root': str(folder), 'files': files, 'folders': folders,
                'skipped_links_or_special': skipped, ignored_key: ignored}
    if mirror:
        manifest['ignored_empty'] = empty
    return manifest


def mappings():
    path = state_dir() / 'connector-mappings.json'
    config = read_json(path) if path.exists() else {'version': 1, 'mappings': {}}
    if not isinstance(config, dict) or config.get('version') != 1 or not isinstance(config.get('mappings'), dict):
        raise SyncError('Invalid connector mapping configuration. Existing files were left untouched.')
    return config


def scope(value, destination=None):
    key, folder = source_folder(value)
    mapping = mappings()['mappings'].get(key)
    requested = relative(destination) if destination is not None else None
    if mapping and requested is not None and requested != mapping['destination_path']:
        raise SyncError('Requested destination differs from the saved mapping. Rebind explicitly.')
    target = mapping['destination_path'] if mapping else requested or key
    return {'source_path': str(folder), 'destination_path': f'My Drive/Codex/{target}',
            'mapping': mapping}


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise SyncError(f'Missing {label}.')
    return value


def checksum_cache(operation, request, *, uploaded=False):
    account = nonempty(request.get('account_id'), 'connected account ID')
    rows = []
    for item in request['files']:
        file_id = nonempty(item.get('file_id'), 'Drive file ID')
        revision = nonempty(item.get('revision_id'), 'current revision ID')
        size = item.get('size')
        if type(size) is not int or not 0 <= size <= 2**63 - 1:
            raise SyncError('Checksum cache requires an observed byte size.')
        row = (account, file_id, revision, size)
        if operation == 'remember':
            digest = item.get('md5')
            if not isinstance(digest, str) or not re.fullmatch(r'[0-9a-fA-F]{32}', digest):
                raise SyncError('Checksum cache requires a verified MD5.')
            if not uploaded and item.get('revision_after') != revision:
                raise SyncError('Drive revision changed during the checksum read.')
            row += (digest.lower(),)
        rows.append(row)
    path = state_dir() / 'connector-checksums.sqlite3'
    if operation == 'lookup':
        hits = {}
        if rows and path.exists():
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as database:
                for row in rows:
                    cached = database.execute(
                        'SELECT md5 FROM checksums WHERE account_id=? AND file_id=? AND revision_id=? AND size=?',
                        row,
                    ).fetchone()
                    if cached:
                        hits[row[1]] = {'revision_id': row[2], 'size': row[3], 'md5': cached[0]}
        return {'hits': hits}
    if rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
        with closing(sqlite3.connect(path)) as database, database:
            database.execute('''
                CREATE TABLE IF NOT EXISTS checksums (
                    account_id TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    revision_id TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    md5 TEXT NOT NULL,
                    PRIMARY KEY (account_id, file_id)
                )
            ''')
            database.executemany('''
                INSERT INTO checksums VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id, file_id) DO UPDATE SET
                    revision_id=excluded.revision_id, size=excluded.size, md5=excluded.md5
            ''', rows)
    return {'stored': len(rows)}


def validate_context(context):
    """Validate normalized folder metadata collected by the harness plugin."""
    if context.get('provider') != 'google-drive':
        raise SyncError('Context must come from the Google Drive plugin.')
    account = nonempty(context.get('account_id'), 'plugin account identifier')
    chain = context.get('folders', [])
    if not isinstance(chain, list) or len(chain) < 2:
        raise SyncError('Provide Codex and at least one destination subfolder.')
    parent = 'root'
    ids, names = set(), []
    for index, item in enumerate(chain):
        ident = nonempty(item.get('id'), 'folder ID')
        name = nonempty(item.get('name'), 'folder name')
        if ident in ids or item.get('parent_id') != parent or item.get('kind') != 'folder':
            raise SyncError('Folder ancestry is invalid or ambiguous.')
        if '/' in name or relative(name) != name:
            raise SyncError('Invalid folder name in plugin context.')
        if index == 0 and name != 'Codex':
            raise SyncError('Destination must be beneath My Drive/Codex.')
        ids.add(ident)
        names.append(name)
        parent = ident
    return {'account_id': account, 'root_id': chain[0]['id'],
            'destination_id': chain[-1]['id'], 'destination_path': '/'.join(names[1:])}


def bind(value, context):
    key, _ = source_folder(value)
    mapping = validate_context(context)
    config = mappings()
    config['mappings'][key] = mapping
    write_json(state_dir() / 'connector-mappings.json', config)
    log_event('mapping_saved', source=key, destination_id=mapping['destination_id'])
    return {'source': key, **mapping}


def capability_check(capabilities, *, mirror=False):
    if capabilities.get('provider') != 'google-drive':
        raise SyncError('Connect the harness Google Drive plugin first.')
    nonempty(capabilities.get('account_id'), 'connected account identifier')
    required = ('list_children', 'get_metadata', 'create_folder', 'upload_binary', 'update_binary')
    if mirror:
        required += ('move_items', 'read_revision')
    else:
        required += ('read_binary',)
    operations = capabilities.get('operations', {})
    missing = [name for name in required if operations.get(name) is not True]
    if missing:
        raise SyncError('Google Drive plugin lacks required operations: ' + ', '.join(missing))
    return True


def run_tests():
    skill = Path(__file__).resolve().parents[1]
    tests = skill / 'tests'
    if not (tests / 'test_drive_sync.py').is_file():
        raise SyncError('Bundled test suite is missing.')
    result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(tests), '-v'],
                            cwd=skill, text=True, capture_output=True, check=False)
    log_event('test_output', returncode=result.returncode, output=result.stdout + result.stderr)
    if result.returncode:
        raise SyncError('Tests failed. See connector-sync.log. No upload is allowed.')


def preflight(capabilities, *, mirror=False):
    # Connection and capabilities can change even within the same day.
    capability_check(capabilities, mirror=mirror)
    folder = state_dir()
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / 'connector-maintenance.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        path = folder / 'connector-maintenance.json'
        state = read_json(path) if path.exists() else {}
        if not isinstance(state, dict):
            raise SyncError('Invalid maintenance state.')
        stamp = state.get('tests_passed_at')
        try:
            current = now()
            passed_today = bool(stamp) and datetime.fromisoformat(stamp).astimezone(current.tzinfo).date() == current.date()
        except (ValueError, TypeError) as exc:
            raise SyncError('Invalid maintenance timestamp.') from exc
        if not passed_today:
            state.update({'dependencies_checked_at': now().isoformat(),
                          'dependency_status': 'stdlib-only; Drive plugin updates managed by harness'})
            log_event('dependency_check', status=state['dependency_status'])
            try:
                run_tests()
            except (OSError, SyncError) as exc:
                state.update({'tests_ran_at': now().isoformat(), 'test_status': 'failed'})
                write_json(path, state)
                log_event('tests_failed', error=str(exc))
                raise
            state.update({'tests_ran_at': now().isoformat(), 'tests_passed_at': now().isoformat(), 'test_status': 'passed'})
            write_json(path, state)
            log_event('tests_passed')
    return {'ready': True, 'maintenance': state}


def mirror_actions(manifest, by_path):
    source_kinds = dict.fromkeys(manifest['folders'], 'folder')
    source_kinds.update((file['path'], 'file') for file in manifest['files'])
    excluded = set(manifest['skipped_links_or_special']) | set(manifest['ignored_empty'])
    before, after = [], []
    covered = set()
    for path, item in sorted(by_path.items(), key=lambda entry: PurePosixPath(entry[0]).parts):
        parts = PurePosixPath(path)
        parent = str(parts.parent)
        if parent != '.' and by_path.get(parent, {}).get('kind') != 'folder':
            raise SyncError(f'Remote inventory is missing a parent folder for {path}.')
        if path in excluded or any(
                str(parent) in covered or str(parent) in excluded for parent in parts.parents):
            continue
        expected = source_kinds.get(path)
        if expected == item['kind']:
            continue
        if expected is None and item['kind'] == 'file' and item.get('size') == 0:
            continue
        actions = after if expected is None else before
        actions.append({'path': path, 'remote_id': item['id'], 'kind': item['kind']})
        covered.add(path)

    replaced = {entry['path'] for entry in before}
    folders = [path for path in manifest['folders'] if path not in by_path or path in replaced]
    return {'create_folders': folders, 'recover_before_copy': before, 'recover_after_copy': after,
            'ignored_empty': manifest['ignored_empty']}


def build_plan(manifest, remote, *, mirror=False):
    # Re-scan so hand-edited or stale manifests cannot silently select other files.
    fresh = scan(manifest['source'], mirror=mirror)
    matches = inventory_digest(fresh) == inventory_digest(manifest) if mirror else fresh == manifest
    if not matches:
        raise SyncError('Local inventory changed. Scan again before planning.')
    manifest = fresh
    key = manifest['source']
    saved = mappings()['mappings']
    mapping = saved.get(key)
    if mapping is None:
        raise SyncError('No saved destination mapping. Bind a plugin-verified destination first.')
    context = validate_context(remote['context'])
    if context != mapping:
        raise SyncError('Plugin account or folder ancestry differs from the saved mapping. Rebind explicitly.')
    if mirror:
        for source, other in saved.items():
            if source == key or other['account_id'] != mapping['account_id']:
                continue
            if other['destination_id'] == mapping['destination_id'] or (
                    other['root_id'] == mapping['root_id'] and
                    other['destination_path'].startswith(mapping['destination_path'] + '/')):
                raise SyncError(f'Mirror destination overlaps the saved destination for {source}.')
    if remote.get('complete') is not True:
        raise SyncError('Remote inventory must include every page and subfolder.')
    by_path, ids = {}, set()
    for item in remote.get('files', []):
        path = relative(item['path'])
        if mirror and any(part.startswith('.') for part in PurePosixPath(path).parts):
            continue
        ident = nonempty(item.get('id'), 'remote file ID')
        if path in by_path or ident in ids:
            raise SyncError(f'Duplicate remote path or ID: {path}')
        if item.get('kind') not in ('file', 'folder'):
            raise SyncError(f'Unsupported remote object: {path}')
        by_path[path] = item
        ids.add(ident)
    mirror_plan = mirror_actions(manifest, by_path) if mirror else {}
    for entry in mirror_plan.get('recover_before_copy', []):
        del by_path[entry['path']]
    hits = {}
    if mirror:
        candidates = [by_path[file['path']] for file in manifest['files'] if file['path'] in by_path]
        revisions = [{'file_id': item['id'], 'revision_id': item['revision_id'], 'size': item['size']}
                     for item in candidates if valid_revision(item)]
        hits = checksum_cache('lookup', {'account_id': mapping['account_id'], 'files': revisions})['hits']
    actions, errors = [], []
    for file in manifest['files']:
        path = file['path']
        existing = by_path.get(path)
        error = None
        for parent in PurePosixPath(path).parents:
            blocker = by_path.get(str(parent))
            if blocker and blocker['kind'] != 'folder':
                error = f'Destination file blocks folder {parent}'
        if existing and existing['kind'] != 'file':
            error = 'Destination path is a folder'
        elif existing and mirror:
            if not valid_revision(existing):
                error = 'Destination has no usable current revision or size'
        elif existing:
            digest = existing.get('md5')
            size = existing.get('size')
            if not isinstance(size, int) or size < 0 or not isinstance(digest, str) or not re.fullmatch(r'[0-9a-fA-F]{32}', digest):
                error = 'Destination has no usable size or MD5'
        if error:
            errors.append({'path': path, 'error': error})
            continue
        reference = hits.get(existing['id']) if mirror and existing else existing
        same = reference is not None and reference['size'] == file['size'] and reference['md5'].lower() == file['md5']
        action = {**file, 'action': 'skip' if same else ('update' if existing else 'upload'),
                  'remote_id': existing['id'] if existing else None}
        if mirror and existing:
            action['remote_revision_id'] = existing['revision_id']
        actions.append(action)
    if mirror:
        mirror_plan['source_snapshot'] = inventory_digest(fresh)
    return {'version': 1, 'run_id': uuid.uuid4().hex, 'created_at': now().isoformat(),
            'source': key, 'mapping': mapping, 'actions': actions, 'errors': errors, **mirror_plan}


def valid_revision(item):
    revision, size = item.get('revision_id'), item.get('size')
    return isinstance(revision, str) and bool(revision.strip()) and type(size) is int and 0 <= size <= 2**63 - 1


def inventory_digest(manifest):
    # Empty paths matter because their Drive counterparts are protected from recovery.
    inventory = {key: manifest[key] for key in ('files', 'folders', 'skipped_links_or_special', 'ignored_empty')}
    return hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()


def finish(plan, receipts):
    """Record success only after a plugin read verifies the remote bytes."""
    if plan.get('version') != 1 or not re.fullmatch(r'[a-f0-9]{32}', plan.get('run_id', '')):
        raise SyncError('Invalid plan.')
    key, source = source_folder(plan['source'])
    if mappings()['mappings'].get(key) != plan['mapping']:
        raise SyncError('Mapping changed after planning.')
    if receipts.get('run_id') != plan['run_id']:
        raise SyncError('Receipts are from another run.')
    mirror = 'source_snapshot' in plan
    if mirror and inventory_digest(scan(key, mirror=True)) != plan['source_snapshot']:
        raise SyncError('Source inventory changed during the mirror run. Scan and retry.')
    counts = {'uploaded': 0, 'overwritten': 0, 'skipped': 0, 'failed': len(plan['errors'])}
    evidence = {}
    for item in receipts.get('files', []):
        name = relative(item['path'])
        if name in evidence:
            raise SyncError('Duplicate upload receipt.')
        evidence[name] = item
    planned_paths = {a['path'] for a in plan['actions'] if a['action'] != 'skip'}
    if set(evidence) - planned_paths:
        raise SyncError('Receipt contains an unplanned upload.')
    failures = list(plan['errors'])
    baselines = []
    for item in plan['actions']:
        path = relative(item['path'])
        if path != item['path']:
            raise SyncError('Invalid action path.')
        if item['action'] == 'skip':
            counts['skipped'] += 1
            continue
        try:
            receipt = evidence.get(path)
            if not receipt or receipt.get('status') != 'verified':
                raise SyncError((receipt or {}).get('error', 'No verified upload receipt'))
            context = validate_context(receipt['context'])
            if context['account_id'] != plan['mapping']['account_id'] or context['root_id'] != plan['mapping']['root_id']:
                raise SyncError('Upload receipt belongs to another Drive account or root.')
            # Context includes the full parent chain through any nested directories.
            parent = str(PurePosixPath(path).parent)
            expected_path = plan['mapping']['destination_path'] + ('' if parent == '.' else '/' + parent)
            if context['destination_path'] != expected_path:
                raise SyncError('Upload receipt points to the wrong folder.')
            folder_chain = receipt['context']['folders']
            mapping_depth = len(PurePosixPath(plan['mapping']['destination_path']).parts)
            if folder_chain[mapping_depth]['id'] != plan['mapping']['destination_id']:
                raise SyncError('Upload receipt is outside the saved destination ID.')
            verified = receipt['remote']
            nonempty(verified.get('id'), 'uploaded file ID')
            if verified.get('name') != PurePosixPath(path).name:
                raise SyncError('Uploaded file name differs from the planned source.')
            if verified.get('parent_id') != context['destination_id']:
                raise SyncError('Uploaded file parent does not match verified ancestry.')
            if item['action'] == 'update' and verified['id'] != item['remote_id']:
                raise SyncError('Update created a duplicate instead of replacing the original file.')
            if mirror:
                if not valid_revision(verified) or verified['size'] != item['size']:
                    raise SyncError('Uploaded file has no confirmed revision or its size differs from the source.')
                if verified['revision_id'] == item.get('remote_revision_id'):
                    raise SyncError('Drive has not reported the updated revision.')
            elif verified.get('md5', '').lower() != item['md5'] or verified.get('size') != item['size']:
                raise SyncError('Uploaded bytes do not match the planned source.')
            local = source / path
            if local.is_symlink() or not local.resolve().is_relative_to(source.resolve()):
                raise SyncError('Source path changed to a symbolic link.')
            if file_snapshot(local) != {'md5': item['md5'], 'size': item['size']}:
                raise SyncError('Local file changed during the run. Scan and retry.')
            if mirror:
                baselines.append({'file_id': verified['id'], 'revision_id': verified['revision_id'],
                                  'size': item['size'], 'md5': item['md5']})
            counts['overwritten' if item['action'] == 'update' else 'uploaded'] += 1
            log_event('file_verified', run_id=plan['run_id'], source=key, path=path, remote_id=verified['id'])
        except (SyncError, OSError, KeyError, IndexError, TypeError) as exc:
            counts['failed'] += 1
            failures.append({'path': path, 'error': str(exc)})
    if baselines:
        checksum_cache('remember', {'account_id': plan['mapping']['account_id'], 'files': baselines}, uploaded=True)
    result = {'run_id': plan['run_id'], 'source': key, 'counts': counts, 'errors': failures,
              'status': 'failed' if counts['failed'] else 'complete'}
    log_event('run_finished', **result)
    write_json(state_dir() / 'last-connector-run.json', result)
    return result


def verify_mirror(plan, remote):
    if 'source_snapshot' not in plan:
        raise SyncError('Final mirror verification requires a mirror plan.')
    manifest = scan(plan['source'], mirror=True)
    if inventory_digest(manifest) != plan['source_snapshot']:
        raise SyncError('Source inventory changed during the mirror run. Scan and retry.')
    current = build_plan(manifest, remote, mirror=True)
    if current['mapping'] != plan['mapping']:
        raise SyncError('Mapping changed after planning.')
    differences = [
        {'path': action['path'], 'reason': action['action']}
        for action in current['actions'] if action['action'] != 'skip'
    ]
    differences.extend({'path': path, 'reason': 'missing folder'} for path in current['create_folders'])
    for key in ('recover_before_copy', 'recover_after_copy'):
        differences.extend({'path': entry['path'], 'reason': 'unexpected Drive item'} for entry in current[key])
    differences.extend({'path': entry['path'], 'reason': entry['error']} for entry in current['errors'])
    return {'status': 'failed' if differences else 'complete', 'differences': differences}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    check = sub.add_parser('preflight')
    check.add_argument('--capabilities', required=True)
    check.add_argument('--mirror', action='store_true')
    resolve = sub.add_parser('scope')
    resolve.add_argument('source')
    resolve.add_argument('--destination', help='Path relative to My Drive/Codex')
    inventory = sub.add_parser('scan')
    inventory.add_argument('source')
    inventory.add_argument('--output', required=True)
    inventory.add_argument('--mirror', action='store_true')
    digest = sub.add_parser('fingerprint')
    digest.add_argument('--raw-response', required=True)
    digest.add_argument('--output', required=True)
    cache = sub.add_parser('checksum-cache')
    cache.add_argument('operation', choices=('lookup', 'remember'))
    cache.add_argument('--input', required=True)
    binding = sub.add_parser('bind')
    binding.add_argument('source')
    binding.add_argument('--context', required=True)
    sub.add_parser('mappings')
    remove = sub.add_parser('unmap')
    remove.add_argument('source')
    plan = sub.add_parser('plan')
    plan.add_argument('--local', required=True)
    plan.add_argument('--remote', required=True)
    plan.add_argument('--output', required=True)
    plan.add_argument('--mirror', action='store_true')
    record = sub.add_parser('finish')
    record.add_argument('--plan', required=True)
    record.add_argument('--receipts', required=True)
    verify = sub.add_parser('verify-mirror')
    verify.add_argument('--plan', required=True)
    verify.add_argument('--remote', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'preflight':
            result = preflight(read_json(args.capabilities), mirror=args.mirror)
        elif args.command == 'scope':
            result = scope(args.source, args.destination)
        elif args.command == 'scan':
            result = scan(args.source, mirror=args.mirror)
            write_json(args.output, result)
        elif args.command == 'fingerprint':
            result = fingerprint(read_json(args.raw_response))
            write_json(args.output, result)
        elif args.command == 'checksum-cache':
            result = checksum_cache(args.operation, read_json(args.input))
        elif args.command == 'bind':
            result = bind(args.source, read_json(args.context))
        elif args.command == 'mappings':
            result = mappings()
        elif args.command == 'unmap':
            key = relative(args.source)
            config = mappings()
            if key not in config['mappings']:
                raise SyncError('Mapping not found.')
            del config['mappings'][key]
            write_json(state_dir() / 'connector-mappings.json', config)
            result = {'removed': key, 'files_deleted': False}
        elif args.command == 'plan':
            result = build_plan(read_json(args.local), read_json(args.remote), mirror=args.mirror)
            write_json(args.output, result)
        elif args.command == 'finish':
            result = finish(read_json(args.plan), read_json(args.receipts))
        else:
            result = verify_mirror(read_json(args.plan), read_json(args.remote))
        print(json.dumps(result, indent=2))
        return 1 if result.get('status') == 'failed' else 0
    except (SyncError, OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
