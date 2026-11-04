import json
import unittest
from unittest.mock import patch

from .. import rabbit_plugins as m, manual_relations


class RelationGraphTests(unittest.TestCase):
    def item(self, name, library=2):
        return {'library_id': library, 'series_name': name}

    def row(self, kind, target):
        return {'relationships_v2': json.dumps([{'relation_type': kind, 'to_series_id': target}])}

    def test_sequence_is_directed_and_reciprocal_edges_are_deduplicated(self):
        items = {1: self.item('려'), 2: self.item('화'), 3: self.item('름')}
        rows = {1: self.row('sequel', 2), 2: self.row('prequel', 1), 3: self.row('prequel', 2)}
        edges = m.RabbitPluginsMetadataProvider._source_graph_edges(rows, items)
        self.assertEqual(edges, [
            {'source_key': ['2', '려'], 'target_key': ['2', '화'], 'relation_type': 'sequel'},
            {'source_key': ['2', '화'], 'target_key': ['2', '름'], 'relation_type': 'sequel'},
        ])

    def test_does_not_expose_unknown_or_same_work_and_preserves_relationship_kind(self):
        items = {1: self.item('외전'), 2: self.item('파생작'), 3: self.item('외전')}
        rows = {1: self.row('spin_off', 2), 2: self.row('sequel', 99), 3: self.row('prequel', 1)}
        edges = m.RabbitPluginsMetadataProvider._source_graph_edges(rows, items)
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]['relation_type'], 'spin_off')

    def test_merge_removes_invisible_nodes_and_disabled_links(self):
        edge = {'source_key': ['2', '려'], 'target_key': ['2', '화'], 'relation_type': 'sequel'}
        hidden = dict(edge, target_key=['9', '접근 불가'])
        result = {'relations': {'spin_off': [self.item('려'), self.item('화')]},
                  'relation_graph': {'edges': [edge, hidden]}}
        def manual_edges(gateway, disabled=False):
            return [('disabled', [[2, '려'], [2, '화']], ['sequel', 'prequel'])] if disabled else []
        with patch.object(manual_relations, 'edges', side_effect=manual_edges):
            self.assertEqual(m.RabbitPluginsMetadataProvider._merge_relation_graph(None, result), {'edges': []})

    def test_manual_type_overrides_automatic_sequence(self):
        from .. import manual_relations
        result = {'relations': {'other': [self.item('외전'), self.item('파생작')]}, 'relation_graph': {'edges': [
            {'source_key': ['2', '외전'], 'target_key': ['2', '파생작'], 'relation_type': 'sequel'}]}}
        with patch.object(manual_relations, 'edges', side_effect=lambda gateway, disabled=False:
                          [] if disabled else [('manual', [[2, '외전'], [2, '파생작']], ['spin_off', 'main_story'])]):
            edges = m.RabbitPluginsMetadataProvider._merge_relation_graph(None, result)['edges']
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]['relation_type'], 'spin_off')
        self.assertTrue(edges[0]['manual'])
