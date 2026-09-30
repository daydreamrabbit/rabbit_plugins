import unittest
from flask import Flask
from .. import rabbit_plugins as m


class Gateway:
    _engine = 'sqlite'
    def __init__(self, names):
        self.names = names
        self.calls = 0
    def fetch_all(self, query):
        self.calls += 1
        return [{'name': name} for name in self.names]


class RequestSchemaCacheTests(unittest.TestCase):
    def test_reuses_schema_only_within_same_request_and_gateway(self):
        app = Flask(__name__)
        first = Gateway(['cover_artist', 'localized_series'])
        other = Gateway([])
        with app.test_request_context('/'):
            self.assertEqual(m._optional_column_sql(first, 'books', 'b', 'cover_artist'), 'b.cover_artist')
            self.assertEqual(m._optional_column_sql(first, 'books', 'c', 'localized_series'), 'c.localized_series')
            self.assertEqual(first.calls, 1)
            self.assertEqual(m._optional_column_sql(other, 'books', 'b', 'cover_artist'), 'NULL')
        with app.test_request_context('/'):
            m._optional_column_sql(first, 'books', 'b', 'cover_artist')
            self.assertEqual(first.calls, 2)
