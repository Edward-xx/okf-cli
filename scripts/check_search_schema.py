"""Exercise the metadata-search response contract with isolated synthetic data."""
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

    def test_other_commands_and_full_text_do_not_use_metadata_item_constraints(self):
        for command, data in (('browse', {'items': [{'kind': 'directory'}]}),
                               ('read', {'content': 'original bytes'}),
                               ('context', {'mode': 'metadata', 'items': [{'ref': '@book/note'}]}),
                               ('search', {'mode': 'full_text', 'items': [{'snippet': {}}]})):
            with self.subTest(command=command, data=data):
                response = deepcopy(self.response)
                response.update(command=command, data=data)
                self.validator.validate(response)


if __name__ == '__main__':
    unittest.main()
