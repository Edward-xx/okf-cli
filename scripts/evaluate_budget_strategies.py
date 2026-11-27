"""Compare bounded production strategies on one fixed offline corpus.

Use --baseline-binary to measure the previous implementation on identical inputs.
Timing includes process startup and is diagnostic, never a quality gate.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from measure import run_measured


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--baseline-binary', type=Path)
    args = parser.parse_args()
    bundles = {}
    for name, size in [('a_empty', 3), ('b_small', 8), ('z_large', 60)]:
        docs = {}
        for i in range(size):
            body = 'sharedterm ordinary background'
            tags = '[]'
            if i == 0:
                body += ' duplicate-evidence'
            if name != 'a_empty' and i >= size - (2 if name == 'z_large' else 1):
                body += ' late-evidence 认证流程 authentication procedure'
                tags = '[selected]'
            docs[f'note-{i:03}.md'] = f'---\ntype: Note\ntags: {tags}\n---\n{body}\n'
        bundles[name] = docs
    tails = ['@b_small/note-007', '@z_large/note-058', '@z_large/note-059']
    tasks = [
        ('tail', 'late-evidence', [], tails),
        ('sparse', 'sharedterm', ['--tag', 'selected'], tails),
        ('duplicates', 'duplicate-evidence', [], [f'@{n}/note-000' for n in bundles]),
        ('chinese', '认证流程', [], tails),
        ('short', '认证', [], tails),
        ('natural', 'how does authentication work', [], tails),
        ('cross_language', '如何进行身份验证', [], tails),
        ('dense', 'sharedterm', [], [f'@{n}/{p[:-3]}' for n, ds in bundles.items() for p in ds]),
        ('absent', 'nonexistent-evidence', [], []),
    ]
    # Relevance is hand-labelled, independent of current literal AND semantics.
    corpus_hash = hashlib.sha256(json.dumps(bundles, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    runs, builds = [], []
    binaries = [('current', args.binary.resolve())]
    if args.baseline_binary:
        binaries.insert(0, ('before', args.baseline_binary.resolve()))
    for build, binary in binaries:
        with tempfile.TemporaryDirectory(prefix='okf-strategies-') as temporary:
            work = Path(temporary)
            env = dict(os.environ, OKF_HOME=str(work / 'home'))

            def call(arguments):
                process, elapsed, peak = run_measured(
                    [str(binary), '--format', 'json', *arguments], cwd=work, env=env)
                assert process.returncode in (0, 7), (arguments, process.stdout, process.stderr)
                assert not process.stderr
                return json.loads(process.stdout), elapsed, peak

            def evidence(response):
                refs = []
                for item in response['data']['items']:
                    name, path = item['ref'][1:].split('/', 1)
                    raw = bundles[name][path + '.md'].encode()
                    snippet = item['snippet']
                    assert hashlib.sha256(raw).hexdigest() == item['content_sha256']
                    assert item['generation'] == generations[name]
                    assert raw[snippet['byte_start']:snippet['byte_end']] == snippet['text'].encode()
                    refs.append(item['ref'])
                assert len(refs) == len(set(refs))
                if response['page']['total'] is None:
                    assert response['status'] == 'partial'
                return refs

            generations, sync_ms = {}, 0
            for name, docs in bundles.items():
                directory = work / name
                directory.mkdir()
                for path, raw in docs.items():
                    (directory / path).write_bytes(raw.encode())
                call(['bundle', 'add', name, '--path', str(directory)])
                response, elapsed, _ = call(['sync', name, '--max-scan', '1000'])
                assert response['status'] == 'ok'
                generations[name] = response['data']['generation']
                sync_ms += elapsed
                directory.rename(work / (name + '-offline'))
            database = work / 'home/content.sqlite'
            with closing(sqlite3.connect(database)) as connection:
                counts = {table: connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                          for table in ('documents', 'candidate_documents', 'candidate_postings')}
            builds.append({'build': build, 'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                           'sync_ms': sync_ms, 'database_bytes': database.stat().st_size, 'table_rows': counts})
            for mode in ('search', 'context'):
                for task, query, filters, relevant in tasks:
                    for scan, rows in [(3, None), (10, None), (100, None),
                                       (3, 24), (10, 256), (100, 4096)]:
                        command = ['search', query, '--full-text'] if mode == 'search' else ['context', '--query', query, '--content']
                        command += [*filters, '--max-scan', str(scan), '--limit', '100',
                                    '--budget-bytes', '65536', '--as-of', '2030-01-01T00:00:00Z']
                        if rows is not None:
                            command += ['--candidate-budget', str(rows)]
                        selected = [arg for name in bundles for arg in ('--bundle', name)]
                        response, elapsed, peak = call(command + selected)
                        reverse, _, _ = call(command + [arg for name in reversed(bundles) for arg in ('--bundle', name)])
                        assert response['data']['items'] == reverse['data']['items']
                        assert response['budget'] == reverse['budget']
                        refs = evidence(response)
                        assert set(refs) <= set(relevant)
                        budget = response['budget']
                        assert budget['indexed_documents_scanned'] <= scan
                        assert budget.get('candidate_rows_read', 0) <= (rows or 0)
                        complete = response['page']['total'] is not None
                        # Executable quality gates, with no machine-dependent timing limits.
                        if build == 'current' and rows == 256 and task in ('tail', 'chinese'):
                            assert set(refs) == set(relevant), (task, refs)
                        if build == 'current' and rows == 24 and task == 'dense':
                            assert len({ref.split('/')[0] for ref in refs}) == 3
                        if scan == 100 and task not in ('natural', 'cross_language'):
                            assert complete and set(refs) == set(relevant), (task, rows, refs)
                        runs.append({'build': build, 'mode': mode, 'task': task,
                                     'strategy': 'path' if rows is None else 'candidates',
                                     'scan_limit': scan, 'candidate_limit': rows,
                                     'evidence_recall': len(refs) / len(relevant) if relevant else None,
                                     'source_coverage': len({r.split('/')[0] for r in refs}) / len({r.split('/')[0] for r in relevant}) if relevant else None,
                                     'references': refs, 'complete': complete, 'budget': budget,
                                     'elapsed_ms': elapsed, 'sampled_peak_rss_bytes': peak})
            # Empty/small earlier bundles release body and row allowances to the tail bundle.
            response, _, _ = call(['search', 'late-evidence', '--full-text', '--bundle', 'a_empty',
                                   '--bundle', 'z_large', '--max-scan', '2', '--candidate-budget', '128',
                                   '--as-of', '2030-01-01T00:00:00Z'])
            refs = evidence(response)
            assert response['budget']['indexed_documents_scanned'] <= 2
            assert response['budget']['candidate_rows_read'] <= 128
            if build == 'current':
                assert refs == tails[1:], response
                assert response['budget']['indexed_documents_scanned'] == 2
            runs.append({'build': build, 'task': 'redistribution', 'response': response})
    report = {'corpus_sha256': corpus_hash,
              'evaluation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'tasks': tasks, 'builds': builds, 'runs': runs,
              'notes': ['Candidate budgets are additional work, not equivalent to body reads.',
                        'Manifest COUNT queries, B-tree traversal and hashes are outside row counters; elapsed time includes them.',
                        'Natural language relevance is independent of exact-match coverage.',
                        'Default remains path scanning; short queries and dense postings can regress.']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Budget strategy evaluation passed: {len(runs)} runs, evidence and quality gates')


if __name__ == '__main__':
    main()
