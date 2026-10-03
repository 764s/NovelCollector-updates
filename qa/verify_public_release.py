"""Portable release acceptance: offline candidate or anonymous published update.

Self-contained so the public distribution repository can run it without private
source access. Online mode deliberately starts the SAME client with a lower
version label; it is not a claim to upgrade an old, private-source client.
"""
import argparse
import base64
from contextlib import suppress
from hashlib import sha256
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import traceback
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
import zipfile

REPOSITORY = '764s/NovelCollector-updates'


def fetch(url, limit, accept='application/vnd.github+json'):
    request = Request(url, headers={'Accept': accept, 'User-Agent': 'NovelCollector-Public-Acceptance'})
    assert request.get_header('Authorization') is None
    with urlopen(request, timeout=120) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('Response too large')
    return data


def unpack(archive, directory):
    def check(name):
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name:
            raise ValueError('Unsafe archive path')
    if archive.suffix == '.zip':
        with zipfile.ZipFile(archive) as bundle:
            for item in bundle.infolist():
                check(item.filename)
                if stat.S_ISLNK(item.external_attr >> 16):
                    raise ValueError('Unexpected Windows symlink')
            bundle.extractall(directory)
    else:
        with tarfile.open(archive) as bundle:
            for item in bundle.getmembers():
                check(item.name)
            bundle.extractall(directory, filter='data')
    roots = list(directory.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise ValueError('Expected one package root')
    return roots[0]


def verify_package(home):
    listed = set()
    for line in (home / 'MANIFEST.SHA256').read_text(encoding='utf-8').splitlines():
        digest, relative = line.split('  ', 1)
        target = home / relative
        if not target.resolve().is_relative_to(home.resolve()) or target.is_symlink():
            raise ValueError('Unsafe manifest path')
        if not re.fullmatch('[0-9a-f]{64}', digest) or sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError('Full package manifest mismatch')
        listed.add(relative)
    actual = {p.relative_to(home).as_posix() for p in home.rglob('*') if p.is_file() and not p.is_symlink()}
    if actual != listed | {'MANIFEST.SHA256'}:
        raise ValueError('Unlisted package files')


def api(address, route, body=None):
    base, token = address
    headers = {'X-App-Token': token, 'Content-Type': 'application/json'}
    request = Request(base + route, headers=headers,
                      data=None if body is None else json.dumps(body).encode())
    with urlopen(request, timeout=150) as response:
        return json.load(response)


def wait_state(log, version):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        matches = re.findall(r'(http://127\.0\.0\.1:\d+)/#token=([^\s]+)', log.read_text(encoding='utf-8', errors='replace')) if log.exists() else []
        for address in reversed(matches):
            with suppress(OSError, ValueError):
                state = api(address, '/api/state')
                if state['app']['version'] == version:
                    return address, state
        time.sleep(.15)
    details = re.sub(r'https?://\S+', '[address redacted]', log.read_text(encoding='utf-8', errors='replace'))
    raise RuntimeError('Application did not reach ' + version + ': ' + details[-3000:])


def next_archive(archive, version):
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read('manifest.json'))
        files = {name: bundle.read(name) for name in manifest['files']}
    files['app/main.py'] = files['app/main.py'].replace(
        ("VERSION = '" + manifest['version'] + "'").encode(), ("VERSION = '" + version + "'").encode())
    manifest.update(version=version, files={name: sha256(data).hexdigest() for name, data in files.items()})
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr('manifest.json', json.dumps(manifest))
        for name, data in files.items():
            bundle.writestr(name, data)
    return output.getvalue()


