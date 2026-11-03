import copy
import sys
import unittest
from unittest.mock import patch
from .. import rabbit_plugins as m


class DetailHoldingsTests(unittest.TestCase):
    def test_shared_core_preserves_metadata_and_detects_missing_first_volume(self):
        try:
            from services.series_coverage import coverage
        except ImportError:
            self.skipTest('shared coverage helper is optional on older cores')
        rows = [{'document_volume_index': 2, 'document_volume_count': 2,
                 'metadata_title': '02권 (완결)'}]
        original = copy.deepcopy(rows)
        with patch.object(m, '_comicinfo_metadata', side_effect=AssertionError('archive opened')):
            result = m._detail_holdings_coverage(rows, '')
        self.assertEqual(result, coverage(rows, ''))
        self.assertEqual(result['holdings_status'], 'missing')
        self.assertEqual(result['holdings_label'], '1권 누락')
        self.assertEqual(rows, original)

    def test_older_core_does_not_infer_missing_from_ongoing_count(self):
        rows = [{'document_volume_index': 1, 'document_volume_count': 3}]
        with patch.dict(sys.modules, {'services.series_coverage': None}):
            result = m._detail_holdings_coverage(rows, '0')
        self.assertEqual(result['holdings_status'], 'unknown')

    def test_older_core_explicit_complete_coverage(self):
        rows = [{'document_volume_index': 2, 'document_volume_count': 2}]
        with patch.dict(sys.modules, {'services.series_coverage': None}):
            result = m._detail_holdings_coverage(rows, '2')
        self.assertEqual(result['holdings_status'], 'missing')

    def test_one_shot_sentinel_is_not_a_volume(self):
        rows = [{'document_volume_index': 100000, 'document_volume_count': 1}]
        self.assertNotEqual(m._detail_holdings_coverage(rows, '')['holdings_status'], 'missing')
