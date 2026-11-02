import json
import unittest

from .. import rabbit_plugins as m


class ParentRelationTests(unittest.TestCase):
    def test_parent_points_to_main_story(self):
        for source_kind in ('parent', 'parent_story'):
            with self.subTest(source_kind=source_kind):
                row = {'relationships_v2': json.dumps([
                    {'relation_type': source_kind, 'to_series_id': 89},
                ])}
                relations = m._series_relation_ids(row)
                self.assertEqual(relations['main_story'], [89])
                self.assertEqual(relations['other'], [])

    def test_unrecognized_relation_stays_other(self):
        row = {'relationships_v2': json.dumps([
            {'relation_type': 'unrecognized', 'to_series_id': 123},
        ])}
        self.assertEqual(m._series_relation_ids(row)['other'], [123])

    def test_source_and_distinct_relation_types_keep_their_meaning(self):
        expected = {
            'source': 'original', 'parody': 'parody', 'contains': 'contains',
            'compilation': 'compilation', 'cameo': 'cameo', 'series': 'series',
            'remake': 'remake', 'crossover': 'crossover', 'summary': 'summary',
            'reboot': 'reboot', 'expansion': 'expansion',
            'character_focus': 'character_focus', 'uncollected': 'uncollected',
        }
        for source_kind, display_kind in expected.items():
            with self.subTest(source_kind=source_kind):
                row = {'relationships_v2': json.dumps([
                    {'relation_type': source_kind, 'to_series_id': 123},
                ])}
                relations = m._series_relation_ids(row)
                self.assertEqual(relations[display_kind], [123])
                self.assertEqual(relations['other'], [])


if __name__ == '__main__':
    unittest.main()
