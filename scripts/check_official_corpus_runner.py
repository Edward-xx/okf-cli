"""Offline regression tests for the official corpus runner's failure reporting."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import check_official_corpus as runner


class CorpusRunnerChecks(unittest.TestCase):
    def test_git_expectations_ignore_crlf_filters_and_worktree_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            git = os.environ.get('OKF_GIT', 'git')
            def command(*args):
                return subprocess.check_output([git, '-C', str(checkout), '-c', 'core.autocrlf=false',
                                                '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                                                '-c', 'commit.gpgsign=false', *args], stderr=subprocess.PIPE)
            command('init')
            bundle = checkout / 'bundles/ga4'
            bundle.mkdir(parents=True)
            original = b'---\ntype: Note\n---\nOriginal\n'
            (bundle / 'index.md').write_bytes(original)
            command('add', '.')
            command('commit', '-m', 'fixed corpus')
            commit = command('rev-parse', 'HEAD').decode().strip()
            for changed in (original.replace(b'\n', b'\r\n'), b'edited worktree'):
                (bundle / 'index.md').write_bytes(changed)
                (bundle / 'untracked.md').write_bytes(b'untracked')
                self.assertEqual(runner.git_documents(git, checkout, commit, 'ga4'), {'index.md': original})
                self.assertEqual((bundle / 'index.md').read_bytes(), changed)

    def run_case(self, bundles, extra=(), result=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checkout = root / 'checkout'
            (checkout / 'bundles').mkdir(parents=True)
            for name, content in bundles.items():
                directory = checkout / 'bundles' / name
                directory.mkdir()
                if content is not None:
                    (directory / 'index.md').write_text(content, encoding='utf-8')
            binary = root / 'okf'
            binary.write_bytes(b'placeholder')
            output = root / 'report'
            argv = ['check_official_corpus.py', str(binary), str(checkout), str(output), *extra]
            with patch.object(sys, 'argv', argv), patch.object(
                    runner.subprocess, 'check_output', return_value='a' * 40), patch.object(
                    runner.subprocess, 'run', side_effect=result if isinstance(result, Exception) else None,
                    return_value=result) as command, contextlib.redirect_stdout(io.StringIO()):
                status = runner.main()
            report = json.loads((output / 'report.json').read_text(encoding='utf-8'))
            trace = output / 'trace.jsonl'
            records = [json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines()] if trace.exists() else []
            return status, report, records, command.call_count

    def test_empty_missing_unsupported_and_empty_document_sets_fail(self):
        cases = [({}, (), 'no bundles selected'),
                 ({'ga4': 'text'}, ('--bundles', 'typo'), 'unknown bundle names'),
                 ({'new_bundle': 'text'}, (), 'unsupported bundles'),
                 ({'ga4': None}, (), 'no Markdown documents')]
        for bundles, extra, expected in cases:
            with self.subTest(expected=expected):
                status, report, records, calls = self.run_case(bundles, extra)
                self.assertTrue(status)
                self.assertIn(expected, report['summary']['failures'][0]['error'])
                self.assertEqual(calls, 0)

    def test_invalid_json_keeps_raw_output_and_report(self):
        result = subprocess.CompletedProcess([], 2, b'not json', b'explanation')
        status, report, records, calls = self.run_case({'ga4': 'text'}, result=result)
        self.assertTrue(status)
        self.assertEqual(report['calls'], 1)
        self.assertEqual(records[0]['stdout'], 'not json')
        self.assertEqual(records[0]['stderr'], 'explanation')
        self.assertEqual(records[0]['exit_code'], 2)
        self.assertIn('error', records[0])

    def test_missing_executable_still_writes_report(self):
        status, report, records, calls = self.run_case(
            {'ga4': 'text'}, result=FileNotFoundError('missing executable'))
        self.assertTrue(status)
        self.assertIn('missing executable', records[0]['error'])
        self.assertEqual(report['calls'], 1)
        self.assertTrue(report['summary']['failures'])

    def test_timeout_keeps_partial_output_and_report(self):
        error = subprocess.TimeoutExpired('okf', 120, output=b'partial', stderr=b'detail')
        status, report, records, calls = self.run_case({'ga4': 'text'}, result=error)
        self.assertTrue(status)
        self.assertEqual(records[0]['stdout'], 'partial')
        self.assertEqual(records[0]['stderr'], 'detail')
        self.assertTrue(report['summary']['failures'])

    def test_selected_supported_bundle_and_as_of_are_preserved(self):
        result = subprocess.CompletedProcess([], 1, b'{"schema_version":"2.0"}', b'')
        status, report, records, calls = self.run_case(
            {'ga4': 'text', 'new_bundle': 'text'},
            ('--bundles', 'ga4', '--as-of', '2026-10-31T00:00:00Z'), result)
        self.assertTrue(status)
        self.assertEqual(calls, 1)
        self.assertEqual(report['as_of'], '2026-10-31T00:00:00Z')
        self.assertIn('exit 1', report['summary']['failures'][0]['error'])


if __name__ == '__main__':
    unittest.main()
