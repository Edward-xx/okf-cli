"""Verify pinned fixtures; --online detects changes, never accepts compatibility."""
import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
EXIT_CODES = {'local_verified': 0, 'unchanged': 0, 'review_required': 2, 'check_failed': 1}


class IncompleteTree(ValueError):
    pass


def fetch(url):
    headers = {'User-Agent': 'okf-cli-upstream'}
    if os.environ.get('GH_TOKEN'):
        headers['Authorization'] = 'Bearer ' + os.environ['GH_TOKEN']
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
        return json.load(response)


def inventory(tree, bundle, spec, require_layout=True):
    if not isinstance(tree, dict):
        raise ValueError('invalid upstream tree response')
    if tree.get('truncated'):
        raise IncompleteTree('upstream tree truncated; refusing to report complete coverage')
    if not isinstance(tree.get('tree'), list):
        raise ValueError('invalid upstream tree response')
    paths = set()
    for entry in tree['tree']:
        if not isinstance(entry, dict) or any(not isinstance(entry.get(key), str) or not entry[key]
                                             for key in ('path', 'sha', 'type')):
            raise ValueError('invalid upstream tree entry')
        if entry['type'] not in ('blob', 'tree', 'commit'):
            raise ValueError('unknown upstream tree entry type')
        if entry['path'] in paths:
            raise ValueError('duplicate upstream tree path')
        paths.add(entry['path'])
    result = {entry['path']: entry['sha'] for entry in tree['tree'] if entry['type'] == 'blob'
              and (entry['path'] == spec or
                   (entry['path'].startswith(bundle + '/') and entry['path'].endswith('.md')))}
    if require_layout and missing_layout(result, bundle, spec):
        raise ValueError('expected specification or bundle is missing')
    return result


def missing_layout(entries, bundle, spec):
    return ([spec] if spec not in entries else []) + (
        [bundle + '/'] if not any(p.startswith(bundle + '/') for p in entries) else [])


def compare(before, after):
    return {'added': sorted(after.keys() - before.keys()), 'removed': sorted(before.keys() - after.keys()),
            'changed': sorted(p for p in before.keys() & after.keys() if before[p] != after[p])}


def audit(online, fetcher=fetch, root=ROOT):
    results = []
    report = {'sources': results, 'baseline_rewritten': False, 'online': online}
    try:
        manifest = json.loads((root / 'fixtures/upstream/manifest.json').read_text(encoding='utf-8'))
        if not isinstance(manifest, list) or not manifest:
            raise ValueError('manifest must contain sources')
        names = set()
        for source in manifest:
            for key in ('name', 'repository', 'commit', 'bundle', 'spec'):
                if not isinstance(source[key], str) or not source[key]:
                    raise ValueError(f'invalid source field: {key}')
            if source['name'] in names or not re.fullmatch('[0-9a-f]{40}', source['commit']):
                raise ValueError('source names must be unique and commits immutable')
            names.add(source['name'])
            if not isinstance(source['track'], bool) or not isinstance(source['files'], list):
                raise ValueError('invalid source tracking or files configuration')
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {**report, 'status': 'check_failed', 'error': str(error)}
    for source in manifest:
        result = {'name': source['name'], 'repository': source['repository'], 'baseline': source['commit'],
                  'local_hashes_valid': False, 'upstream_checked': False, 'changes': None,
                  'review_required': False, 'status': 'check_failed'}
        results.append(result)
        stage = 'local_integrity'
        try:
            if not source['files']:
                raise ValueError('source has no pinned files')
            paths = [file['source_path'] for file in source['files']]
            if len(paths) != len(set(paths)):
                raise ValueError('duplicate pinned source path')
            if source['track'] and missing_layout(paths, source['bundle'], source['spec']):
                raise ValueError('pinned specification or sample files missing from manifest')
            for file in source['files']:
                raw = (root / 'fixtures/upstream' / file['path']).read_bytes()
                if hashlib.sha256(raw).hexdigest() != file['sha256']:
                    raise ValueError(f'fixture modified: {file["path"]}')
            result.update(local_hashes_valid=True, status='local_verified')
            if not online or not source['track']:
                result['skip_reason'] = 'offline' if not online else 'historical_snapshot'
                continue
            stage = 'remote_data'
            api = source['repository'].replace('https://github.com/', 'https://api.github.com/repos/')
            # Resolve HEAD once; all following requests use immutable commits.
            head = fetcher(api + '/commits/HEAD')['sha']
            if not isinstance(head, str) or not re.fullmatch('[0-9a-f]{40}', head):
                raise ValueError('HEAD did not resolve to an immutable commit')
            spec, bundle = source['spec'], source['bundle']
            result.update(head=head, spec_path=spec,
                          compare_url=source['repository'] + '/compare/' + source['commit'] + '...' + head)
            before = inventory(fetcher(api + '/git/trees/' + source['commit'] + '?recursive=1'), bundle, spec)
            after = inventory(fetcher(api + '/git/trees/' + head + '?recursive=1'), bundle, spec, False)
            changes = compare(before, after)
            missing = missing_layout(after, bundle, spec)
            review = any(changes.values()) or bool(missing)
            result.update(upstream_checked=True, changes=changes, spec_changed=before.get(spec) != after.get(spec),
                          sample_changes={k: [p for p in paths if p != spec] for k, paths in changes.items()},
                          layout_changes=missing, review_required=review,
                          status='review_required' if review else 'unchanged',
                          changed_hashes={p: {'before': before.get(p), 'after': after.get(p)}
                                          for paths in changes.values() for p in paths})
        except (OSError, ValueError, KeyError, TypeError, http.client.HTTPException) as error:
            kind = 'network' if isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError,
                                                   http.client.HTTPException)) else stage
            if isinstance(error, IncompleteTree):
                kind = 'tree_truncated'
            result.update(status='check_failed', error_kind=kind, error=str(error))
    statuses = {r['status'] for r in results}
    report['status'] = ('check_failed' if 'check_failed' in statuses else
                        'review_required' if 'review_required' in statuses else
                        'unchanged' if 'unchanged' in statuses else 'local_verified')
    return report


def summary(report):
    lines = ['# Upstream monitoring', '', f'Status: **{report["status"]}**', '',
             'Change detection is not a compatibility approval. Baselines were not rewritten.', '']
    if report.get('error'):
        lines.append(report['error'])
    for source in report['sources']:
        lines.extend([f'## {source["name"]}: {source["status"]}', '',
                      f'Baseline: `{source["baseline"]}`', ''])
        if source.get('compare_url'):
            lines.append(f'[Compare commits]({source["compare_url"]})')
        if source.get('error'):
            lines.append(f'Error ({source["error_kind"]}): {source["error"]}')
        if source.get('skip_reason'):
            lines.append(f'Remote check skipped: {source["skip_reason"]}')
        if source.get('changes') is not None:
            lines.append(f'Specification changed: {source["spec_changed"]}')
            for kind, paths in source['changes'].items():
                lines.extend(f'- {kind}: `{path}`' for path in paths)
            lines.extend(f'- Missing expected layout: `{path}`' for path in source['layout_changes'])
        lines.append('')
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    parser.add_argument('--online', action='store_true')
    args = parser.parse_args(argv)
    if args.output.suffix.lower() != '.json':
        parser.error('output must use .json; a sibling .md summary is also written')
    report = audit(args.online)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    args.output.with_suffix('.md').write_text(summary(report), encoding='utf-8')
    print(f'Upstream monitoring: {report["status"]}; compatibility approval is a separate review')
    return EXIT_CODES[report['status']]


if __name__ == '__main__':
    raise SystemExit(main())
