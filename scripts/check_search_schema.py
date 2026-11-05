"""Exercise search response contracts with isolated synthetic data."""
from copy import deepcopy
import json
from pathlib import Path
import unittest
from jsonschema import Draft202012Validator, ValidationError


class MetadataSearchSchemaChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / 'schemas/response.schema.json').read_text(encoding='utf-8'))
        Draft202012Validator.check_schema(schema)
        cls.validator = Draft202012Validator(schema)

    def setUp(self):
        self.response = {
            'schema_version': '2.0', 'request_id': 'synthetic-schema-test',
            'command': 'search', 'status': 'ok',
            'data': {
                'query': '认证', 'mode': 'metadata', 'evaluated_at': '2030-01-01T00:00:00Z',
                'items': [{
                    'id': '服务/认证', 'type': 'Service', 'title': '认证服务',
                    'description': None, 'tags': [], 'trust': 'unverified',
                    'status': 'stable', 'expiry': 'unspecified', 'kind': 'concept',
                    'ref': '@book/服务/认证', 'bundle_id': 'bundle-synthetic',
                    'content_sha256': 'a' * 64, 'upstream_revision': None,
                }],
            },
            'bundles': [],
            'page': {'returned': 1, 'total': 1, 'has_more': False, 'next_cursor': None},
            'budget': {}, 'diagnostics': [], 'next': [],
        }

    def test_success_partial_and_empty_results_are_valid(self):
        for status in ('ok', 'partial'):
            for empty in (False, True):
                with self.subTest(status=status, empty=empty):
                    response = deepcopy(self.response)
                    response['status'] = status
                    if empty:
                        response['data']['items'] = []
                        response['page']['returned'] = 0
                        response['page']['total'] = 0
                    if status == 'partial':
                        response['page']['total'] = None
                    self.validator.validate(response)

    def test_nullable_fields_declared_values_and_extensions_are_valid(self):
        response = deepcopy(self.response)
        response['data']['extension'] = {'enabled': True}
        item = response['data']['items'][0]
        item.update(description='原始摘要', tags=['security', 'security'],
                    upstream_revision='b' * 40, type='Custom Type', status='custom-status',
                    extension={'producer': 'custom'})
        for trust in ('unverified', 'machine-confirmed', 'human-reviewed'):
            for expiry in ('unspecified', 'unknown', 'current', 'expired'):
                with self.subTest(trust=trust, expiry=expiry):
                    item.update(trust=trust, expiry=expiry)
                    self.validator.validate(response)

    def test_search_data_fields_are_required_for_success_and_partial(self):
        for status in ('ok', 'partial'):
            for field in ('query', 'mode', 'items', 'evaluated_at'):
                with self.subTest(status=status, field=field):
                    response = deepcopy(self.response)
                    response['status'] = status
                    del response['data'][field]
                    with self.assertRaises(ValidationError):
                        self.validator.validate(response)

    def test_metadata_items_require_every_card_and_provenance_field(self):
        for status in ('ok', 'partial'):
            for field in self.response['data']['items'][0]:
                with self.subTest(status=status, field=field):
                    response = deepcopy(self.response)
                    response['status'] = status
                    del response['data']['items'][0][field]
                    with self.assertRaises(ValidationError):
                        self.validator.validate(response)

    def test_invalid_card_field_types_are_rejected(self):
        invalid = {
            'id': 1, 'type': None, 'title': [], 'description': 1, 'tags': 'security',
            'trust': False, 'status': None, 'expiry': [], 'kind': 1, 'ref': None,
            'bundle_id': 1, 'content_sha256': None, 'upstream_revision': 1,
        }
        for field, value in invalid.items():
            with self.subTest(field=field):
                response = deepcopy(self.response)
                response['data']['items'][0][field] = value
                with self.assertRaises(ValidationError):
                    self.validator.validate(response)

    def test_invalid_result_shapes_and_query_types_are_rejected(self):
        for field, value in (('items', {}), ('items', [None]), ('items', ['note']),
                             ('query', 1), ('query', ''), ('evaluated_at', 1),
                             ('mode', None), ('mode', 'metdata')):
            with self.subTest(field=field, value=value):
                response = deepcopy(self.response)
                response['data'][field] = value
                with self.assertRaises(ValidationError):
                    self.validator.validate(response)

    def test_invalid_card_values_are_rejected(self):
        for field, value in (('kind', 'directory'), ('tags', ['security', 1]),
                             ('type', ''), ('trust', 'verified'), ('expiry', 'fresh'),
                             ('content_sha256', 'a' * 63), ('content_sha256', 'g' * 64),
                             ('content_sha256', 'a' * 64 + '\n'),
                             ('ref', 'book/note')):
            with self.subTest(field=field, value=value):
                response = deepcopy(self.response)
                response['data']['items'][0][field] = value
                with self.assertRaises(ValidationError):
                    self.validator.validate(response)

    def test_search_errors_do_not_require_success_data(self):
        for data in ({}, {'mode': 'metadata'}):
            with self.subTest(data=data):
                response = deepcopy(self.response)
                response.update(status='error', data=data)
                self.validator.validate(response)

    def test_other_commands_do_not_use_metadata_item_constraints(self):
        for command, data in (('browse', {'items': [{'kind': 'directory'}]}),
                               ('read', {'content': 'original bytes'}),
                               ('context', {'mode': 'metadata', 'items': [{'ref': '@book/note'}]})):
            with self.subTest(command=command, data=data):
                response = deepcopy(self.response)
                response.update(command=command, data=data)
                self.validator.validate(response)


class IndexedSearchSchemaChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / 'schemas/response.schema.json').read_text(encoding='utf-8'))
        Draft202012Validator.check_schema(schema)
        cls.validator = Draft202012Validator(schema)

    def responses(self):
        for command in ('search', 'context'):
            for status in ('ok', 'partial'):
                yield {
                    'schema_version': '2.0', 'request_id': 'synthetic-indexed-schema-test',
                    'command': command, 'status': status,
                    'data': {
                        'query': '认证', 'mode': 'full_text',
                        'evaluated_at': '2030-01-01T00:00:00Z',
                        'disclosure': 'indexed_snippets' if command == 'search' else 'selected_excerpts',
                        'generation': 'b' * 64,
                        'items': [{
                            'id': '服务/认证', 'type': 'Service', 'title': '认证服务',
                            'description': None, 'tags': [], 'trust': 'unverified',
                            'status': 'stable', 'expiry': 'unspecified',
                            'ref': '@book/服务/认证', 'bundle_id': 'bundle-synthetic',
                            'content_sha256': 'a' * 64, 'upstream_revision': None,
                            'generation': 'b' * 64, 'score': 1,
                            'selection_reason': 'all query terms matched',
                            'snippet': {'path': '服务/认证.md', 'byte_start': 30,
                                        'byte_end': 40, 'text': '认证🙂'},
                        }],
                    },
                    'bundles': [],
                    'page': {'returned': 1, 'total': None if status == 'partial' else 1,
                             'has_more': False, 'next_cursor': None},
                    'budget': {}, 'diagnostics': [], 'next': [],
                }

    def test_single_multi_bundle_candidate_and_empty_results_are_valid(self):
        for response in self.responses():
            for variant in ('single', 'multi', 'candidate', 'empty'):
                with self.subTest(command=response['command'], status=response['status'], variant=variant):
                    result = deepcopy(response)
                    if variant == 'multi':
                        result['data']['generations'] = {
                            'book': result['data'].pop('generation'), 'other': 'c' * 64}
                        other = deepcopy(result['data']['items'][0])
                        other.update(ref='@other/服务/认证', bundle_id='other-synthetic', generation='c' * 64)
                        result['data']['items'].append(other)
                        result['page'].update(returned=2, total=None if result['status'] == 'partial' else 2)
                    elif variant == 'candidate':
                        result['data']['retrieval_strategy'] = 'candidates-v1'
                        result['budget'] = {'candidate_rows_limit': 100, 'candidate_rows_read': 1}
                    elif variant == 'empty':
                        result['data']['items'] = []
                        result['data'].pop('generation')
                        result['page'].update(returned=0, total=None if result['status'] == 'partial' else 0)
                    self.validator.validate(result)

    def test_nullable_fields_empty_body_and_extensions_are_valid(self):
        for response in self.responses():
            item = response['data']['items'][0]
            item.update(type='Custom Type', status='custom-status', description='自定义摘要',
                        upstream_revision='c' * 40, extension={'producer': 'synthetic'})
            item['snippet'].update(byte_start=30, byte_end=30, text='', extension=True)
            response['data']['extension'] = {'enabled': True}
            with self.subTest(command=response['command'], status=response['status']):
                self.validator.validate(response)

    def test_required_data_card_provenance_and_snippet_fields(self):
        for response in self.responses():
            for section, fields in (
                    ('data', ('query', 'mode', 'evaluated_at', 'disclosure', 'items')),
                    ('item', tuple(response['data']['items'][0])),
                    ('snippet', ('path', 'byte_start', 'byte_end', 'text'))):
                for field in fields:
                    with self.subTest(command=response['command'], status=response['status'],
                                      section=section, field=field):
                        result = deepcopy(response)
                        target = result['data']
                        if section != 'data':
                            target = target['items'][0]
                        if section == 'snippet':
                            target = target['snippet']
                        del target[field]
                        with self.assertRaises(ValidationError):
                            self.validator.validate(result)

    def test_invalid_field_types_and_values_are_rejected(self):
        invalid = {
            'data': (('query', ''), ('query', 1), ('evaluated_at', 1),
                     ('mode', None), ('mode', 'metadata'), ('mode', 'fulltext'),
                     ('disclosure', None), ('disclosure', 'metadata_candidates'),
                     ('items', {}), ('items', [None])),
            'item': (('id', 1), ('type', ''), ('title', None), ('description', 1),
                     ('tags', 'tag'), ('tags', [1]), ('trust', 'verified'), ('status', None),
                     ('expiry', 'fresh'), ('ref', 'book/note'), ('bundle_id', 1),
                     ('content_sha256', 'g' * 64), ('content_sha256', 'a' * 63),
                     ('upstream_revision', 1), ('generation', ''), ('generation', None),
                     ('score', -1), ('score', 1.5), ('score', True),
                     ('selection_reason', None), ('selection_reason', ''), ('snippet', None)),
            'snippet': (('path', ''), ('path', 1), ('byte_start', -1), ('byte_start', 0.5),
                        ('byte_end', -1), ('byte_end', True), ('text', None)),
        }
        for response in self.responses():
            for section, fields in invalid.items():
                for field, value in fields:
                    with self.subTest(command=response['command'], status=response['status'],
                                      section=section, field=field, value=value):
                        result = deepcopy(response)
                        target = result['data']
                        if section != 'data':
                            target = target['items'][0]
                        if section == 'snippet':
                            target = target['snippet']
                        target[field] = value
                        with self.assertRaises(ValidationError):
                            self.validator.validate(result)

    def test_disclosure_matches_the_command(self):
        for response in self.responses():
            response['data']['disclosure'] = (
                'selected_excerpts' if response['command'] == 'search' else 'indexed_snippets')
            with self.subTest(command=response['command'], status=response['status']):
                with self.assertRaises(ValidationError):
                    self.validator.validate(response)

    def test_errors_and_other_commands_do_not_require_indexed_results(self):
        for response in self.responses():
            for data in ({}, {'mode': 'full_text'}):
                with self.subTest(command=response['command'], data=data):
                    result = deepcopy(response)
                    result.update(status='error', data=data)
                    self.validator.validate(result)
            response.update(command='browse', data={'mode': 'full_text', 'items': [{}]})
            self.validator.validate(response)


if __name__ == '__main__':
    unittest.main()
