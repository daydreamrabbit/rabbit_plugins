"""External links must not accumulate in a single MariaDB TEXT column."""
import json
import unittest
from .. import rabbit_plugins as m
from .test_cover_priority_audit import Gateway


class ExternalLinkStorageTests(unittest.TestCase):
    def test_many_series_exceed_aggregate_limit_without_overflow(self):
        g = Gateway()
        for n in range(600):
            m._remember_external_links(g, 2, '시노비고토' * 12 + str(n),
                                       'https://ridibooks.com/books/' + str(n))
        values = m._external_link_preferences(g)
        self.assertEqual(len(values), 600)
        self.assertGreater(len(json.dumps(values, ensure_ascii=False).encode()), 65535)
        self.assertIsNone(g.get_setting(m._EXTERNAL_LINK_PREFERENCES_KEY))

    def test_full_legacy_is_preserved_and_new_entry_overrides(self):
        g = Gateway()
        legacy = {'2:시노비고토': 'https://old.example/1', '2:다른 작품': 'https://old.example/2',
                  'padding': 'x' * 65000}
        raw = json.dumps(legacy, ensure_ascii=False)
        g.set_setting(m._EXTERNAL_LINK_PREFERENCES_KEY, raw)
        m._remember_external_links(g, 2, '시노비고토', 'https://ridibooks.com/books/1')
        values = m._external_link_preferences(g)
        self.assertEqual(values['2:시노비고토'], 'https://ridibooks.com/books/1')
        self.assertEqual(values['2:다른 작품'], legacy['2:다른 작품'])
        self.assertEqual(g.get_setting(m._EXTERNAL_LINK_PREFERENCES_KEY)['value'], raw)

    def test_library_scope_and_idempotent_write(self):
        g = Gateway()
        for lib in (2, 3, 2):
            m._remember_external_links(g, lib, '시노비고토', f'https://example.com/{lib}')
        self.assertEqual(len(m._external_link_preferences(g)), 2)
        self.assertEqual(len(g.fetch_all('SELECT * FROM settings')), 2)

    def test_corrupt_record_does_not_hide_legacy(self):
        g = Gateway()
        g.set_setting(m._EXTERNAL_LINK_PREFERENCES_KEY, '{"2:legacy":"https://example.com/1"}')
        for index, raw in enumerate(('{broken', '[]', '{"identity":2,"links":[]}')):
            g.set_setting(m._EXTERNAL_LINK_PREFIX + str(index), raw)
        self.assertEqual(m._external_link_preferences(g), {'2:legacy': 'https://example.com/1'})
