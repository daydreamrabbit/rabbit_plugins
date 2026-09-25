"""Banner upload contract for the plugin detail editor."""
import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask, session
from PIL import Image

from ..rabbit_plugins import RabbitPluginsMetadataProvider


class Gateway:
    def __init__(self):
        self.rows = [
            {'id': 1, 'series_name': '작품', 'library_id': 17, 'banner_image': None},
            {'id': 2, 'series_name': '작품', 'library_id': 17, 'banner_image': None},
            {'id': 3, 'series_name': '작품', 'library_id': 18, 'banner_image': None},
        ]

    def fetch_one(self, sql, params):
        return next(({'id': row['id']} for row in self.rows
                     if row['series_name'] == params[0] and row['library_id'] == params[1]), None)

    def execute(self, sql, params):
        path, updated_at, name, library_id = params
        for row in self.rows:
            if row['series_name'] == name and row['library_id'] == library_id:
                row['banner_image'] = path
                row['banner_updated_at'] = updated_at


class BannerEditTests(unittest.TestCase):
    def setUp(self):
        self.provider = object.__new__(RabbitPluginsMetadataProvider)
        self.gateway = Gateway()
        self.provider.get_db_gateway = lambda db_type: self.gateway
        self.app = Flask(__name__)
        self.app.secret_key = 'test-only'
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    @staticmethod
    def image_data(color):
        output = io.BytesIO()
        Image.new('RGB', (40, 20), color).save(output, format='PNG')
        return 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode('ascii')

    def test_upload_replaces_only_the_selected_library_and_can_remove(self):
        with self.app.test_request_context('/'), patch(
                'services.cover_storage_service.get_covers_dir', return_value=self.temp.name):
            session['role'] = 'admin'
            context = {'series_name': '작품', 'library_id': 17,
                       'banner_data': self.image_data('red')}
            first = self.provider.run_context_menu_action('general', 'save_series_banner', context)
            self.assertTrue(first['success'])
            path = Path(self.temp.name) / first['banner_image']
            self.assertTrue(path.is_file())
            with Image.open(path) as image:
                self.assertEqual(image.format, 'WEBP')
                self.assertEqual(image.size, (40, 20))
            self.assertEqual([row['banner_image'] for row in self.gateway.rows],
                             [first['banner_image'], first['banner_image'], None])

            context['banner_data'] = self.image_data('blue')
            second = self.provider.run_context_menu_action('general', 'save_series_banner', context)
            self.assertEqual(second['banner_image'], first['banner_image'])
            self.assertEqual(len(list(path.parent.glob('banner_*.webp'))), 1)
            with Image.open(path) as image:
                self.assertGreater(image.getpixel((0, 0))[2], 200)

            removed = self.provider.run_context_menu_action('general', 'save_series_banner',
                {'series_name': '작품', 'library_id': 17, 'remove_banner': True})
            self.assertTrue(removed['success'])
            self.assertEqual([row['banner_image'] for row in self.gateway.rows], [None, None, None])

    def test_rejects_unapproved_user_invalid_image_and_other_library(self):
        with self.app.test_request_context('/'), patch(
                'services.cover_storage_service.get_covers_dir', return_value=self.temp.name):
            context = {'series_name': '작품', 'library_id': 17,
                       'banner_data': self.image_data('red')}
            session['role'] = 'user'
            self.assertFalse(self.provider.run_context_menu_action('general', 'save_series_banner', context)['success'])
            session['role'] = 'admin'
            for invalid in ('data:image/png;base64,' + base64.b64encode(b'bad').decode('ascii'),
                            'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=', 'https://example.com/x.png'):
                context['banner_data'] = invalid
                self.assertFalse(self.provider.run_context_menu_action('general', 'save_series_banner', context)['success'])
            context['banner_data'] = self.image_data('red')
            context['library_id'] = 99
            self.assertFalse(self.provider.run_context_menu_action('general', 'save_series_banner', context)['success'])
            self.assertTrue(all(row['banner_image'] is None for row in self.gateway.rows))
