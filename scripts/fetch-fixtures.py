"""Explicitly download pinned upstream bundles; normal tests never use the network."""
from concurrent.futures import ThreadPoolExecutor
import argparse
import base64
import re
from pathlib import Path
import hashlib
import json
from check_upstream import fetch

ROOT = Path(__file__).resolve().parents[1] / 'fixtures' / 'upstream'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, help='Manifest source to explicitly refresh')
    parser.add_argument('--commit', help='Reviewed immutable 40-character commit; never HEAD')
    args = parser.parse_args()
    manifest_bytes = (ROOT / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    newline = '\r\n' if b'\r\n' in manifest_bytes else '\n'
    sources = [source for source in manifest if source['name'] == args.source]
    if len(sources) != 1:
        parser.error('source must identify exactly one manifest entry')
    source = sources[0]
    if args.commit and not source['track']:
        parser.error('historical snapshots cannot be advanced')
    revision = args.commit or source['commit']
    if not re.fullmatch('[0-9a-f]{40}', revision):
        parser.error('commit must be a full immutable SHA')
    name, repo, bundle = source['name'], source['repository'].removeprefix('https://github.com/'), source['bundle']
    tree = fetch(f'https://api.github.com/repos/{repo}/git/trees/{revision}?recursive=1')
    if tree.get('truncated'):
        raise ValueError('incomplete upstream tree')
    paths = [entry['path'] for entry in tree['tree'] if entry['type'] == 'blob'
             and entry['path'].startswith(bundle + '/') and entry['path'].endswith('.md')]
    if not paths:
        raise ValueError(f'empty bundle: {repo}/{bundle}')
    extras = source['licenses'] + ([source['spec']] if source['track'] else [])
    blobs = {entry['path']: entry['sha'] for entry in tree['tree'] if entry['type'] == 'blob'}

    def download(path):
        blob = fetch(f'https://api.github.com/repos/{repo}/git/blobs/{blobs[path]}')
        if blob['encoding'] != 'base64':
            raise ValueError('unexpected Git blob encoding')
        data = base64.b64decode(blob['content'])
        git_hash = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        if git_hash != blobs[path]:
            raise ValueError(f'Git blob hash mismatch: {path}')
        relative = f'{name}/bundle/{path[len(bundle) + 1:]}' if path.startswith(bundle + '/') else f'{name}/{path}'
        return {'path': relative, 'source_path': path, 'sha256': hashlib.sha256(data).hexdigest()}, data

    with ThreadPoolExecutor(max_workers=6) as pool:
        downloaded = list(pool.map(download, sorted(set(paths + extras))))
    # Download everything before writing; reviewers remove obsolete files in the PR.
    for file, data in downloaded:
        target = ROOT / file['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    source.update(commit=revision, files=[file for file, _ in downloaded])
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8', newline=newline)
    print(name, len(paths), 'Markdown files')


if __name__ == '__main__':
    main()
