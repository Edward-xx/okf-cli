"""Validate real success, partial and error responses against the JSON Schema."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import sqlite3
from contextlib import closing
from jsonschema import Draft202012Validator

root = Path(__file__).resolve().parents[1]
binary = Path(sys.argv[1]).resolve()
subprocess.run([sys.executable, str(root / 'scripts/check_search_schema.py')], check=True)
schema = json.loads((root / 'schemas/response.schema.json').read_text(encoding='utf-8'))
Draft202012Validator.check_schema(schema)
validator = Draft202012Validator(schema)
with tempfile.TemporaryDirectory(prefix='okf-schema-') as temporary:
    workspace = Path(temporary)
    env = dict(os.environ, OKF_HOME=str(workspace / 'home'))
    bundle = workspace / 'book'
    bundle.mkdir()
    (bundle / 'note.md').write_text('---\ntype: Note\n---\nHello 认证🙂\n', encoding='utf-8')
    (bundle / 'rich.md').write_text(
        '---\ntype: Custom Type\ntitle: 丰富卡片\ndescription: 检索摘要\ntags: [security]\n---\nRich content\n',
        encoding='utf-8')
    other_bundle = workspace / 'other'
    other_bundle.mkdir()
    (other_bundle / 'note.md').write_text(
        '---\ntype: Note\n---\nHello from another source\n', encoding='utf-8')
    sources = {'book': bundle, 'other': other_bundle}
    generations = {}
    cases = [
        (0, ['bundle', 'add', 'book', '--path', str(bundle)]),
        (0, ['validate', 'book']), (0, ['sync', 'book']),
        (0, ['search', 'note', '--bundle', 'book'], ['note']),
        (0, ['search', 'rich', '--bundle', 'book'], ['rich']),
        (0, ['search', 'no-match', '--bundle', 'book'], []),
        (7, ['search', 'note', '--bundle', 'book', '--max-scan', '1'], []),
        (3, ['search', 'note', '--bundle', 'missing']),
        (0, ['search', 'Hello', '--bundle', 'book', '--full-text'], ['note']),
        (0, ['context', '--query', 'Hello', '--bundle', 'book', '--content'], ['note']),
        (0, ['search', 'no-match', '--bundle', 'book', '--full-text'], []),
        (0, ['context', '--query', 'no-match', '--bundle', 'book', '--content'], []),
        (0, ['search', '认证🙂', '--bundle', 'book', '--full-text', '--candidate-budget', '1000'], ['note']),
        (0, ['context', '--query', '认证🙂', '--bundle', 'book', '--content', '--candidate-budget', '1000'], ['note']),
        (7, ['search', 'Hello', '--bundle', 'book', '--full-text', '--max-scan', '1'], ['note']),
        (7, ['context', '--query', 'Hello', '--bundle', 'book', '--content', '--candidate-budget', '0'], []),
        (3, ['search', 'Hello', '--bundle', 'missing', '--full-text']),
        (3, ['context', '--query', 'Hello', '--bundle', 'missing', '--content']),
        (0, ['read', '@book/note', '--indexed']),
        (0, ['index', 'status', 'book']), (0, ['cache', 'reset']),
        (0, ['index', 'verify', 'book']),
        (0, ['neighbors', '@book/note']),
        (0, ['validate', 'book', '--attachments', '--anchor-style', 'headings-v1']),
        (0, ['search', 'Hello', '--bundle', 'book', '--full-text', '--type', 'Note'], ['note']),
        (3, ['read', '@missing/note']),
        (7, ['sync', 'book', '--max-scan', '1']),
        (2, ['read', '@book/note', '--limit', '2']),
        (2, ['bundle', 'cache', 'book', '--compact']),
        (0, ['build-info']),
        (0, ['search', 'note', '--bundle', 'book', '--trust', 'unverified', '--expiry', 'unspecified', '--as-of', '2030-01-01T00:00:00Z']),
        (0, ['validate', 'book', '--max-warnings', '0']),
        (7, ['validate', 'book', '--max-warnings', '0', '--max-scan', '1']),
        (0, ['bundle', 'add', 'other', '--path', str(other_bundle)]),
        (7, ['search', 'note', '--bundle', 'book', '--bundle', 'other', '--limit', '1'], ['note']),
        (7, ['search', 'Hello', '--bundle', 'book', '--bundle', 'other', '--full-text'], ['note']),
        (7, ['context', '--query', 'Hello', '--bundle', 'book', '--bundle', 'other', '--content'], ['note']),
        (0, ['sync', 'other']),
        (0, ['search', 'Hello', '--bundle', 'book', '--bundle', 'other', '--full-text'], ['note', 'note']),
        (0, ['context', '--query', 'Hello', '--bundle', 'book', '--bundle', 'other', '--content'], ['note', 'note']),
        (7, ['context', '--query', 'Hello', '--bundle', 'book', '--bundle', 'other', '--content', '--max-scan', '1'], ['note']),
    ]
    for code, args, *expected_ids in cases:
        result = subprocess.run([str(binary), '--format', 'json', *args], env=env,
                                cwd=workspace, capture_output=True, timeout=60)
        assert result.returncode == code, (args, result.stdout, result.stderr)
        assert not result.stderr, result.stderr
        response = json.loads(result.stdout)
        validator.validate(response)
        if args[0] == 'sync' and code == 0:
            generations[args[1]] = response['data']['generation']
        if expected_ids:
            assert [item['id'] for item in response['data']['items']] == expected_ids[0], response
        if response['data'].get('mode') == 'full_text':
            for item in response['data']['items']:
                snippet = item['snippet']
                alias, concept_id = item['ref'][1:].split('/', 1)
                assert snippet['path'] == concept_id + '.md', item
                raw = (sources[alias] / snippet['path']).read_bytes()
                assert 0 <= snippet['byte_start'] <= snippet['byte_end'] <= len(raw), item
                assert raw[snippet['byte_start']:snippet['byte_end']].decode('utf-8') == snippet['text'], item
                assert hashlib.sha256(raw).hexdigest() == item['content_sha256'], item
                data = response['data']
                generation = data.get('generation') or data['generations'][alias]
                assert item['generation'] == generation == generations[alias], item
    (bundle / 'note.md').write_text('---\ntype: Note\ntitle: 123\n---\nHello\n', encoding='utf-8')
    result = subprocess.run([str(binary), '--format', 'json', 'validate', 'book', '--max-warnings', '0'],
                            env=env, cwd=workspace, capture_output=True, timeout=60)
    assert result.returncode == 1 and not result.stderr
    response = json.loads(result.stdout)
    validator.validate(response)
    assert response['data']['quality_gate']['passed'] is False
    (bundle / 'second.md').write_text('---\ntype: Note\n---\nevidence\n', encoding='utf-8')
    for code, command in [(0, ['sync', 'book']), (7, ['index', 'verify', 'book', '--max-scan', '1'])]:
        result = subprocess.run([str(binary), '--format', 'json', *command], env=env, cwd=workspace, capture_output=True, timeout=60)
        assert result.returncode == code and not result.stderr, result.stdout
        validator.validate(json.loads(result.stdout))
    with closing(sqlite3.connect(workspace / 'home/content.sqlite')) as connection:
        connection.execute("UPDATE documents SET raw='damaged' WHERE path='second.md'")
        connection.commit()
    result = subprocess.run([str(binary), '--format', 'json', 'index', 'verify', 'book'], env=env, cwd=workspace, capture_output=True, timeout=60)
    assert result.returncode == 5 and not result.stderr
    validator.validate(json.loads(result.stdout))
print(f'Schema validated against {len(cases) + 4} command responses')