def check(args):
    platform = 'windows-x64' if os.name == 'nt' else 'linux-x64'
    with tempfile.TemporaryDirectory(prefix='novel-public-') as temp:
        root = Path(temp)
        published = None
        if args.online:
            published = json.loads(fetch('https://api.github.com/repos/' + REPOSITORY + '/releases/latest', 512 * 1024))
            if published.get('tag_name') != args.tag or published.get('draft') or published.get('prerelease'):
                raise ValueError('Expected exact stable public release')
            suffix = '.zip' if os.name == 'nt' else '.tar.gz'
            name = 'NovelCollector-' + args.tag[1:] + '-' + platform + suffix
            asset = next(a for a in published['assets'] if a['name'] == name)
            data = fetch('https://api.github.com/repos/' + REPOSITORY + '/releases/assets/' + str(asset['id']),
                         64 * 1024 * 1024, 'application/octet-stream')
            if len(data) != asset['size'] or 'sha256:' + sha256(data).hexdigest() != asset['digest']:
                raise ValueError('Public full package differs from release digest')
            archive = root / name
            archive.write_bytes(data)
        else:
            archive = args.package.resolve()
        home = unpack(archive, root / 'unpacked')
        verify_package(home)
        pointer = json.loads((home / 'active-release.json').read_text(encoding='utf-8'))
        current = pointer['version']
        if args.online and current != args.tag[1:]:
            raise ValueError('Wrong public package version')
        before_version = current
        if args.online:
            # Exercise the actual online prepare/install routes against the real
            # stable release. Only this temporary test copy has a lower label.
            parts = list(map(int, current.split('.')))
            before_version = '.'.join(map(str, [*parts[:2], parts[2] - 1])) if parts[2] else '2.2.0'
            assert tuple(map(int, before_version.split('.'))) < tuple(parts)
            app = home / pointer['path']
            lowered = home / 'releases' / before_version / 'app'
            lowered.parent.mkdir(parents=True)
            app.rename(lowered)
            main = lowered / 'main.py'
            main.write_bytes(main.read_bytes().replace(("VERSION = '" + current + "'").encode(),
                                                      ("VERSION = '" + before_version + "'").encode()))
            pointer.update(version=before_version, path='releases/' + before_version + '/app', previous=None)
            (home / 'active-release.json').write_text(json.dumps(pointer), encoding='utf-8')
        env = {**os.environ, 'LOCALAPPDATA': str(root / '用户数据'), 'XDG_DATA_HOME': str(root / '用户数据'),
               'PATH': os.path.join(os.environ['SystemRoot'], 'System32') if os.name == 'nt' else '/no-cli'}
        for name in tuple(env):
            if name.startswith(('GH_', 'GITHUB_')) or name in ('PYTHONHOME', 'PYTHONPATH'):
                env.pop(name, None)
        launcher = home / ('NovelCollector.exe' if os.name == 'nt' else 'NovelCollector')
        python = home / ('runtime/python.exe' if os.name == 'nt' else 'runtime/bin/python3')
        log, address, process = root / 'app.log', None, None

        def start(wanted):
            nonlocal process, address
            with log.open('wb') as stream:
                process = subprocess.Popen([str(launcher), '--no-browser', '--port', '0'],
                                           cwd=home, env=env, stdout=stream, stderr=stream)
            address, state = wait_state(log, wanted)
            return state

        def stop():
            nonlocal address
            if address:
                with suppress(OSError):
                    api(address, '/api/shutdown', {})
                address = None
            if process is not None:
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=15)
            time.sleep(.5)

        try:
            state = start(before_version)
            assert 'github_authorization' not in state['app']
            saved = api(address, '/api/collections', {'name': '公开更新验收', 'start_url': 'https://www.52shuku.net/',
                'keywords': [{'term': '修仙', 'weight': 4.25}]})
            note = Path(state['app']['data_dir']) / '只读备份验收.txt'
            note.write_bytes(b'read-only backup fixture')
            note.chmod(0o444)
            if args.online:
                found = api(address, '/api/updates/github/check', {})
                assert found['status'] == 'available' and found['version'] == current, found
                prepared = api(address, '/api/updates/github/prepare', {})
                target_version = current
            else:
                parts = current.split('.')
                target_version = '.'.join([*parts[:2], str(int(parts[2]) + 1)])
                prepared = api(address, '/api/updates/prepare', {'archive': base64.b64encode(next_archive(args.archive, target_version)).decode()})
            assert prepared['version'] == target_version
            installed = api(address, '/api/updates/install', {'stage_id': prepared['stage_id'], 'confirmed': True})
            assert Path(installed['backup_dir'], 'app.db').is_file()
            assert Path(installed['backup_dir'], note.name).read_bytes() == note.read_bytes()
            address, upgraded = wait_state(log, target_version)
            assert upgraded['app']['data_dir'] == state['app']['data_dir']
            assert any(c['id'] == saved['id'] and c['keywords'] == saved['keywords'] for c in upgraded['collections'])
            if args.online:
                assert api(address, '/api/updates/github/check', {})['status'] == 'current'
                update = next(a for a in published['assets'] if a['name'].endswith('-app-only.zip'))
                with zipfile.ZipFile(io.BytesIO(fetch('https://api.github.com/repos/' + REPOSITORY + '/releases/assets/' + str(update['id']),
                                                    20 * 1024 * 1024, 'application/octet-stream'))) as bundle:
                    manifest = json.loads(bundle.read('manifest.json'))
                app = home / 'releases' / current / 'app'
                for relative, digest in manifest['files'].items():
                    assert sha256((app.parent / relative).read_bytes()).hexdigest() == digest
                stop()
                restarted = start(current)
                assert any(c['id'] == saved['id'] for c in restarted['collections'])
                assert api(address, '/api/updates/github/check', {})['status'] == 'current'
            else:
                rollback = api(address, '/api/updates/rollback', {'confirmed': True})
                assert Path(rollback['backup_dir'], 'app.db').is_file()
                address, restored = wait_state(log, current)
                assert any(c['id'] == saved['id'] for c in restored['collections'])
            stop()
            # Run installed application tests with the bundled runtime as well.
            app = home / 'releases' / current / 'app'
            code = "import sys, unittest, shutil; sys.path[:0]=[sys.argv[1],sys.argv[1]+'/tests']; assert shutil.which('gh') is None; loader=unittest.defaultTestLoader; suite=loader.loadTestsFromNames(['test_updater','test_release_http','test_github_updates','test_public_updates']) if sys.platform=='win32' else loader.discover(sys.argv[1]+'/tests'); r=unittest.TextTestRunner().run(suite); raise SystemExit(not r.wasSuccessful())"
            completed = subprocess.run([str(python), '-X', 'utf8', '-I', '-B', '-c', code, str(app)], env=env, capture_output=True, text=True, encoding='utf-8', timeout=180)
            if completed.returncode:
                raise RuntimeError('Installed tests failed: ' + completed.stderr[-6000:])
            count = re.search(r'Ran (\d+) tests', completed.stderr)
            assert count and int(count[1]) >= (67 if os.name == 'nt' else 258)
            report = {'version': current, 'platform': platform, 'online': args.online,
                'application_github_credentials': False, 'application_cli': False, 'native_launcher': True,
                'backup_and_data_preserved': True, 'installed_archive_tests': int(count[1]),
                'test_scope': 'update modules' if os.name == 'nt' else 'full application suite',
                'install_and_restart': True, 'rollback': not args.online,
                'start_kind': 'same-client lower-version fixture' if args.online else 'complete package'}
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
            print(json.dumps(report), flush=True)
        finally:
            stop()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--online', action='store_true')
    parser.add_argument('--tag')
    parser.add_argument('--package', type=Path)
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--output', type=Path)
    options = parser.parse_args()
    if options.online:
        if not options.tag or not re.fullmatch(r'v\d+\.\d+\.\d+', options.tag):
            parser.error('--online requires --tag vX.Y.Z')
    elif not options.package or not options.archive:
        parser.error('offline mode requires --package and --archive')
    try:
        check(options)
    except Exception:
        detail = traceback.format_exc()
        for name in ('GH_TOKEN', 'GITHUB_TOKEN'):
            if os.environ.get(name):
                detail = detail.replace(os.environ[name], '[redacted]')
        detail = re.sub(r'https?://\S+', '[URL redacted]', detail)
        safe = detail.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
        print('::error::' + safe.encode('ascii', 'backslashreplace').decode('ascii'), flush=True)
        raise SystemExit(1)
