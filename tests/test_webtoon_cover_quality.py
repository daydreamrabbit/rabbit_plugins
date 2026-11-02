import io
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image
from .. import rabbit_plugins as m


class WebtoonCoverQualityTests(unittest.TestCase):
    target = {'series_name': '보고 있지？', 'link': 'https://series.naver.com/comic/detail.series?productNo=8391026'}
    base = 'https://cdn.mangaupdates.com/image/small.jpg'
    high = 'https://comicthumb-phinf.pstatic.net/high.jpg'

    def choose(self, sizes, choices=(), order='series_db,naver', existing='', details=None):
        provider = object.__new__(m.RabbitPluginsMetadataProvider)
        with patch.object(m, '_metadata_cover_size', side_effect=lambda url: sizes.get(url, (0, 0))) as probe, \
                patch.object(m, '_stored_cover_size', return_value=(455, 660) if existing else (0, 0)), \
                patch.object(m, '_remote_fetch_metadata', return_value=details or {}) as remote:
            selected = provider._webtoon_cover_selection(
                self.target, self.base, 'series_db', {'webtoon_cover_candidates': list(choices)},
                {'metadata_sources': order}, existing_cover=existing)
        return selected, probe, remote

    def test_sufficient_series_db_image_keeps_priority_without_other_requests(self):
        chosen, probe, remote = self.choose({self.base: (500, 750)})
        self.assertEqual(chosen, (self.base, 'series_db'))
        self.assertEqual(probe.call_count, 1)
        remote.assert_not_called()

    def test_small_image_uses_verified_larger_provider_and_configured_priority(self):
        ridi = 'https://img.ridicdn.net/cover/1/xxlarge'
        chosen, _, remote = self.choose(
            {self.base: (160, 231), self.high: (690, 1000), ridi: (600, 900)},
            [{'source': 'naver', 'cover': self.high}, {'source': 'ridi', 'cover': ridi}],
            order='series_db,ridi,naver')
        self.assertEqual(chosen, (ridi, 'ridi'))
        remote.assert_not_called()

    def test_unknown_failed_smaller_or_landscape_image_does_not_replace_cover(self):
        for size in ((0, 0), (100, 150), (900, 400)):
            with self.subTest(size=size):
                chosen, _, _ = self.choose(
                    {self.base: (160, 231), self.high: size}, [{'source': 'naver', 'cover': self.high}])
                self.assertEqual(chosen, (self.base, 'series_db'))

    def test_disabled_provider_is_not_used(self):
        chosen, probe, remote = self.choose(
            {self.base: (160, 231), self.high: (690, 1000)},
            [{'source': 'naver', 'cover': self.high}], order='series_db')
        self.assertEqual(chosen, (self.base, 'series_db'))
        self.assertEqual(probe.call_count, 1)
        remote.assert_not_called()

    def test_stored_catalogue_link_requires_exact_work_title_and_original_image_url(self):
        for title, success in (('보고 있지? [독점]', True), ('보고 있지? 외전', False)):
            with self.subTest(title=title):
                chosen, _, remote = self.choose(
                    {self.base: (160, 231), self.high: (690, 1000)},
                    details={'title': title, 'cover': self.high + '?type=m260'})
                self.assertEqual(chosen, (self.high, 'naver') if success else (self.base, 'series_db'))
                self.assertEqual(remote.call_count, 1)

    def test_existing_high_resolution_file_is_reused_without_network_fallback(self):
        chosen, _, remote = self.choose({self.base: (160, 231)}, existing='11/shared.webp')
        self.assertEqual(chosen, ('11/shared.webp', 'stored'))
        remote.assert_not_called()

    def test_unknown_primary_dimensions_keep_existing_high_resolution_cover(self):
        chosen, _, _ = self.choose({}, existing='11/shared.webp')
        self.assertEqual(chosen, ('11/shared.webp', 'stored'))

    def test_at_most_three_alternative_images_are_probed(self):
        choices = [{'source': 'naver', 'cover': f'https://image.example/{i}'} for i in range(10)]
        _, probe, _ = self.choose({self.base: (160, 231)}, choices)
        self.assertEqual(probe.call_count, 4)

    def test_merging_keeps_successful_cover_options_only_for_webtoons(self):
        provider = object.__new__(m.RabbitPluginsMetadataProvider)
        candidates = [{'source': 'series_db', 'cover': self.base}, {'source': 'naver', 'cover': self.high}]
        result = provider._merge_metadata_candidates(candidates, 'manhwa')
        self.assertEqual(result['cover_source'], 'series_db')
        self.assertEqual(result['webtoon_cover_candidates'], candidates)
        self.assertNotIn('webtoon_cover_candidates', provider._merge_metadata_candidates(candidates, 'manga'))

    def test_dimension_probe_reads_image_headers_and_closes_response(self):
        data = io.BytesIO()
        Image.new('RGB', (690, 1000)).save(data, format='JPEG')
        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_content.return_value = iter([data.getvalue()[:2048]])
        with patch('requests.get', return_value=response) as get:
            self.assertEqual(m._metadata_cover_size(self.high), (690, 1000))
        self.assertTrue(get.call_args.kwargs['stream'])
        response.__exit__.assert_called_once()

    def test_dimension_probe_caps_bytes_and_treats_http_errors_as_unknown(self):
        count = []
        def chunks():
            for _ in range(30):
                count.append(1)
                yield b'\x00' * 16384
        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_content.return_value = chunks()
        with patch('requests.get', return_value=response):
            self.assertEqual(m._metadata_cover_size(self.high), (0, 0))
        self.assertLessEqual(len(count), 16)
        with patch('requests.get', side_effect=TimeoutError):
            self.assertEqual(m._metadata_cover_size(self.high), (0, 0))


if __name__ == '__main__':
    unittest.main()
