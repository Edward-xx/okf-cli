"""Exercise OKF v0.3 with an actual CLI and isolated filesystem bundles."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    binary = args.binary.resolve()
    checks, calls = [], []

    def check(name, condition):
        checks.append({'name': name, 'passed': bool(condition)})

    with tempfile.TemporaryDirectory(prefix='okf-v03-') as temporary:
        work = Path(temporary)
        # Stop project discovery here even if a user's home has a parent binding.
        (work / '.git').mkdir()
        env = {**os.environ, 'OKF_HOME': str(work / 'home')}

        def run(*command):
            result = subprocess.run([str(binary), '--format', 'json', *command],
                                    cwd=work, env=env, capture_output=True, timeout=60)
            response = json.loads(result.stdout)
            calls.append({'args': command, 'exit_code': result.returncode, 'response': response})
            if result.stderr:
                raise AssertionError(result.stderr.decode(errors='replace'))
            return result.returncode, response

        def write(root, name, text):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding='utf-8', newline='\n')

        def concept(fields='', body='evidence\n'):
            return f'---\ntype: Note\n{fields}---\n{body}'

        for version in [None, '0.2', '0.3', '99.0']:
            alias = 'v' + (version or 'none').replace('.', '')
            root = work / alias
            write(root, 'index.md', (f"---\nokf_version: '{version}'\ntitle: Test\nx-extra: true\n---\n" if version else '# Index\n'))
            write(root, 'README.md', '# Intro\nOrdinary documentation\n')
            write(root, '.hidden/invalid.md', '---\ntype: [\n---\n')
            write(root, 'new.md', concept())
            write(root, 'nested/local.md', concept())
            write(root, 'local.md', concept())
            write(root, 'nested/old.md', concept('status: deprecated\nsuperseded_by: new.md\nresource: local.md\nsources: {resource: ../new.md}\n', '[original](../new.md)\n'))
            check(alias + ': bind', run('bundle', 'add', alias, '--path', str(root))[0] == 0)
            validation_code, validation = run('validate', alias)
            check(alias + ': valid bundle with README and dot files', validation_code == 0)
            check(alias + ': unknown version diagnostic', any(d['code'] == 'UNKNOWN_OKF_VERSION' for d in validation['diagnostics']) == (version == '99.0'))
            check(alias + ': sync', run('sync', alias)[0] == 0)
            code, graph = run('neighbors', f'@{alias}/nested/old', '--direction', 'outgoing')
            edges = graph['data']['items']
            replacement = [e for e in edges if e['evidence']['reference'].get('field') == '/superseded_by']
            check(alias + ': fallback gated by version', bool(replacement) == (version in (None, '0.2')))
            check(alias + ': primary wins', any(e['to'] == f'@{alias}/nested/local.md' and e['evidence']['reference'].get('field') == '/resource' for e in edges))
            check(alias + ': source and body evidence retained', len([e for e in edges if e['to'] == f'@{alias}/new.md']) == (3 if replacement else 2))
            check(alias + ': hidden explicit read denied', run('read', f'@{alias}/.hidden/invalid')[0] == 3)
            original = (root / 'nested/old.md').read_bytes()
            read_code, read = run('read', f'@{alias}/nested/old', '--indexed')
            check(alias + ': original bytes and hash', read_code == 0 and read['data']['document']['text'].encode() == original and read['data']['document']['content_sha256'] == hashlib.sha256(original).hexdigest())
            root.rename(work / (alias + '-offline'))
            offline_code, offline = run('neighbors', f'@{alias}/nested/old', '--direction', 'outgoing')
            check(alias + ': immutable offline graph', offline_code == code and offline['data']['items'] == edges)

        root = work / 'edges'
        write(root, 'index.md', "---\nokf_version: '0.3'\n---\n")
        fields = 'status: deprecated\n' + '\n'.join([
            'resource: target.md', 'sources: {resource: target.md}',
            'computation: target.md', 'executor: {resource: target.md}',
            'attester: {resource: target.md}', 'superseded_by: target.md']) + '\n'
        write(root, 'node.md', concept(fields, '[target](target.md)\n'))
        write(root, 'target.md', concept('status: deprecated\nsuperseded_by: node.md\n'))
        run('bundle', 'add', 'edges', '--path', str(root))
        run('sync', 'edges')
        code, graph = run('neighbors', '@edges/node', '--direction', 'outgoing')
        check('all six path fields plus body', code == 0 and len(graph['data']['items']) == 7)
        check('replacement cycle diagnosed', any(d['code'] == 'SUPERSESSION_CYCLE' for d in graph['diagnostics']))
        check('bounded replacement traversal', run('neighbors', '@edges/node', '--direction', 'outgoing', '--max-scan', '1')[0] == 7)
        write(root, 'node.md', concept(fields.replace('status: deprecated', 'status: stable')))
        run('sync', 'edges')
        _, graph = run('neighbors', '@edges/node', '--direction', 'outgoing')
        check('stable replacement ignored', not any(e['evidence']['reference'].get('field') == '/superseded_by' for e in graph['data']['items']))
        write(root, 'node.md', concept('status: deprecated\nsuperseded_by: target.md/\nresource: target.md%2F\n'))
        run('sync', 'edges')
        _, graph = run('neighbors', '@edges/node', '--direction', 'outgoing')
        check('directory syntax cannot identify a concept file', not graph['data']['items'] and any(d['code'] == 'INVALID_SUPERSESSION_TARGET' for d in graph['diagnostics']))

        # A real directory must block legacy fallback just like a non-concept file.
        root = work / 'shadow'
        write(root, 'node.md', concept())
        write(root, 'nested/source.md', concept('resource: node.md\n'))
        (root / 'nested/node.md').mkdir()
        run('bundle', 'add', 'shadow', '--path', str(root))
        _, synced = run('sync', 'shadow')
        generation = synced['data']['generation']
        check('directory inventory verifies', run('index', 'verify', 'shadow')[0] == 0)
        root.rename(work / 'shadow-offline')
        _, graph = run('neighbors', '@shadow/nested/source', '--direction', 'outgoing')
        check('directory blocks legacy graph fallback offline', not graph['data']['items'])
        (work / 'shadow-offline').rename(root)
        (root / 'nested/node.md').rmdir()
        _, synced = run('sync', 'shadow')
        check('directory-only change updates generation', synced['data']['generation'] != generation)
        _, graph = run('neighbors', '@shadow/nested/source', '--direction', 'outgoing')
        check('removed directory permits fallback', len(graph['data']['items']) == 1)
        generation = synced['data']['generation']
        with closing(sqlite3.connect(work / 'home/content.sqlite')) as db, db:
            db.execute('UPDATE snapshots SET directories=? WHERE generation=?', ('["forged"]', generation))
        check('tampered directory inventory rejected', run('neighbors', '@shadow/nested/source')[0] == 5)
        check('tampered directory inventory verification fails', run('index', 'verify', 'shadow')[0] == 5)
        with closing(sqlite3.connect(work / 'home/content.sqlite')) as db, db:
            db.execute('UPDATE snapshots SET directories=NULL WHERE generation=?', (generation,))
        check('legacy inventory cannot imply absence', run('neighbors', '@shadow/nested/source')[0] == 3)
        check('legacy inventory preserves indexed reads', run('read', '@shadow/nested/source', '--indexed')[0] == 0)
        check('sync repairs directory inventory', run('sync', 'shadow')[0] == 0 and run('neighbors', '@shadow/nested/source')[0] == 0)

        root = work / 'scope'
        write(root, 'source.md', concept("sources: {resource: 'all queries in project: finance'}\n"))
        run('bundle', 'add', 'scope', '--path', str(root))
        run('sync', 'scope')
        code, graph = run('neighbors', '@scope/source', '--direction', 'outgoing')
        check('scope descriptor is not a malformed path', code == 0 and not graph['data']['items'])

        root = work / 'anchors'
        write(root, 'README.md', '# Intro\nDocumentation\n')
        write(root, 'source.md', concept('', '[docs](README.md#intro)\n'))
        run('bundle', 'add', 'anchors', '--path', str(root))
        code, validation = run('validate', 'anchors', '--anchor-style', 'headings-v1')
        check('non-concept anchor is checked', code == 0 and validation['data']['anchor_check']['skipped'] == 0 and not any(d['code'] == 'MISSING_ANCHOR' for d in validation['diagnostics']))

        root = work / 'sections'
        write(root, 'compute.md', '---\ntype: Attested Computation\nruntime: sql\n---\nComputation\n===========\n```sql\nSELECT 1\n```\n')
        run('bundle', 'add', 'sections', '--path', str(root))
        _, result = run('read', '@sections/compute')
        check('computation locator requires ATX heading', any(d['code'] == 'MISSING_COMPUTATION' for d in result['diagnostics']))

        root = work / 'case-paths'
        write(root, 'index.md', "---\nokf_version: '0.3'\n---\n")
        write(root, 'target.md', concept())
        write(root, 'query.sql', 'SELECT 1\n')
        write(root, 'compute.md', '---\ntype: Attested Computation\nruntime: sql\ncomputation: QUERY.sql\n---\n')
        run('bundle', 'add', 'casepaths', '--path', str(root))
        _, validation = run('validate', 'casepaths')
        check('resource paths are case-sensitive', any(d['code'] == 'MISSING_RESOURCE' for d in validation['diagnostics']))
        check('explicit file paths are case-sensitive', run('read', '@casepaths/TARGET')[0] == 3)
        write(root, 'compute.md', '---\ntype: Attested Computation\nruntime: sql\ncomputation: query.sql%2F\n---\n')
        _, validation = run('validate', 'casepaths')
        check('computation resource must use file syntax', any(d['code'] == 'RESOURCE_NOT_A_FILE' for d in validation['diagnostics']))

        # Exercise the Git adapter against committed objects, not the worktree.
        repo = work / 'git-repo'
        root = repo / 'bundle'
        raw = concept("title: needle\ntags: 'one,two'\nstatus: null\nresource: ../target.md\ngenerated: {by: worker/1, at: 2026-11-26}\nverified: {by: 'team:finance', at: 2026-11-27}\nstale_after: 2026-11-27\nx-extension: {unchanged: true}\n")
        write(root, 'index.md', "---\nokf_version: '0.3'\ntitle: Git bundle\n---\n")
        write(root, 'nested/note.md', raw)
        write(root, 'target.md', concept())
        write(root, 'README.md', '# Not a concept\n')
        write(root, '.hidden/bad.md', '---\ntype: [\n---\n')
        for command in [('init', '-q'), ('add', '.'), ('commit', '-q', '-m', 'fixture')]:
            subprocess.run([os.environ.get('OKF_GIT', 'git'), '-C', str(repo),
                            '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                            '-c', 'commit.gpgsign=false', '-c', 'core.autocrlf=false', *command],
                           check=True, capture_output=True, timeout=60)
        check('git bundle bind', run('bundle', 'add', 'gitbook', '--kind', 'git', '--path', str(repo), '--bundle-path', 'bundle')[0] == 0)
        write(root, 'nested/note.md', '---\ntype: [\n---\n')
        code, read = run('read', '@gitbook/nested/note')
        check('git pinned bytes ignore dirty worktree', code == 0 and read['data']['document']['text'] == raw)
        check('git dot-path explicit read denied', run('read', '@gitbook/.hidden/bad')[0] == 3)
        check('git validation skips dot paths and README', run('validate', 'gitbook')[0] == 0)
        check('git sync', run('sync', 'gitbook')[0] == 0)
        results = []
        for mode in [[], ['--full-text'], ['--full-text', '--candidate-budget', '1000']]:
            code, result = run('search', 'needle', '--bundle', 'gitbook', '--as-of', '2026-11-27T00:00:00Z', *mode)
            entries = result['data']['items']
            check('git metadata rules ' + str(mode), code == 0 and len(entries) == 1 and entries[0]['trust'] == 'machine-confirmed' and entries[0]['expiry'] == 'expired' and entries[0]['status'] == 'stable' and entries[0]['tags'] == ['one,two'])
            results.append([entry['id'] for entry in entries])
        check('git live and indexed search agree', results[0] == results[1] == results[2])
        repo.rename(work / 'git-offline')
        code, graph = run('neighbors', '@gitbook/nested/note', '--direction', 'outgoing')
        check('git offline path navigation', code == 0 and len(graph['data']['items']) == 1 and graph['data']['items'][0]['to'] == '@gitbook/target.md')

    report = {'binary': str(binary), 'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
              'checks': checks, 'calls': calls}
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    failed = [c['name'] for c in checks if not c['passed']]
    print(json.dumps({'checks': len(checks), 'cli_calls': len(calls), 'failed': failed}))
    return bool(failed)


if __name__ == '__main__':
    raise SystemExit(main())
