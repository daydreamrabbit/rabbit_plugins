import unittest
from unittest.mock import patch
from .. import rabbit_plugins as m


class SearchExclusionTests(unittest.TestCase):
    def test_preserves_parentheses_and_brackets_by_default(self):
        title = '나「」만「」의「」비「」밀「(나만의 비밀) [스미노 요루]'
        self.assertEqual(m._metadata_search_query(title, {}), title)

    def test_both_configured_lists_exclude_only_literal_terms(self):
        config = {'metadata_search_remove_keywords': '[작가]',
                  'metadata_search_keep_keywords': '(리디);[미즈]'}
        self.assertEqual(m._metadata_search_query(
            '제목(나만의 비밀) [작가] (리디) [미즈]', config),
            '제목(나만의 비밀)')

    def test_regex_symbols_are_literal_and_case_insensitive(self):
        self.assertEqual(m._metadata_search_query(
            '제목(부제) C++ a.b axb',
            {'metadata_search_keep_keywords': 'c++,a.b'}), '제목(부제) axb')

    def test_remote_cover_policy_never_opens_archive(self):
        with patch.object(m, '_metadata_internal_cover', side_effect=AssertionError):
            for ext in ('pdf', 'epub', 'cbz', 'zip'):
                row = {'file_path': '/remote/book.' + ext}
                self.assertTrue(m._metadata_cover_eligible(row, False, False))
                row['cover_image'] = 'existing.webp'
                self.assertFalse(m._metadata_cover_eligible(row, False, False))
                self.assertTrue(m._metadata_cover_eligible(row, False, True))
                row['metadata_locked'] = 1
                self.assertFalse(m._metadata_cover_eligible(row, False, False))
