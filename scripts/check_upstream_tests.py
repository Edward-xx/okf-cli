"""Offline regressions for upstream monitoring and its CLI contract."""
import copy
import base64
import hashlib
import http.client
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

import check_upstream as upstream

spec = importlib.util.spec_from_file_location('fetch_fixtures', Path(__file__).with_name('fetch-fixtures.py'))
downloader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(downloader)


def tree(entries):
    return {'tree': [{'type': 'blob', 'path': path, 'sha': sha} for path, sha in entries.items()]}


class UpstreamChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.fixtures = self.root / 'fixtures/upstream'
        self.fixtures.mkdir(parents=True)
        (self.fixtures / 'SPEC.md').write_bytes(b'pinned')
        (self.fixtures / 'note.md').write_bytes(b'pinned')
        self.source = {'name': 'official', 'repository': 'https://github.com/new/official',
                       'commit': 'a' * 40, 'spec': 'SPEC.md', 'bundle': 'bundle', 'track': True,
                       'files': [{'path': 'SPEC.md', 'source_path': 'SPEC.md', 'sha256': hashlib.sha256(b'pinned').hexdigest()},
                                 {'path': 'note.md', 'source_path': 'bundle/note.md', 'sha256': hashlib.sha256(b'pinned').hexdigest()}]}
        self.write_manifest([self.source])
        self.before = {'SPEC.md': 'spec', 'bundle/index.md': 'index', 'bundle/note.md': 'note'}
        self.after = self.before.copy()
        self.urls = []

    def write_manifest(self, sources):
        (self.fixtures / 'manifest.json').write_text(json.dumps(sources), encoding='utf-8')

    def fetch(self, url):
        self.urls.append(url)
        if url.endswith('/commits/HEAD'):
            return {'sha': 'b' * 40}
        return tree(self.before if '/trees/' + 'a' * 40 in url else self.after)

    def audit(self, online=True, fetcher=None):
        return upstream.audit(online, fetcher or self.fetch, self.root)

    def test_upstream_changes_preserve_additions_removals_and_edits(self):
        self.assertEqual(upstream.compare({'old':'a','edited':'b','same':'c'}, {'new':'a','edited':'d','same':'c'}),
                         {'added':['new'],'removed':['old'],'changed':['edited']})

    def test_upstream_requires_complete_tree_and_expected_paths(self):
        for tree in ({'truncated':True}, {'tree':[]},
                     {'tree':[{'type':'blob','path':'SPEC.md','sha':'a'}]}):
            with self.assertRaises(ValueError):
                upstream.inventory(tree,'bundle','SPEC.md')
        result = upstream.inventory({'tree':[{'type':'blob','path':p,'sha':'a'} for p in
                                    ('SPEC.md','bundle/note.md','bundle-extra/note.md','code.py')]},'bundle','SPEC.md')
        self.assertEqual(set(result), {'SPEC.md','bundle/note.md'})

    def test_offline_and_historical_sources_never_access_network(self):
        self.assertEqual(self.audit(False)['status'], 'local_verified')
        historical = {**self.source, 'name': 'google', 'track': False,
                      'repository': 'https://github.com/old/official'}
        self.write_manifest([historical, self.source])
        report = self.audit()
        self.assertEqual(report['status'], 'unchanged')
        self.assertEqual(report['sources'][0]['skip_reason'], 'historical_snapshot')
        self.assertTrue(all('/new/official/' in url for url in self.urls))
        self.assertEqual(sum(url.endswith('/commits/HEAD') for url in self.urls), 1)

    def test_spec_and_sample_changes_are_distinct_and_hashed(self):
        for after in ({**self.before, 'SPEC.md': 'new-spec'},
                      {'SPEC.md': 'spec', 'bundle/index.md': 'new-index', 'bundle/new.md': 'new'}):
            with self.subTest(after=after):
                self.after = after
                original = (self.fixtures / 'manifest.json').read_bytes()
                report = self.audit()
                result = report['sources'][0]
                self.assertEqual(report['status'], 'review_required')
                self.assertEqual(result['spec_changed'], after['SPEC.md'] != 'spec')
                self.assertNotIn('SPEC.md', sum(result['sample_changes'].values(), []))
                self.assertTrue(result['changed_hashes'])
                self.assertEqual((self.fixtures / 'manifest.json').read_bytes(), original)

    def test_layout_move_is_review_not_network_failure(self):
        self.after = {'moved/SPEC.md': 'spec', 'moved/index.md': 'index'}
        result = self.audit()['sources'][0]
        self.assertEqual(result['status'], 'review_required')
        self.assertEqual(result['layout_changes'], ['SPEC.md', 'bundle/'])

    def test_truncated_or_malformed_tree_is_check_failure(self):
        for response in ({'truncated': True}, {'unexpected': []}, None, [], {'tree': [None]},
                         tree({'SPEC.md': None}), {'tree': [{'path': 'SPEC.md'}]},
                         {'tree': [{'path': 'SPEC.md', 'sha': 'a', 'type': 'unknown'}]},
                         {'tree': tree(self.before)['tree'] * 2}):
            def fetch(url):
                return {'sha': 'b' * 40} if url.endswith('/HEAD') else response
            with self.subTest(response=response):
                self.assertEqual(self.audit(fetcher=fetch)['status'], 'check_failed')

    def test_interrupted_http_body_preserves_other_sources(self):
        second = {**self.source, 'name': 'second', 'repository': 'https://github.com/other/repo'}
        self.write_manifest([second, self.source])
        def fetch(url):
            if '/other/repo/' in url:
                raise http.client.IncompleteRead(b'partial', 100)
            return self.fetch(url)
        report = self.audit(fetcher=fetch)
        self.assertEqual(report['status'], 'check_failed')
        self.assertEqual(report['sources'][0]['error_kind'], 'network')
        self.assertEqual(report['sources'][1]['status'], 'unchanged')

    def test_missing_pinned_spec_or_samples_is_not_local_success(self):
        for files in ([self.source['files'][0]], [self.source['files'][1]], self.source['files'] * 2):
            self.write_manifest([{**self.source, 'files': files}])
            result = self.audit(False)
            self.assertEqual(result['status'], 'check_failed')
            self.assertFalse(result['sources'][0]['local_hashes_valid'])

    def test_summary_cannot_overwrite_json_output(self):
        output = self.root / 'report.md'
        output.write_text('existing report')
        with patch('sys.stderr'), self.assertRaises(SystemExit) as error:
            upstream.main([str(output)])
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(output.read_text(), 'existing report')

    def test_network_failure_preserves_other_source_results(self):
        second = {**self.source, 'name': 'second', 'repository': 'https://github.com/other/repo'}
        self.write_manifest([second, self.source])
        self.after['SPEC.md'] = 'new'
        def fetch(url):
            if '/other/repo/' in url:
                raise URLError('simulated outage')
            return self.fetch(url)
        report = self.audit(fetcher=fetch)
        self.assertEqual(report['status'], 'check_failed')
        self.assertEqual(report['sources'][0]['error_kind'], 'network')
        self.assertEqual(report['sources'][1]['status'], 'review_required')

    def test_local_corruption_and_invalid_manifest_fail(self):
        (self.fixtures / 'SPEC.md').write_bytes(b'changed')
        result = self.audit()['sources'][0]
        self.assertFalse(result['local_hashes_valid'])
        self.assertEqual(result['error_kind'], 'local_integrity')
        self.assertEqual(self.urls, [])
        (self.fixtures / 'manifest.json').write_text('{')
        self.assertEqual(self.audit()['status'], 'check_failed')

    def test_cli_writes_reports_before_returning_each_exit_status(self):
        report = self.audit()
        for status, code in upstream.EXIT_CODES.items():
            with self.subTest(status=status):
                current = copy.deepcopy(report)
                current['status'] = status
                output = self.root / status / 'report.json'
                with patch.object(upstream, 'audit', return_value=current), patch('builtins.print') as printer:
                    self.assertEqual(upstream.main([str(output), '--online']), code)
                self.assertEqual(json.loads(output.read_text())['status'], status)
                self.assertIn(status, output.with_suffix('.md').read_text())
                self.assertNotIn('passed', printer.call_args.args[0])

    def test_refresh_pins_spec_and_preserves_other_sources(self):
        self.source['licenses'] = ['LICENSE']
        historical = {**self.source, 'name': 'google', 'track': False}
        self.write_manifest([historical, self.source])
        data = b'original\r\nbytes\n'
        blob_sha = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        def fetch(url):
            if '/git/trees/' in url:
                self.assertIn('c' * 40, url)
                return tree({p: blob_sha for p in ('SPEC.md', 'LICENSE', 'bundle/index.md')})
            return {'encoding': 'base64', 'content': base64.b64encode(data).decode()}
        with patch.object(downloader, 'ROOT', self.fixtures), patch.object(downloader, 'fetch', side_effect=fetch), \
                patch('sys.argv', ['fetch-fixtures.py', '--source', 'official', '--commit', 'c' * 40]):
            downloader.main()
        manifest = json.loads((self.fixtures / 'manifest.json').read_text())
        self.assertEqual(manifest[0], historical)
        self.assertEqual(manifest[1]['commit'], 'c' * 40)
        self.assertEqual(len(manifest[1]['files']), 3)
        self.assertEqual((self.fixtures / 'official/SPEC.md').read_bytes(), data)
        self.assertEqual((self.fixtures / 'official/bundle/index.md').read_bytes(), data)
        self.assertNotIn(b'\r', (self.fixtures / 'manifest.json').read_bytes())

    def test_refresh_failure_does_not_write_baseline(self):
        self.source['licenses'] = []
        self.write_manifest([self.source])
        original = (self.fixtures / 'manifest.json').read_bytes()
        def fetch(url):
            if '/git/trees/' in url:
                return tree({'SPEC.md': 'a' * 40, 'bundle/index.md': 'b' * 40})
            return {'encoding': 'base64', 'content': 'YmFk'}
        with patch.object(downloader, 'ROOT', self.fixtures), patch.object(downloader, 'fetch', side_effect=fetch), \
                patch('sys.argv', ['fetch-fixtures.py', '--source', 'official']):
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                downloader.main()
        self.assertEqual((self.fixtures / 'manifest.json').read_bytes(), original)
        self.assertFalse((self.fixtures / 'official').exists())

    def test_refresh_rejects_moving_ref_and_historical_advance(self):
        for track, commit in ((True, 'HEAD'), (False, 'c' * 40)):
            self.write_manifest([{**self.source, 'track': track}])
            with patch.object(downloader, 'ROOT', self.fixtures), patch.object(downloader, 'fetch') as fetch, \
                    patch('sys.argv', ['fetch-fixtures.py', '--source', 'official', '--commit', commit]), \
                    patch('sys.stderr'):
                with self.assertRaises(SystemExit) as error:
                    downloader.main()
                self.assertEqual(error.exception.code, 2)
                fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
