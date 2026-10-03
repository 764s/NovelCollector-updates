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


def main():
    action, tag = sys.argv[1:]
    release = candidate(tag)
    fingerprint = sha256(json.dumps(sorted((a['name'], a['size'], a['digest']) for a in release['assets'])).encode()).hexdigest()
    if action in ('promote', 'receipt'):
        if any(os.environ.get(name) != fingerprint for name in ('WINDOWS_FINGERPRINT', 'LINUX_FINGERPRINT')):
            raise ValueError('Candidate changed after native verification')
    if action == 'download':
        output = Path('candidate')
        output.mkdir(exist_ok=True)
        gh('release', 'download', tag, '--repo', REPOSITORY, '--dir', str(output))
        for asset in release['assets']:
            data = (output / asset['name']).read_bytes()
            if len(data) != asset['size'] or 'sha256:' + sha256(data).hexdigest() != asset['digest']:
                raise ValueError('Candidate digest mismatch')
        (output / 'candidate.json').write_text(json.dumps(release), encoding='utf-8')
        if os.environ.get('GITHUB_OUTPUT'):
            platform = 'windows' if os.name == 'nt' else 'linux'
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as stream:
                stream.write(platform + '_fingerprint=' + fingerprint + '\n')
    elif action == 'promote':
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
    main()
