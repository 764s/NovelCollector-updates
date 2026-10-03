"""Trusted public-repository workflow helper: download a draft or promote it.

Only five named distribution assets are accepted. This file does not execute
application code; the separate read-only test jobs do that with no credentials.
"""
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import traceback

REPOSITORY = '764s/NovelCollector-updates'


def gh(*args):
    result = subprocess.run(['gh', *args], capture_output=True, timeout=180)
    if result.returncode:
        raise RuntimeError('GitHub distribution operation failed')
    return result.stdout


def candidate(tag):
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError('Expected a version tag')
    repo = json.loads(gh('api', 'repos/' + REPOSITORY))
    if repo.get('private') is not False:
        raise ValueError('Distribution repository must remain public')
    version = tag[1:]
    names = {'NovelCollector-' + version + suffix for suffix in
             ('-app-only.zip', '-app-only.zip.sha256', '-windows-x64.zip', '-linux-x64.tar.gz')}
    names.add('SHA256SUMS.txt')
    releases = json.loads(gh('api', 'repos/' + REPOSITORY + '/releases?per_page=100'))
    release = next((item for item in releases if item.get('tag_name') == tag), None)
    if release is None or release.get('prerelease'):
        raise ValueError('Missing stable candidate')
    assets = release.get('assets', [])
    if len(assets) != 5 or {a['name'] for a in assets} != names:
        raise ValueError('Expected exactly five release assets')
    for asset in assets:
        if (asset.get('state') != 'uploaded' or not 0 < asset.get('size', 0) <= 64 * 1024 * 1024
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', asset.get('digest', ''))):
            raise ValueError('Invalid asset digest or size')
    return release


def staged_files(tag):
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise ValueError('Expected a version tag')
    root = Path('incoming')
    manifest = json.loads((root / 'candidate.json').read_text(encoding='utf-8'))
    version = tag[1:]
    names = {'NovelCollector-' + version + suffix for suffix in
             ('-app-only.zip', '-app-only.zip.sha256', '-windows-x64.zip', '-linux-x64.tar.gz')} | {'SHA256SUMS.txt'}
    if manifest.get('tag') != tag or not re.fullmatch('[0-9a-f]{40}', manifest.get('source_commit', '')):
        raise ValueError('Candidate source identity mismatch')
    records = manifest.get('assets', [])
    if len(records) != 5 or {a['name'] for a in records} != names:
        raise ValueError('Expected five distribution files')
    for item in records:
        path = root / item['name']
        if path.is_symlink() or not 0 < path.stat().st_size <= 64 * 1024 * 1024:
            raise ValueError('Unsafe candidate file')
        data = path.read_bytes()
        if len(data) != item['bytes'] or sha256(data).hexdigest() != item['sha256']:
            raise ValueError('Candidate file digest mismatch: ' + item['name'])
    return manifest


def download_staged(tag):
    manifest = staged_files(tag)
    fingerprint = sha256(json.dumps(sorted((a['name'], a['bytes'], 'sha256:' + a['sha256']) for a in manifest['assets'])).encode()).hexdigest()
    Path('incoming').rename('candidate')
    if os.environ.get('GITHUB_OUTPUT'):
        platform = 'windows' if os.name == 'nt' else 'linux'
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
            stream.write(platform + '_fingerprint=' + fingerprint + '\n')


def upload_candidate(tag):
    manifest = staged_files(tag)
    root = Path('incoming')
    records = manifest['assets']
    names = {a['name'] for a in records}
    version = tag[1:]
    repo = json.loads(gh('api', 'repos/' + REPOSITORY))
    if repo.get('private') is not False:
        raise ValueError('Distribution must be public')
    releases = json.loads(gh('api', 'repos/' + REPOSITORY + '/releases?per_page=100'))
    existing = next((a for a in releases if a.get('tag_name') == tag), None)
    marker = '<!-- source-commit: ' + manifest['source_commit'] + ' -->'
    if existing and (not existing.get('draft') or marker not in (existing.get('body') or '')):
        raise ValueError('Refusing to replace a published release or unrelated draft')
    notes = manifest.get('notes')
    if not isinstance(notes, str) or marker not in notes or len(notes) > 65536:
        raise ValueError('Invalid candidate release notes')
    notes_path = root / 'release-notes.md'
    notes_path.write_text(notes, encoding='utf-8')
    if not existing:
        gh('release', 'create', tag, '--repo', REPOSITORY, '--target', 'main', '--draft',
           '--title', 'NovelCollector ' + version, '--notes-file', str(notes_path))
    gh('release', 'upload', tag, '--repo', REPOSITORY, *(str(root / name) for name in sorted(names)), '--clobber')
    uploaded = candidate(tag)
    actual = {a['name']:(a['size'], a['digest']) for a in uploaded['assets']}
    expected = {a['name']:(a['bytes'], 'sha256:' + a['sha256']) for a in records}
    if actual != expected:
        raise ValueError('Uploaded assets differ from staged candidate')
    print(json.dumps({'uploaded': tag, 'assets': len(records)}))


def main():
    action, tag = sys.argv[1:]
    if action == 'upload':
        upload_candidate(tag)
        return
    if action == 'download':
        download_staged(tag)
        return
    release = candidate(tag)
    fingerprint = sha256(json.dumps(sorted((a['name'], a['size'], a['digest']) for a in release['assets'])).encode()).hexdigest()
    if action in ('promote', 'receipt'):
        if any(os.environ.get(name) != fingerprint for name in ('WINDOWS_FINGERPRINT', 'LINUX_FINGERPRINT')):
            raise ValueError('Candidate changed after native verification')
    if action == 'promote':
        # Called only by the job depending on both native preflight jobs.
        # Re-running a successful workflow may verify the same immutable release.
        if release['draft']:
            gh('release', 'edit', tag, '--repo', REPOSITORY, '--draft=false', '--latest')
        print(json.dumps({'published': tag, 'repository': REPOSITORY}))
    elif action == 'receipt':
        if release['draft']:
            raise ValueError('Release was not published')
        report = {'version': tag[1:], 'status': 'success', 'repository': REPOSITORY,
                  'run_url': 'https://github.com/' + REPOSITORY + '/actions/runs/' + os.environ.get('GITHUB_RUN_ID', ''),
                  'candidate_fingerprint': fingerprint,
                  'windows_anonymous_update_verified': True, 'linux_anonymous_update_verified': True,
                  'source_repository_changed_visibility': False,
                  'assets': [{key:a[key] for key in ('name', 'size', 'digest')} for a in release['assets']]}
        Path('release-status.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    else:
        raise ValueError('Unknown action')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        detail = traceback.format_exc()
        for name in ('GH_TOKEN', 'GITHUB_TOKEN'):
            if os.environ.get(name):
                detail = detail.replace(os.environ[name], '[redacted]')
        detail = re.sub(r'https?://\S+', '[URL redacted]', detail)
        safe = detail.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
        print('::error::' + safe.encode('ascii', 'backslashreplace').decode('ascii'), flush=True)
        raise SystemExit(1)
