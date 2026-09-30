import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, session
from .. import rabbit_plugins as m


class DetailArchiveIoTests(unittest.TestCase):
    def test_detail_metadata_uses_scanned_database_values_without_archive_probe(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'Kavita.YAML').write_text('Title: 작품\n', encoding='utf-8')
            media_path = Path(folder, '작품 01.cbz')
            media_path.write_bytes(b'archive should not be opened')
            row = {
                'file_path': str(media_path),
                'file_format': 'cbz',
                'author': 'DB 작가',
                'summary': 'YAML에서 스캔한 소개',
                'localized_series': '원제',
                'release_date': '2024-01-02',
                'publication_status': '2',
                'translator': 'ComicInfo 번역가',
                'document_volume_index': 1,
                'document_volume_count': 3,
            }

            with patch.object(m, '_comicinfo_metadata', side_effect=AssertionError('archive was opened')):
                result = m._detail_file_metadata(row)

            self.assertEqual(result['writer'], 'DB 작가')
            self.assertEqual(result['translator'], 'ComicInfo 번역가')
            self.assertEqual(result['summary'], 'YAML에서 스캔한 소개')
            self.assertEqual(result['localized_series'], '원제')
            self.assertEqual((result['volume'], result['count']), (1, 3))

    def test_detail_metadata_does_not_fallback_to_comicinfo_archive(self):
        with tempfile.TemporaryDirectory() as folder:
            media_path = Path(folder, '작품 01.cbz')
            media_path.write_bytes(b'fixture')
            row = {'file_path': str(media_path), 'file_format': 'cbz'}
            with patch.object(m, '_comicinfo_metadata', side_effect=AssertionError('archive was opened')):
                self.assertEqual(m._detail_file_metadata(row)['summary'], '')

    def test_files_endpoint_does_not_probe_sidecars_or_open_series_archives(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, 'kavita.yaml').write_text('Title: 작품\n', encoding='utf-8')
            media_path = str(Path(folder, '작품 01.cbz'))
            book = {
                'id': 1, 'series_name': '작품', 'series_alias': '', 'localized_series': '원제',
                'library_id': 4, 'content_kind': 'novel', 'author': 'DB 작가', 'summary': 'YAML 소개',
                'genre': '판타지', 'tags': '웹소설', 'books_lv': 'Everyone', 'file_path': media_path,
                'file_format': 'cbz', 'file_mtime': 1, 'file_size': 10, 'cover_artist': '',
                'release_date': '2024-01-02', 'publication_status': '2',
                'document_volume_index': 1, 'document_volume_count': 3,
                'translator': '박경용',
            }
            file_row = {
                **book, 'created_at': '2024-01-02', 'total_pages': 0, 'pages_read': 0,
                'is_completed': 0, 'last_read_at': None,
            }

            class Gateway:
                def fetch_one(self, sql, _params=()):
                    if sql.startswith('SELECT name FROM libraries'):
                        return {'name': 'library'}
                    if 'COUNT(*) AS books' in sql:
                        return {'books': 1, 'libraries': 1}
                    return dict(book)

                def fetch_all(self, sql, _params=()):
                    return [dict(file_row)] if 'LEFT JOIN user_progress' in sql else []

                def get_setting(self, *_args):
                    return {'value': '{}'}

            provider = object.__new__(m.RabbitPluginsMetadataProvider)
            provider.get_db_gateway = lambda _kind: Gateway()
            provider.get_plugin_config = lambda *_args: {}
            app = Flask('detail-kavita-test')
            app.secret_key = 'fixture'

            def optional_column(_gateway, _table, alias, column):
                return f'{alias}.{column}'

            with app.test_request_context('/?book_id=1&mode=files'), \
                    patch.object(m, '_load_core_dependencies', return_value=({
                        'check_adult_permission': lambda _kind: True,
                        'check_download_permission': lambda: True,
                        'check_book_rating_permission': None,
                        'ContentRatingService': None,
                    }, None)), \
                    patch.object(m, '_optional_column_sql', side_effect=optional_column), \
                    patch.object(m, '_comicinfo_metadata', side_effect=AssertionError('archive was opened')):
                session.update(user_id=9, role='admin')
                result = provider.get_dashboard_data('general', limit=18)

            self.assertTrue(result['success'])
            self.assertEqual(result['comicinfo']['summary'], 'YAML 소개')
            self.assertEqual(result['comicinfo']['translator'], '박경용')
            self.assertEqual(result['files'][0]['file_path'], media_path)
            self.assertEqual(result['publication_volume_coverage'], {
                'known': True, 'present': 1, 'total': 3, 'missing': 2,
            })


if __name__ == '__main__':
    unittest.main()
