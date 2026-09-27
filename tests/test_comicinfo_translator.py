import tempfile
import unittest
import zipfile
from pathlib import Path

from .. import rabbit_plugins as plugin


class ComicInfoTranslatorTests(unittest.TestCase):
    def test_translator_is_read_from_comicinfo(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '작품 01권.cbz'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('ComicInfo.xml',
                    '<ComicInfo><Title>작품</Title><Writer>아키모토 아키</Writer><Translator>한호성</Translator></ComicInfo>')
            info = plugin._read_comicinfo(str(path), 'cbz', '', 0, path.stat().st_size)
        self.assertEqual(info['translator'], '한호성')
        self.assertEqual(info['writer'], '아키모토 아키')

    def test_missing_translator_stays_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '작품 01권.cbz'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('ComicInfo.xml', '<ComicInfo><Title>작품</Title></ComicInfo>')
            info = plugin._read_comicinfo(str(path), 'cbz', '', 0, path.stat().st_size)
        self.assertEqual(info['translator'], '')


if __name__ == '__main__':
    unittest.main()
