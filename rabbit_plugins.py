# 도서 상세 탭에 필요한 파일 정보, 관련작품, 추천항목을 제공합니다.
import os
import html as html_lib
from html.parser import HTMLParser

try:
    import fcntl
except ImportError:  # Windows has no fcntl; provide the small flock subset used below.
    import msvcrt

    class _FcntlCompat:
        LOCK_EX = 1
        LOCK_NB = 2
        LOCK_UN = 8

        @staticmethod
        def flock(file_descriptor, operation):
            non_blocking = bool(operation & _FcntlCompat.LOCK_NB)
            if operation & _FcntlCompat.LOCK_UN:
                mode = msvcrt.LK_UNLCK
            else:
                mode = msvcrt.LK_NBLCK if non_blocking else msvcrt.LK_LOCK

            # msvcrt.locking locks bytes, so keep one byte in the sidecar file.
            # The file is opened in append mode by both callers; concurrent first
            # use is harmless because every process locks byte zero.
            if not (operation & _FcntlCompat.LOCK_UN) and os.fstat(file_descriptor).st_size == 0:
                current_position = os.lseek(file_descriptor, 0, os.SEEK_CUR)
                try:
                    os.lseek(file_descriptor, 0, os.SEEK_END)
                    os.write(file_descriptor, b'\0')
                finally:
                    os.lseek(file_descriptor, current_position, os.SEEK_SET)

            os.lseek(file_descriptor, 0, os.SEEK_SET)
            try:
                msvcrt.locking(file_descriptor, mode, 1)
            except OSError as error:
                if non_blocking:
                    raise BlockingIOError(error.errno, str(error)) from error
                raise

    fcntl = _FcntlCompat()
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import unicodedata
import zipfile
from collections import deque
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus, urljoin, urlparse
from xml.etree import ElementTree as ET

from flask import has_request_context, request, session
from plugins.metadata.base import BaseMetadataProvider
from .provider_search import SOURCE_KINDS, SOURCE_LABELS, NOVEL_GENRES, search_novelpia_author, search as search_additional_provider

PLUGIN_VERSION = '3.2.1'
REQUIRED_CORE_COMMIT = '9ba7c93'
SERIES_TYPES_BY_LIBRARY = {
    'manga': {'manga', 'manhwa', 'manhua', 'oel'},
    'manhwa': {'manga', 'manhwa', 'manhua', 'oel'},
    'novel': {'novel'},
    'book': {'other'},
}
RELATION_FIELDS = {
    'main_story': 'relationships_main_story',
    'prequel': 'relationships_prequel',
    'sequel': 'relationships_sequel',
    'spin_off': 'relationships_spin_off',
    'side_story': 'relationships_side_story',
    'alternative': 'relationships_alternative',
    'adaptation': 'relationships_adaptation',
    'other': 'relationships_other',
}

METADATA_SOURCES = ('series_db', 'ridi', 'naver', 'kyobo', 'yes24', *SOURCE_KINDS)
METADATA_SOURCE_LABELS = {
    'series_db': '데이터베이스', 'ridi': '리디', 'naver': '네이버시리즈',
    'kyobo': '교보문고', 'yes24': '예스24',
    **SOURCE_LABELS,
}
METADATA_FIELDS = (
    'title', 'localized_series', 'author', 'cover_artist', 'publisher', 'summary',
    'genre', 'tags', 'release_date', 'isbn', 'link', 'cover',
    'publication_status', 'books_lv', 'publication_start_date', 'publication_end_date',
)
METADATA_COVER_KIND_PATTERN = re.compile(r'^[a-z][a-z0-9_-]{0,23}$')
YES24_NOVEL_SECTIONS = frozenset({
    # eBook>판타지/무협 (017001049), eBook>로맨스 (017001046),
    # eBook>라이트노벨 (017001063). goodsSortNm can omit the parent label.
    '판타지/무협', '판타지', '퓨전', '현대', '게임', '스포츠', '대체역사', '무협',
    '로맨스', '현대물', '역사/시대물', 'tl/삽화소설', '할리퀸', '로맨틱판타지',
    '라이트노벨', '시프트노벨', 'nt 노벨', 'l노벨', '노블엔진', '시드노벨',
    's노벨', 'ak novel', '길찾기', '나이트노벨', '노블오즈', '익스트림노벨',
    '제이노블', '기타 라이트노벨',
})

# Scanner hooks can be delivered by more than one background thread (the
# ``new books`` and ``scan completed`` events are intentionally independent in
# the core).  Automatic collection must therefore serialize its database and
# remote-provider work.  The lock is process-local, which is sufficient for a
# single BookOasis worker and avoids starting a second nested daemon thread.
_AUTO_COLLECT_LOCK = threading.Lock()
_COMPLETION_SCAN_MARKERS = deque(maxlen=512)
_COVER_PREFERENCE_LOCK = threading.Lock()
_COVER_RECONCILE_START_LOCK = threading.Lock()
_COVER_RECONCILE_STARTED = False
_EXTERNAL_COVER_PREFERENCES_KEY = 'rabbit_plugins:external-cover-preferences:v1'


def _setting_text(gateway, key, default=''):
    row = gateway.get_setting(key, None)
    if isinstance(row, dict):
        return str(row.get('value') or default)
    return str(row or default)


def _external_cover_preferences(gateway):
    try:
        value = json.loads(_setting_text(gateway, _EXTERNAL_COVER_PREFERENCES_KEY, '{}'))
    except (TypeError, ValueError, json.JSONDecodeError):
        value = {}
    return value if isinstance(value, dict) else {}


def _remember_external_cover(gateway, book_id, cover_path, library_id, content_kind):
    """Remember the plugin-selected cover independently from scanner caches.

    Remote Lazy Scanner work can finish minutes after the metadata hook and
    replace ``books.cover_image`` with Kavita/cover.jpg output.  The preference
    lets the plugin restore the already-downloaded external cover without
    fetching the provider again.
    """
    if not book_id or not cover_path:
        return
    with _COVER_PREFERENCE_LOCK:
        preferences = _external_cover_preferences(gateway)
        preferences[str(int(book_id))] = {
            'path': str(cover_path),
            'library_id': int(library_id),
            'content_kind': _metadata_content_kind(content_kind),
        }
        gateway.set_setting(
            _EXTERNAL_COVER_PREFERENCES_KEY,
            json.dumps(preferences, ensure_ascii=False, separators=(',', ':')),
        )


def _reconcile_external_covers(provider, db_type):
    """Restore configured external covers after delayed remote scanner writes."""
    gateway = provider.get_db_gateway(db_type)
    preferences = _external_cover_preferences(gateway)
    if not preferences:
        return 0
    config = provider.get_plugin_config(db_type, {}) or {}
    if not _config_bool(config.get('metadata_collect_cover'), True):
        return 0

    ids = []
    for raw_id in preferences:
        try:
            ids.append(int(raw_id))
        except (TypeError, ValueError):
            continue
    changed = 0
    for start in range(0, len(ids), 200):
        chunk = ids[start:start + 200]
        placeholders = ','.join('?' for _ in chunk)
        rows = gateway.fetch_all(
            'SELECT b.id, b.cover_image, b.library_id, '
            'COALESCE(NULLIF(l.content_kind, ""), "unspecified") AS content_kind '
            'FROM books b LEFT JOIN libraries l ON l.id = b.library_id '
            f'WHERE b.id IN ({placeholders}) AND COALESCE(b.is_deleted, 0) = 0',
            tuple(chunk),
        )
        for row in rows:
            preference = preferences.get(str(row.get('id'))) or {}
            desired = str(preference.get('path') or '').strip()
            kind = _metadata_content_kind(row.get('content_kind'))
            metadata_overwrite = _metadata_overwrite_enabled(config, kind)
            if not desired or not _metadata_cover_enabled(config, kind):
                continue
            if not _metadata_cover_overwrite_enabled(config, kind, metadata_overwrite):
                continue
            if str(row.get('cover_image') or '').strip() == desired:
                continue
            gateway.execute(
                'UPDATE books SET cover_image = ?, cover_updated_at = CURRENT_TIMESTAMP '
                'WHERE id = ? AND COALESCE(is_deleted, 0) = 0',
                (desired, row['id']),
            )
            changed += 1
    if changed:
        print(f'[RabbitPlugins-Metadata] delayed scanner cover restore db={db_type} changed={changed}')
    return changed


def _config_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in ('1', 'true', 'yes', 'on')


def _config_list(value, allowed):
    if isinstance(value, (list, tuple)):
        raw = value
    else:
        raw = str(value or '').replace(';', ',').split(',')
    result = []
    for item in raw:
        key = str(item or '').strip().casefold()
        if key in allowed and key not in result:
            result.append(key)
    return result


def _metadata_source_order(config):
    configured = _config_list(config.get('metadata_sources'), METADATA_SOURCES)
    values = configured if 'metadata_sources' in config else list(METADATA_SOURCES)
    # Each catalogue/webtoon pair moves as one priority block. Activation is
    # still independent, so a block can contain only its enabled member.
    for group in (('naver', 'naver_webtoon'), ('kakaopage', 'kakao_webtoon')):
        enabled = [source for source in group if source in values]
        if not enabled:
            continue
        insert_at = min(values.index(source) for source in enabled)
        values = [source for source in values if source not in set(group)]
        values[insert_at:insert_at] = enabled
    return values


def _metadata_source_supports_kind(source, content_kind):
    kinds = SOURCE_KINDS.get(source)
    if not kinds:
        return True
    return content_kind in kinds if isinstance(kinds, (tuple, list, set)) else kinds == content_kind


def _metadata_content_kind(value):
    """Normalize core/custom library labels before choosing provider tabs."""
    text = str(value or '').strip().casefold()
    if text in ('novel', '소설', '라노벨', '라이트노벨', '웹소설'):
        return 'novel'
    if text in ('manga', '만화', 'comic', 'comics'):
        return 'manga'
    if text in ('manhwa', '웹툰'):
        return 'manhwa'
    if text in ('book', '도서'):
        return 'book'
    return text


_BOOK_FOLDER_AUTHOR_NOISE = {
    '단행', '단행본', '도서', '연재', '소설', '만화', '웹툰', '일반',
    '완결', '단편', '스캔', '원본', '리디', '교보', '예스', '예스24', '네이버',
}

# Kyobo's per-volume identifiers are encoded as a 12-digit S/E code in many
# ebook filenames.  Keep this deliberately strict so ordinary numeric volume
# labels are never treated as a provider product code.
_KYOBO_SE_CODE_PATTERN = re.compile(r'(?<![A-Z0-9])([SsEe]\d{12})(?!\d)', re.IGNORECASE)


def _metadata_ebook_code(value):
    """Return a normalized Kyobo S/E product code found in a filename/query."""
    text = unicodedata.normalize('NFKC', str(value or ''))
    match = _KYOBO_SE_CODE_PATTERN.search(text)
    return match.group(1).upper() if match else ''


def _kyobo_product_url(code):
    """Build the same direct Kyobo URL used by Comic Book Butler."""
    normalized = _metadata_ebook_code(code)
    if not normalized:
        return ''
    if normalized.startswith('S'):
        return f'https://product.kyobobook.co.kr/detail/{normalized}'
    return f'https://ebook-product.kyobobook.co.kr/dig/epd/ebook/{normalized}'


def _metadata_book_folder_identity(value, content_kind):
    """Split a media folder's ``시리즈명 [작가명]`` convention.

    BookOasis stores the folder basename as ``series_name``.  For book
    libraries, manga, novels, and webtoons, a trailing bracket group can be
    the author's disambiguator.  Known source/status/resolution markers keep
    their previous meaning and are not treated as authors.
    """
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    if _metadata_content_kind(content_kind) not in {'book', 'manga', 'novel', 'manhwa'} or not text:
        return text, ''

    match = re.match(r'^(.+?)\s*\[([^\[\]\r\n]+)\]\s*$', text)
    if not match:
        return text, ''

    title = re.sub(r'\s+', ' ', match.group(1)).strip()
    author = re.sub(r'\s+', ' ', match.group(2)).strip()
    author_key = re.sub(r'\s+', '', unicodedata.normalize('NFKC', author)).casefold()
    # Do not mistake a resolution/source/status marker for an author when a
    # legacy folder happens to use the same trailing-bracket syntax.
    if (
        not title or not author or author.casefold() in _BOOK_FOLDER_AUTHOR_NOISE
        or _metadata_ebook_code(author)
        or re.fullmatch(r'\d{2,5}(?:x\d{2,5}|x)?(?:p|i)?', author_key, flags=re.IGNORECASE)
        or re.fullmatch(r'(?:vol(?:ume)?|v|권|화)\.?\d+', author_key, flags=re.IGNORECASE)
        or author_key in {'raw', 'hq', 'kr', 'jp', 'en', 'scan', 'translated'}
    ):
        return text, ''
    return title, author


def _metadata_author_parts(value):
    return [
        part.strip() for part in re.split(r'[,;|/·・]+', str(value or ''))
        if part.strip()
    ]


def _metadata_author_key(value):
    return re.sub(r'[\s()（）\[\]【】{}"\']+', '',
                  unicodedata.normalize('NFKC', str(value or '')).casefold())


def _metadata_author_matches(hint, candidate):
    """Return whether a provider author contains the folder author hint."""
    hint_parts = [_metadata_author_key(part) for part in _metadata_author_parts(hint)]
    candidate_parts = [_metadata_author_key(part) for part in _metadata_author_parts(candidate)]
    hint_parts = [part for part in hint_parts if part]
    candidate_parts = [part for part in candidate_parts if part]
    if not hint_parts or not candidate_parts:
        return False
    return any(
        hint_part == candidate_part
        or hint_part in candidate_part
        or candidate_part in hint_part
        for hint_part in hint_parts
        for candidate_part in candidate_parts
    )


def _metadata_candidate_author(candidate):
    values = []
    metadata = candidate.get('metadata') or {}
    if isinstance(metadata, dict):
        # A search card may shorten credits to "외 N명". The hydrated detail
        # contains the full names and is the safer identity for auto matching.
        values.extend((metadata.get('author'), metadata.get('writer')))
    values.extend((candidate.get('author'), candidate.get('_match_author')))
    return next((str(value).strip() for value in values if str(value or '').strip()), '')


def _metadata_book_type_value(value):
    """Normalize a media type supplied by an older core/detail bundle."""
    text = str(value or '').strip().casefold()
    if text in ('novel', '소설', '라노벨', '라이트노벨', '웹소설'):
        return 'novel'
    if text in ('webtoon', '웹툰'):
        return 'webtoon'
    if text in ('series', '연재', '만화 연재'):
        return 'series'
    if text in ('single', '단행', '단행본', '만화 e북', '만화 ebook'):
        return 'single'
    if text in ('book', '도서'):
        return 'book'
    return text


def _metadata_value_map(value):
    """Parse one value conversion per line: ``incoming -> replacement``."""
    rules = {}
    chunks = []
    for line in re.split(r'[\r\n;]+', str(value or '')):
        chunks.extend(re.split(r'\s*,\s*(?=[^,]+?\s*(?:=>|->|=))', line))
    for chunk in chunks:
        match = re.match(r'^(.+?)\s*(?:=>|->|=)\s*(.+)$', chunk.strip())
        if not match:
            continue
        source = re.sub(r'\s+', ' ', match.group(1)).strip()
        replacement = re.sub(r'\s+', ' ', match.group(2)).strip()
        if source and replacement:
            rules[source.casefold()] = replacement
    return rules


def _metadata_convert_terms(value, rules):
    if not value or not rules:
        return value
    converted = []
    for term in re.split(r'[,|\n]', str(value or '')):
        term = re.sub(r'\s+', ' ', term).strip()
        if not term:
            continue
        converted.append(rules.get(term.casefold(), term))
    return _join_terms(converted)


def _metadata_apply_conversions(metadata, config):
    """Apply configured genre and publisher conversions at save time."""
    result = dict(metadata or {})
    genre_rules = _metadata_value_map(config.get('metadata_genre_map'))
    publisher_rules = _metadata_value_map(config.get('metadata_publisher_map'))
    if result.get('genre'):
        result['genre'] = _metadata_convert_terms(result['genre'], genre_rules)
    if result.get('publisher'):
        result['publisher'] = _metadata_convert_terms(result['publisher'], publisher_rules)
    return result


def _metadata_internal_cover(row):
    """Keep the reader's own cover; inspect ZIP entries without extracting files.

    Unavailable archives are left to the scanner rather than replaced remotely.
    """
    path = str(row.get('file_path') or '')
    extension = Path(path).suffix.lower()
    if extension in {'.pdf', '.cbr', '.rar', '.cb7', '.7z', '.jpg', '.jpeg', '.png', '.webp', '.avif'}:
        return True
    if extension in {'.epub', '.cbz', '.zip'}:
        try:
            with zipfile.ZipFile(path) as archive:
                return any(not entry.is_dir() and entry.file_size > 0
                           and Path(entry.filename).suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.avif', '.svg'}
                           for entry in archive.infolist())
        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile):
            return True
    return False


def _metadata_cover_eligible(row, webtoon, overwrite, source='', per_volume=False):
    if int(row.get('metadata_locked') or 0) == 1 and not overwrite:
        return False
    if webtoon:
        return True
    # A PDF has a renderable first page, but BookOasis may not have generated
    # its cover yet (for example after a remote or deferred scan). An external
    # cover can fill that genuinely empty slot. Keep an existing cover intact.
    if Path(str(row.get('file_path') or '')).suffix.casefold() == '.pdf':
        return not row.get('cover_image')
    if source == 'ridi' and per_volume:
        # A remote archive is deliberately treated as having an internal
        # cover because checking it would open the remote file.  Ridi's
        # volume list gives us an exact cover without that I/O, so allow it
        # when this row has no saved cover. Existing covers still follow the
        # administrator's overwrite setting.
        return overwrite or not row.get('cover_image')
    return not _metadata_internal_cover(row) and (overwrite or not row.get('cover_image'))


def _metadata_cover_enabled(config, content_kind):
    """Return whether cover downloads are enabled for this library type.

    Missing ``metadata_cover_kinds`` keeps the previous behavior and allows all
    types. An explicitly empty value means that no type should download covers.
    """
    if not _config_bool(config.get('metadata_collect_cover'), True):
        return False
    if 'metadata_cover_kinds' not in config:
        return True
    raw = config.get('metadata_cover_kinds')
    values = raw if isinstance(raw, (list, tuple)) else str(raw or '').replace(';', ',').split(',')
    selected = {
        str(item).strip().casefold() for item in values
        if METADATA_COVER_KIND_PATTERN.fullmatch(str(item).strip().casefold())
    }
    kind = str(content_kind or 'unspecified').strip().casefold()
    if not METADATA_COVER_KIND_PATTERN.fullmatch(kind):
        kind = 'unspecified'
    return kind in selected


def _metadata_kind_enabled(config, key, content_kind, default=True):
    """Check a dynamic library type setting while keeping old configs valid."""
    if key not in config:
        return default
    raw = config.get(key)
    values = raw if isinstance(raw, (list, tuple)) else str(raw or '').replace(';', ',').split(',')
    selected = {
        str(item).strip().casefold() for item in values
        if METADATA_COVER_KIND_PATTERN.fullmatch(str(item).strip().casefold())
    }
    kind = str(content_kind or 'unspecified').strip().casefold()
    if not METADATA_COVER_KIND_PATTERN.fullmatch(kind):
        kind = 'unspecified'
    return kind in selected


def _metadata_overwrite_enabled(config, content_kind):
    return (
        _config_bool(config.get('metadata_overwrite'), False)
        and _metadata_kind_enabled(config, 'metadata_overwrite_kinds', content_kind)
    )


def _metadata_overwrite_kinds(config):
    """Return automatic-overwrite kinds, or ``None`` for an old all-kind config."""
    if not _config_bool(config.get('metadata_overwrite'), False):
        return set()
    if 'metadata_overwrite_kinds' not in config:
        return None
    raw = config.get('metadata_overwrite_kinds')
    values = raw if isinstance(raw, (list, tuple)) else str(raw or '').replace(';', ',').split(',')
    return {
        str(item).strip().casefold() for item in values
        if METADATA_COVER_KIND_PATTERN.fullmatch(str(item).strip().casefold())
    }


def _metadata_manual_overwrite_enabled(config):
    """Whether an explicit admin metadata apply may replace existing values."""
    return _config_bool(config.get('metadata_manual_overwrite'), False)


def _metadata_cover_overwrite_enabled(config, content_kind, metadata_overwrite):
    if 'metadata_cover_overwrite' in config:
        enabled = _config_bool(config.get('metadata_cover_overwrite'), False)
    else:
        # Older configurations used metadata_overwrite for both metadata and covers.
        enabled = metadata_overwrite
    if not enabled:
        return False
    if 'metadata_cover_overwrite_kinds' in config:
        return _metadata_kind_enabled(config, 'metadata_cover_overwrite_kinds', content_kind)
    if 'metadata_overwrite_kinds' in config:
        return _metadata_kind_enabled(config, 'metadata_overwrite_kinds', content_kind)
    return True


def _metadata_field_selection(config, requested=None, automatic=False):
    values = _config_list(requested or config.get('metadata_fields'), METADATA_FIELDS)
    if not values:
        values = list(METADATA_FIELDS)
    if _config_bool(config.get('metadata_collect_cover'), True) and 'cover' not in values:
        values.append('cover')
    return values


def _json_values(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    try:
        parsed = json.loads(value or '[]')
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else [parsed] if isinstance(parsed, dict) else []


def _json_names(value):
    result = []
    for item in _json_values(value):
        if isinstance(item, dict):
            item = item.get('name') or item.get('title') or item.get('author')
        item = str(item or '').strip()
        if item and item not in result:
            result.append(item)
    return result


def _join_terms(value):
    raw_values = value if isinstance(value, (list, tuple)) else [value]
    values = []
    for raw_value in raw_values:
        values.extend(re.split(r'[,;|\n]', str(raw_value or '')))
    result = []
    for item in values:
        item = html_lib.unescape(str(item or '')).strip()
        if item and item != '-' and item.casefold() not in {x.casefold() for x in result}:
            result.append(item)
    return ', '.join(result)


def _merge_variant_genres(incoming, existing):
    """Merge genres while replacing mutually exclusive product variants."""
    incoming_terms = [part.strip() for part in _join_terms(incoming).split(',') if part.strip()]
    existing_terms = [part.strip() for part in _join_terms(existing).split(',') if part.strip()]
    variant_groups = (
        {'만화 e북', '만화 연재', '웹툰'},
        {'소설 e북', '웹소설', '라이트노벨'},
    )
    replaced = set()
    incoming_keys = {term.casefold() for term in incoming_terms}
    for group in variant_groups:
        group_keys = {term.casefold() for term in group}
        if incoming_keys & group_keys:
            replaced.update(group_keys)
    kept_existing = [term for term in existing_terms if term.casefold() not in replaced]
    return _join_terms([*incoming_terms, *kept_existing])


def _merge_converted_genres(incoming, existing, rules, overwrite=False):
    """Apply genre rules to saved terms before retaining them in a merge."""
    incoming = _metadata_convert_terms(incoming, rules)
    if overwrite:
        return incoming
    return _merge_variant_genres(incoming, _metadata_convert_terms(existing, rules))


def _join_links(*values):
    result = []
    for value in values:
        items = value if isinstance(value, (list, tuple)) else re.split(r'[,;|\n]', str(value or ''))
        for item in items:
            item = html_lib.unescape(str(item or '')).strip()
            parsed = urlparse(item)
            host = (parsed.hostname or '').casefold().removeprefix('www.')
            if host == 'ridibooks.com' and parsed.path.startswith('/books/'):
                # Search-result tracking parameters are not part of the book
                # identity and otherwise make the same Ridi product look like
                # two different links.
                item = f'https://ridibooks.com{parsed.path}'
            if item and item not in result:
                result.append(item)
    return ', '.join(result)


def _link_provider(value):
    """Return the metadata provider represented by one external URL."""
    host = (urlparse(str(value or '').strip()).hostname or '').casefold()
    if host == 'ridibooks.com' or host.endswith('.ridibooks.com') or host == 'ridi.com' or host.endswith('.ridi.com'):
        return 'ridi'
    if host == 'series.naver.com':
        return 'naver'
    if host == 'comic.naver.com':
        return 'naver_webtoon'
    if host.endswith('kyobobook.co.kr'):
        return 'kyobo'
    if host == 'yes24.com' or host.endswith('.yes24.com'):
        return 'yes24'
    if host.endswith('kakao.com'):
        return 'kakao'
    if host.endswith('munpia.com'):
        return 'munpia'
    if host.endswith('novelpia.com'):
        return 'novelpia'
    return ''


def _replace_provider_links(existing, incoming):
    """Replace stale editions from providers represented by incoming links.

    Applying a newly selected Ridi e-book must remove an older Ridi serial
    link while preserving unrelated Naver, Kakao, or database links.
    """
    incoming_links = _join_links(incoming).split(', ') if _join_links(incoming) else []
    incoming_providers = {_link_provider(link) for link in incoming_links}
    incoming_providers.discard('')
    existing_links = _join_links(existing).split(', ') if _join_links(existing) else []
    kept = [
        link for link in existing_links
        if not _link_provider(link) or _link_provider(link) not in incoming_providers
    ]
    return _join_links(kept, incoming_links)


def _metadata_title(value):
    text = html_lib.unescape(str(value or '')).strip()
    text = re.split(r'\s*작품\s*소개\s*[:：]', text, maxsplit=1)[0]
    return re.sub(r'\s+', ' ', text).strip()


def _ridi_search_title(value):
    """Clean provider-only labels from a Ridi search result title."""
    text = _metadata_title(value)
    return re.sub(
        r'^\s*(?:\[\s*(?:e북|전자책|웹툰|연재|웹소설|라이트노벨|라노벨|소설)\s*\]|【\s*(?:e북|전자책|웹툰|연재|웹소설|라이트노벨|라노벨|소설)\s*】)\s*',
        '', text, count=1, flags=re.IGNORECASE)


def _metadata_variant_excluded(value):
    """Reject editions that are not a normal work or volume candidate."""
    text = html_lib.unescape(str(value or '')).casefold()
    return any(marker in text for marker in (
        '세트', '전권', '합본', '묶음', '체험판', 'box set', 'boxset', 'bundle',
    ))


def _metadata_summary(value, title=''):
    """Remove provider labels that were accidentally copied into summaries.

    Some provider pages expose a description as ``제목 작품소개: 설명`` (and
    occasionally as the placeholder ``작품소개: 작품소개``).  The label and
    title are not part of the book description, so keep only the description
    while preserving any HTML such as ComicInfo ``<img>`` tags.
    """
    text = html_lib.unescape(str(value or '')).strip()
    if not text:
        return ''
    # Strip a provider's leading section heading without flattening body HTML.
    text = re.sub(r'^\s*<(h[1-6]|p|div|strong|b)\b[^>]*>\s*작품\s*소개\s*[:：]?\s*</\1>\s*', '', text, count=1, flags=re.I)
    text = re.sub(r'^\s*(?:#{1,6}\s*)?작품\s*소개(?:\s*[:：]\s*|\s+|$)', '', text, count=1)
    clean_title = _metadata_title(title)
    if clean_title:
        text = re.sub(
            r'^\s*' + re.escape(clean_title) + r'\s*작품\s*소개\s*[:：]\s*',
            '', text, count=1, flags=re.IGNORECASE)
    # Handle a title variant that differs only by a leading publisher tag.
    text = re.sub(
        r'^\s*(?:\[[^\]\r\n]+\]\s*)?[^\r\n:]{1,200}?\s+작품\s*소개\s*[:：]\s*',
        '', text, count=1, flags=re.IGNORECASE)
    # Providers sometimes repeat the label as its placeholder value.
    for _ in range(2):
        text = re.sub(r'^\s*작품\s*소개\s*[:：]\s*', '', text, count=1, flags=re.IGNORECASE)
    text = text.strip()
    if re.fullmatch(r'작품\s*소개\s*[:：]?\s*', text, flags=re.IGNORECASE):
        return ''
    return text


def _summary_needs_cleanup(value):
    """Detect the provider title/label prefix in an already stored summary."""
    raw = str(value or '').strip()
    return bool(raw and _metadata_summary(raw) != raw)


def _summary_should_refresh(existing, incoming):
    """Allow a longer provider description to replace a truncated preview."""
    current = str(existing or '').strip()
    replacement = str(incoming or '').strip()
    if not replacement:
        return False
    if not current or _summary_needs_cleanup(current):
        return True
    return len(replacement) > len(current) and current.endswith('...')


def _metadata_keyword_list(value):
    """Read comma/newline separated search keywords without duplicating them."""
    raw = value if isinstance(value, (list, tuple)) else re.split(r'[,;|\n]+', str(value or ''))
    result = []
    seen = set()
    for item in raw:
        term = re.sub(r'\s+', ' ', str(item or '')).strip()
        key = term.casefold()
        if term and key not in seen:
            result.append(term)
            seen.add(key)
    return result


def _metadata_search_query(value, config):
    """Apply configured keep/remove terms before querying metadata providers.

    Keep terms are protected first, so a broad remove term cannot remove a
    deliberately preserved title part.  This mirrors Comic Book Butler's
    remove/preserve behavior while keeping the plugin setting a simple text
    field.
    """
    text = re.sub(r'\s+', ' ', str(value or '')).strip()
    if not text:
        return ''
    keep = sorted(_metadata_keyword_list(config.get('metadata_search_keep_keywords')), key=len, reverse=True)
    keep_keys = {term.casefold() for term in keep}
    remove = sorted(
        (term for term in _metadata_keyword_list(config.get('metadata_search_remove_keywords'))
         if term.casefold() not in keep_keys),
        key=len, reverse=True)
    protected = {}
    for index, term in enumerate(keep):
        marker = f' rabbitpluginskeep{index} '
        protected[marker] = term
        text = re.sub(re.escape(term), marker, text, flags=re.IGNORECASE)

    # File names and release labels often append a source marker such as
    # ``[SUN SUN SUN]`` or ``[1080x]``.  Those markers are not part of the
    # work title and should not be sent to providers.  Remove only outer
    # bracket groups so a legitimate bracketed phrase in the middle of a
    # title remains searchable.  A configured keep term remains protected.
    bracket_group = r'(?:\[[^\[\]]+\]|【[^【】]+】|\([^()]+\)|（[^（）]+）|\{[^{}]+\})'
    for _ in range(4):
        before = text
        leading = re.match(rf'^\s*({bracket_group})\s*', text)
        if leading and 'rabbitpluginskeep' not in leading.group(1).casefold():
            text = text[leading.end():]
        trailing = re.search(rf'\s*({bracket_group})\s*$', text)
        if trailing and 'rabbitpluginskeep' not in trailing.group(1).casefold():
            text = text[:trailing.start()]
        if text == before:
            break

    for term in remove:
        escaped = re.escape(term.strip('[](){}【】（）'))
        for left, right in (('[', ']'), ('【', '】'), ('(', ')'), ('（', '）'), ('{', '}')):
            text = re.sub(
                rf'\s*{re.escape(left)}\s*{escaped}\s*{re.escape(right)}',
                ' ', text, flags=re.IGNORECASE)
        text = re.sub(re.escape(term), ' ', text, flags=re.IGNORECASE)
        text = re.sub(r'\[\s*\]|【\s*】|\(\s*\)|（\s*）|\{\s*\}', ' ', text)
    for marker, term in protected.items():
        text = text.replace(marker, term)
    return re.sub(r'\s+', ' ', text).strip()


def _read_series_dates(gateway, key):
    raw = gateway.get_setting(key, '{}')
    if isinstance(raw, dict):
        raw = raw.get('value', '{}')
    try:
        value = json.loads(raw or '{}')
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _chapter_file_coverage(files, total):
    """Compare positive episode numbers; episode zero is an optional prologue."""
    try:
        total = int(total)
    except (ValueError, TypeError):
        return {}
    if not 0 < total <= 1000000:
        return {}
    intervals = []
    unknown = False
    for row in files:
        if str(row.get('file_format') or '').lower() not in {'txt', 'text', 'epub'}:
            unknown = True
            continue
        name = Path(str(row.get('file_path') or '')).stem
        match = re.search(r'(?:^|\s)(\d{1,6})\s*[-~～]\s*(\d{1,6})(?:\s*(?:화|회))?(?:\s*\([^)]*\)|\s*\[[^]]*\])*\s*$', name)
        if not match:
            unknown = True
            continue
        start, end = map(int, match.groups())
        if end < start:
            unknown = True
            continue
        if end >= 1 and start <= total:
            intervals.append((max(1, start), min(total, end)))
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    present = sum(end - start + 1 for start, end in merged)
    return {'total': total, 'present': present, 'missing': max(0, total - present),
            'known': bool(intervals) and not unknown}


def _series_dates_key(library_id, series_name):
    identity = json.dumps([str(library_id), str(series_name)], ensure_ascii=False)
    return 'rabbit_plugins:publication_dates:' + hashlib.sha256(identity.encode()).hexdigest()


def _metadata_hold_key(library_id, series_name):
    identity = json.dumps([str(library_id), str(series_name)], ensure_ascii=False)
    return 'rabbit_plugins:metadata_hold:' + hashlib.sha256(identity.encode()).hexdigest()


def _read_metadata_hold(gateway, library_id, series_name):
    stored = gateway.get_setting(_metadata_hold_key(library_id, series_name))
    try:
        value = json.loads((stored or {}).get('value') or '{}')
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) and value.get('reason') == 'author_conflict' else {}


def _metadata_date(value):
    text = str(value or '').strip()[:10].replace('.', '-').replace('/', '-')
    if re.fullmatch(r'\d{8}', text):
        text = f'{text[:4]}-{text[4:6]}-{text[6:]}'
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return ''


def _metadata_clean(item):
    """Normalize one crawler/Series.db item to the plugin's DB field names."""
    item = dict(item or {})
    title = _metadata_title(item.get('title') or item.get('name') or '')
    genres = item.get('genre', item.get('genres', ''))
    tags = item.get('tags', '')
    author = _join_terms(item.get('author') or item.get('authors') or item.get('writer'))
    publisher = str(item.get('publisher') or '').strip()
    genre_text = _join_terms(genres)
    genre_keys = {term.strip().casefold() for term in re.split(r'[,;|\n]', genre_text) if term.strip()}
    metadata_keys = {
        term.strip().casefold()
        for value in (author, publisher, title)
        for term in re.split(r'[,;|\n]', value)
        if term.strip()
    }
    clean_tags = [term.strip() for term in re.split(r'[,;|\n]', _join_terms(tags))
                  if term.strip()
                  and term.strip().casefold() not in genre_keys
                  and term.strip().casefold() not in metadata_keys]
    result = {
        'title': title,
        'localized_series': str(item.get('localized_series') or item.get('original_title') or '').strip(),
        'author': author,
        'cover_artist': _join_terms(item.get('cover_artist') or item.get('artists') or item.get('penciller')),
        'publisher': publisher,
        'summary': _metadata_summary(item.get('summary') or item.get('description') or '', title),
        'genre': genre_text,
        'tags': ', '.join(dict.fromkeys(clean_tags)),
        'release_date': _metadata_date(item.get('release_date') or item.get('releaseDate')),
        'total_chapters': int(item['total_chapters']) if str(item.get('total_chapters', '')).isdigit() and 0 < int(item['total_chapters']) <= 1000000 else 0,
        'publication_start_date': _metadata_date(item.get('publication_start_date')),
        'publication_end_date': _metadata_date(item.get('publication_end_date')),
        'publication_status_label': '연재중단' if item.get('publication_status_label') == '연재중단' else '',
        'publication_status': str(item.get('publication_status', '')) if str(item.get('publication_status', '')) in {'0', '1', '2'} else '',
        'books_lv': str(item.get('books_lv') or '').strip(),
        'isbn': str(item.get('isbn') or item.get('isbn13') or '').strip(),
        'link': _join_links(item.get('link') or item.get('url') or item.get('web_url') or ''),
        'cover': str(item.get('cover') or item.get('cover_url') or item.get('coverUrl') or '').strip(),
    }
    return {key: value for key, value in result.items() if value}


def _metadata_remove_terms(value, excluded):
    excluded_keys = {
        str(term or '').strip().casefold()
        for term in re.split(r'[,;|\n]', str(excluded or ''))
        if str(term or '').strip()
    }
    if not excluded_keys:
        return _join_terms(value)
    terms = [
        term for term in re.split(r'[,;|\n]', str(value or ''))
        if term.strip().casefold() not in excluded_keys
    ]
    return _join_terms(terms)


def _metadata_author_cleanup(existing, incoming, artist):
    """Keep the writer field free of a separately stored illustrator."""
    existing = str(existing or '').strip()
    incoming = str(incoming or '').strip()
    artist = str(artist or '').strip()
    if not artist:
        return incoming
    cleaned_existing = _metadata_remove_terms(existing, artist) if existing else ''
    cleaned_incoming = _metadata_remove_terms(incoming, artist) if incoming else ''
    if cleaned_existing and cleaned_existing != existing:
        return cleaned_existing
    return cleaned_incoming


def _metadata_artist_refresh(existing, incoming):
    """Replace a legacy romanized artist with a provider's Korean value."""
    existing = str(existing or '').strip()
    incoming = str(incoming or '').strip()
    if not existing or not incoming or existing == incoming:
        return ''
    has_hangul = lambda value: bool(re.search(r'[\uac00-\ud7a3]', value))
    return incoming if has_hangul(incoming) and not has_hangul(existing) else ''


def _parse_rating_level(value):
    """Convert the core/ComicInfo books_lv variants to a conservative level."""
    if value is None:
        return None
    text = str(value).strip().casefold()
    if not text or text in ('-', 'none', 'null'):
        return None
    try:
        numeric = float(text)
    except (TypeError, ValueError):
        numeric = None
    if numeric is not None:
        if numeric >= 18:
            return 18
        if numeric >= 15:
            return 15
        if numeric >= 0:
            return 0
    compact = re.sub(r'[\s_-]+', '', text)
    if compact == 'm' or compact in {
        'r18', 'r18+', 'x18+', 'xrated', 'adultonly18+', 'adultsonly18+',
        'adultonly18', 'adultsonly18', '18+', '18세', '18세이상',
        'adult', '성인', '19금', '청소년관람불가'
    } or re.search(r'(?:r18|adult|성인|19금|청소년관람불가|18세|18\+|x-rated)', text):
        return 18
    if compact in {'ma15', 'ma15+', 'm15', 'm15+', '15세', '15+', '청소년'} or re.search(r'(?:ma15|m15|15세|15\+|청소년)', text):
        return 15
    if compact in {'everyone', 'general', 'all', 'allages', '전체', '전체이용가', '일반', '전연령', '0세'}:
        return 0
    return None


def _fallback_effective_level(row):
    """Use books_lv when an older core has no ContentRatingService contract."""
    level = _parse_rating_level(row.get('books_lv'))
    return 18 if level is None else level


def _load_core_dependencies():
    """Load core contracts while keeping missing optional capabilities safe."""
    missing = []
    dependencies = {}
    try:
        from api import auth as auth_api
    except ImportError as exc:
        return None, (
            f'Rabbit Plugins {PLUGIN_VERSION} requires BookOasis core main '
            f'{REQUIRED_CORE_COMMIT} or later; api.auth could not be loaded ({exc}).'
        )

    required = 'check_adult_permission'
    dependency = getattr(auth_api, required, None)
    if callable(dependency):
        dependencies[required] = dependency
    else:
        missing.append(f'api.auth.{required}')

    # 구버전 코어에는 아직 없는 선택 기능이다. 다운로드는 항상 거부하고,
    # 등급 권한 함수는 없을 때 플러그인 자체의 books_lv 판정을 사용한다.
    for name in ('check_download_permission', 'check_book_rating_permission'):
        dependency = getattr(auth_api, name, None)
        if callable(dependency):
            dependencies[name] = dependency
    dependencies.setdefault('check_download_permission', lambda: False)
    dependencies['check_book_rating_permission'] = dependencies.get('check_book_rating_permission')

    try:
        from services.content_rating_service import ContentRatingService
    except ImportError:
        dependencies['ContentRatingService'] = None
    else:
        dependencies['ContentRatingService'] = ContentRatingService

    if missing:
        missing_names = ', '.join(missing)
        return None, (
            f'Rabbit Plugins {PLUGIN_VERSION} requires BookOasis core main '
            f'{REQUIRED_CORE_COMMIT} or later. Missing core API: {missing_names}. '
            'Please update BookOasis or switch to the core detail renderer.'
        )
    return dependencies, None


def tokens(value):
    return {part.strip().casefold() for part in re.split(r'[,;|\n]', str(value or ''))
            if part.strip() and part.strip() != '-'}


def _term_filter(groups):
    clauses, values = [], []
    for field, terms in groups.items():
        for term in sorted(terms)[:12]:
            clause = f"LOWER(COALESCE(b.{field}, '')) LIKE ? ESCAPE '!'"
            value = '%' + term.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
            clauses.append(clause)
            values.append(value)
    if not clauses:
        return '', []
    return '(' + ' OR '.join(clauses) + ')', values


def _title_key(value):
    text = unicodedata.normalize('NFKC', str(value or '')).casefold()
    return re.sub(r'[\s‐‑‒–—―−-]+', '', text)


def _series_title_key(value):
    text = re.sub(r'\s*\[[^\[\]]+\]\s*$', '', str(value or '').strip())
    return _title_key(text)


def _metadata_match_key(value):
    """Build a strict work-title key for automatic candidate matching.

    Search pages include provider labels, volume markers, and edition labels in
    their link text.  Those parts are safe to remove for comparison.  Extra
    title words are kept, so a related work such as ``위장복을 벗으면 야수``
    cannot match the shorter work ``벗으면 야수``.
    """
    text = unicodedata.normalize('NFKC', html_lib.unescape(str(value or '')).strip())
    if not text:
        return ''
    # Provider/imprint labels are commonly prepended in square or round
    # brackets: [e북] [베리쉬] 제목, [코이] 제목.
    for _ in range(4):
        stripped = re.sub(
            r'^\s*(?:\[[^\]]+\]|【[^】]+】|\([^)]*\)|（[^）]*）)\s*', '', text)
        if stripped == text:
            break
        text = stripped
    # Remove a volume/chapter marker and its trailing edition labels.
    text = re.sub(
        r'\s*(?:제?\s*\d+(?:[.,]\d+)?\s*(?:권|화|편|vol(?:ume)?\.?)'
        r'|\(\s*총\s*\d+\s*(?:권|화|편)(?:\s*/[^)]*)?\)'
        r'|【\s*총\s*\d+\s*(?:권|화|편)(?:\s*/[^】]*)?】)'
        r'(?:\s*(?:\[[^\]]+\]|【[^】]+】|\([^)]*\)|（[^）]*）))*\s*$',
        '', text, flags=re.IGNORECASE)
    # Search results sometimes put only an edition label after the title.
    text = re.sub(
        r'(?:\s*(?:\[[^\]]+\]|【[^】]+】|\([^)]*\)|（[^）]*）))+\s*$',
        '', text)
    return _title_key(text)


def _metadata_title_matches(query, candidate_title, allow_partial=False):
    query_key = _metadata_match_key(query)
    candidate_key = _metadata_match_key(candidate_title)
    if not query_key or not candidate_key:
        return False
    if query_key == candidate_key:
        return True
    # Manual searches may intentionally use a shortened work title.  Keep
    # automatic collection exact, while allowing an entered title prefix (or
    # a provider's longer canonical title) in the manual result list.
    return bool(allow_partial and query_key in candidate_key)


def _metadata_candidate_variant_label(candidate, content_kind, book_type=''):
    """Normalize a result label against the target library media kind.

    Provider category text such as ``BL 소설 e북`` contains the generic word
    ``e북``. It must never be interpreted as ``만화 e북`` when the target is a
    novel library. Apply this after every provider adapter so the correction is
    source-independent.
    """
    kind = _metadata_content_kind(content_kind)
    current = str(candidate.get('variant_label') or candidate.get('media_type_label') or '').strip()
    metadata = candidate.get('metadata') if isinstance(candidate.get('metadata'), dict) else {}
    evidence = ' '.join(str(value or '') for value in (
        current, candidate.get('title'), candidate.get('genre'),
        metadata.get('genre'), metadata.get('tags'),
    ))
    if kind != 'novel':
        return current

    is_bl = bool(re.search(r'(?<![a-z])bl(?![a-z])', evidence, re.I))
    if re.search(r'라이트\s*노벨|라노벨', evidence, re.I):
        return 'BL 라이트노벨' if is_bl else '라이트노벨'
    is_ebook = bool(re.search(r'소설\s*e북|전자책|단행본|총\s*\d+\s*권', evidence, re.I))
    is_serial = bool(re.search(r'웹소설|연재|총\s*\d+\s*화', evidence, re.I))
    if current in {'웹소설', 'BL 웹소설'}:
        return 'BL 웹소설' if is_bl else '웹소설'
    if is_ebook:
        return 'BL 소설 e북' if is_bl else '소설 e북'
    if is_serial:
        return 'BL 웹소설' if is_bl else '웹소설'
    return 'BL 소설' if is_bl else '소설'


def _metadata_source_variant_score(candidate, content_kind, book_type):
    """Prefer the configured media type when a source has exact duplicates."""
    source = str(candidate.get('source') or '').casefold()
    title = ' '.join(
        str(candidate.get(key) or '')
        for key in ('title', 'variant_label', 'genre')
    ).casefold()
    url = str(candidate.get('url') or '').casefold()
    score = 0
    if source == 'ridi':
        if book_type == 'webtoon' and '웹툰' in title:
            score += 4
        elif book_type == 'series' and ('연재' in title or '웹툰' in title):
            score += 4
        elif book_type == 'single' and ('e북' in title or '단행본' in title):
            score += 4
        elif book_type == 'novel' and ('소설' in title or '라이트노벨' in title):
            score += 4
    elif source == 'naver':
        path = urlparse(url).path
        expected = {
            'novel': '/novel/',
            'book': '/ebook/',
            'webtoon': '/comic/',
            'series': '/comic/',
            'single': '/ebook/',
        }.get(book_type or str(content_kind or '').casefold(), '')
        if expected and expected in path:
            score += 4
    return score


def _metadata_source_variant_allowed(candidate, content_kind, book_type):
    """Reject a clearly different Naver product type during auto collection."""
    source = str(candidate.get('source') or '').casefold()
    if source != 'naver':
        return True
    title = str(candidate.get('title') or '').casefold()
    if re.search(r'(?:세트|합본)', title):
        return False
    has_chapter_marker = bool(re.search(r'(?:총\s*\d+\s*화|웹툰|연재)', title))
    has_volume_marker = bool(re.search(r'(?:총\s*\d+\s*권|단행본)', title))
    if book_type == 'single':
        return has_volume_marker and not has_chapter_marker
    if book_type in {'series', 'webtoon'}:
        return has_chapter_marker and not has_volume_marker
    return True


def _volume_number(value):
    """Extract a volume/episode number from a title without treating years as volumes."""
    text = str(value or '').strip()
    if not text:
        return None
    for pattern in (
        r'(?<!\d)(\d+(?:[.,]\d+)?)\s*(?:권|화|편|vol(?:ume)?\.?)(?!\w)',
        r'(?<!\d)(\d+(?:[.,]\d+)?)\s*(?=[\]\)】])',
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        try:
            number = float(match.group(1).replace(',', '.'))
        except (TypeError, ValueError):
            continue
        if number > 0:
            return number
    return None


def _volume_key(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ''
    if number <= 0:
        return ''
    return str(int(number)) if number.is_integer() else f'{number:.6f}'.rstrip('0').rstrip('.')


def _row_volume_key(row):
    """Return a DB row's volume using metadata first and filename as fallback."""
    key = _volume_key(row.get('volume_index'))
    if key:
        return key
    for value in (row.get('title_alias'), row.get('title'), row.get('file_path')):
        key = _volume_key(_volume_number(value))
        if key:
            return key
    return ''


def _row_explicit_volume_key(row):
    """Return a volume only when the local name explicitly says ``N권``."""
    for value in (row.get('title_alias'), row.get('title'), row.get('file_path')):
        name = os.path.basename(str(value or '').replace('\\', '/'))
        match = re.search(r'(?<!\d)(\d+(?:\.\d+)?)\s*권(?:\b|\s|\)|\]|$)', name)
        if match:
            return _volume_key(match.group(1))
    return ''


def _row_mapped_cover(row, cover_by_volume, cover_by_title):
    """Resolve only an exact volume/title cover, never a series fallback."""
    volume = _row_explicit_volume_key(row)
    if not volume:
        return ''
    cover = str((cover_by_volume or {}).get(volume) or '').strip()
    if cover:
        return cover
    title = row.get('title_alias') or row.get('title') or ''
    return str((cover_by_title or {}).get(_title_key(title)) or '').strip()


def _use_ridi_volume_covers(source, rows, cover_by_volume, cover_by_title):
    """Use strict volume matching only when a local series has many volumes.

    A completed web novel is commonly stored as one TXT file such as
    ``1-470``. Ridi can still expose a one-item series map, but treating that
    map as a multi-volume contract makes the TXT range fail exact matching and
    suppresses the representative cover entirely.
    """
    return (
        str(source or '').strip().casefold() == 'ridi'
        and len(rows or ()) > 1
        and any(_row_explicit_volume_key(row) for row in (rows or ()))
        and bool(cover_by_volume or any(
            _row_mapped_cover(row, {}, cover_by_title) for row in (rows or ())))
    )


def _use_ridi_exact_single_cover(source, rows, cover):
    """Allow the selected Ridi e-book cover for one explicit local volume."""
    return (
        str(source or '').strip().casefold() == 'ridi'
        and len(rows or ()) == 1
        and bool(_row_explicit_volume_key((rows or [])[0]))
        and bool(str(cover or '').strip())
    )


def _korean_titles(titles, secondary_titles):
    result = []
    for raw in (titles, secondary_titles):
        try:
            entries = json.loads(raw or '[]') if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict):
                title = entry.get('title')
                if not str(entry.get('language') or '').casefold().startswith('ko'):
                    continue
            else:
                title = entry
            title = str(title or '').strip()
            if title and title not in result:
                result.append(title)
    return result


def _recommendation_slug(item):
    match = re.search(r'/series/([^/?#]+)', str(item.get('series_url') or ''), re.I)
    return match.group(1).casefold() if match else ''


def _relation_ids(value):
    try:
        values = json.loads(value or '[]')
    except (TypeError, ValueError):
        return []
    result = []
    if isinstance(values, list):
        for value in values:
            try:
                series_id = int(value)
            except (TypeError, ValueError):
                continue
            if series_id > 0 and series_id not in result:
                result.append(series_id)
    return result


def _comicinfo_metadata(book):
    return dict(_read_comicinfo(
        str(book.get('file_path') or '').strip(),
        str(book.get('file_format') or '').casefold(),
        str(book.get('cover_artist') or '').strip(),
        str(book.get('file_mtime') or ''),
        str(book.get('file_size') or ''),
    ))


def _optional_column_sql(gateway, table, alias, column):
    supported = {
        'books': {
            'localized_series', 'cover_artist',
            'document_volume_index', 'document_volume_count', 'cover_updated_at', 'publication_status', 'books_lv',
        },
        'libraries': {'content_kind'},
    }
    if column not in supported.get(table, set()):
        raise ValueError('Unsupported optional database column')
    engine = str(getattr(gateway, '_engine', 'sqlite')).casefold()
    if engine in ('mariadb', 'mysql'):
        columns = gateway.fetch_all('SHOW COLUMNS FROM ' + table)
        name = 'Field'
    else:
        columns = gateway.fetch_all('PRAGMA table_info(' + table + ')')
        name = 'name'
    if any(str(row.get(name) or '').casefold() == column for row in columns):
        return alias + '.' + column
    return "'unspecified'" if (table, column) == ('libraries', 'content_kind') else 'NULL'


def _series_type_matches_library(series_type, content_kind):
    allowed_types = SERIES_TYPES_BY_LIBRARY.get(str(content_kind or 'unspecified').strip().casefold())
    return allowed_types is None or str(series_type or '').strip().casefold() in allowed_types


def _localized_series_value(book):
    return str(book.get('localized_series') or _comicinfo_metadata(book).get('localized_series') or '').strip()


def _comicinfo_summary(element):
    if element is None:
        return ''

    parts = [element.text or '']
    for child in element:
        name = child.tag.rsplit('}', 1)[-1].casefold()
        if name in ('img', 'br'):
            parts.append(ET.tostring(child, encoding='unicode', method='html'))
        else:
            parts.append(_comicinfo_summary(child))
        parts.append(child.tail or '')
    return ''.join(parts).strip()


def _metadata_number(value, integer_only=False):
    match = re.search(r'(?<!\d)(\d+(?:\.\d+)?)', str(value or ''))
    if not match:
        return None
    try:
        number = float(match.group(1))
    except (TypeError, ValueError, OverflowError):
        return None
    if number <= 0 or (integer_only and not number.is_integer()):
        return None
    return int(number) if number.is_integer() else number


def _series_volume_and_count(entries):
    """Read explicit volume/count labels from EPUB and PDF document metadata."""
    volume = count = None
    searchable = []
    volume_keys = {
        'volume', 'vol', 'volumenumber', 'seriesindex', 'volumeindex',
        'booknumber', 'bookindex', 'issuenumber', 'issue', 'groupposition',
    }
    count_keys = {
        'count', 'seriescount', 'volumecount', 'totalcount', 'totalvolumes',
        'numberofvolumes', 'totalbooks', 'numberofbooks', 'totalissues',
        'numberofissues', 'seriestotal',
    }

    for key, raw_value in entries:
        key_text = str(key or '').strip()
        value = str(raw_value or '').strip()
        if not value:
            continue
        normalized = re.sub(r'[^a-z0-9]+', '', key_text.casefold())
        searchable.append(f'{key_text}: {value}')
        if volume is None and (
            normalized in volume_keys
            or normalized.endswith(('seriesindex', 'volumeindex', 'volumenumber', 'booknumber', 'groupposition'))
        ):
            volume = _metadata_number(value)
        if count is None and (
            normalized in count_keys
            or normalized.endswith(('seriescount', 'volumecount', 'totalcount', 'totalvolumes', 'totalissues'))
        ):
            count = _metadata_number(value, integer_only=True)

    text = '\n'.join(searchable)
    if volume is None:
        match = re.search(
            r'(?i)(?:calibre[\s:_-]*)?(?:series[\s:_-]*)?'
            r'(?:index|volumes?|vol\.?|issues?|books?)'
            r'(?:[\s:_-]*(?:number|no\.?))?\s*[:=#-]?\s*(\d+(?:\.\d+)?)',
            text,
        )
        if match:
            volume = _metadata_number(match.group(1))
    if volume is None:
        match = re.search(r'(?<!\d)(\d+(?:\.\d+)?)\s*(?:권|巻)', text)
        if match:
            volume = _metadata_number(match.group(1))

    position = re.search(r'(?i)\b(\d+(?:\.\d+)?)\s*(?:of|/)\s*(\d+)\b', text)
    if position:
        volume = volume or _metadata_number(position.group(1))
        count = count or _metadata_number(position.group(2), integer_only=True)
    if count is None:
        match = re.search(
            r'(?i)(?:\b(?:series[\s:_-]*)?(?:count|total[\s:_-]*count)\b'
            r'|\b(?:total[\s:_-]*|number[\s:_-]*of[\s:_-]*)'
            r'(?:volumes?|books?|issues?)\b)\s*[:=#-]?\s*(\d+)',
            text,
        )
        if match:
            count = _metadata_number(match.group(1), integer_only=True)
    return volume, count


def _series_number(entries):
    """Read a chapter/episode number without treating it as a volume."""
    number_keys = {
        'number', 'chapternumber', 'chapter', 'episodenumber', 'episode',
    }
    searchable = []
    for key, raw_value in entries:
        key_text = str(key or '').strip()
        value = str(raw_value or '').strip()
        if not value:
            continue
        normalized = re.sub(r'[^a-z0-9]+', '', key_text.casefold())
        searchable.append(f'{key_text}: {value}')
        if normalized in number_keys:
            number = _metadata_number(value)
            if number is not None:
                return number
    text = '\n'.join(searchable)
    match = re.search(
        r'(?i)\b(?:chapter|chap\.?|episode|ep\.?|number|no\.?)\s*[:=#-]?\s*(\d+(?:\.\d+)?)',
        text,
    )
    return _metadata_number(match.group(1)) if match else None


def _metadata_publication_date(entries):
    """Use explicit publication dates; a PDF file-creation timestamp is not publication time."""
    date_keys = {'date', 'dcdate', 'publicationdate', 'publisheddate', 'datepublished', 'releasedate'}
    for key, raw_value in entries:
        value = str(raw_value or '').strip()
        if not value:
            continue
        normalized = re.sub(r'[^a-z0-9]+', '', str(key or '').casefold())
        candidates = [value] if normalized in date_keys else []
        label = re.search(
            r'(?i)(?:publication\s*date|published(?:\s+date)?|release\s*date|released\s*date|dc:date)'
            r'\s*[:=#]\s*(D:\s*\d{8,14}|\d{4}(?:[-/.]?\d{1,2})?(?:[-/.]?\d{1,2})?)',
            value,
        )
        if label:
            candidates.append(label.group(1))
        for candidate in candidates:
            match = re.search(r'(?<!\d)(\d{4})(?:[-/.]?(\d{2}))?(?:[-/.]?(\d{2}))?', candidate)
            if not match:
                continue
            try:
                return date(
                    int(match.group(1)), int(match.group(2) or 1), int(match.group(3) or 1)
                ).isoformat()
            except (ValueError, OverflowError):
                continue
    return ''


def _read_pdf_series_metadata(path, result):
    """Read PDF document properties without rendering pages."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(path)
    try:
        metadata = document.get_metadata_dict() or {}
    finally:
        document.close()
    entries = [(str(key), str(value)) for key, value in metadata.items() if value]
    entries.append(('filename', Path(path).stem))
    result['volume'], result['count'] = _series_volume_and_count(entries)
    result['number'] = _series_number(entries)
    result['date'] = _metadata_publication_date(entries)
    return result


@lru_cache(maxsize=2048)
def _read_comicinfo(path, file_format, artist, _file_mtime, _file_size):
    result = {
        'artist': artist,
        'format': '', 'count': None, 'volume': None, 'number': None, 'date': '',
        'summary': '', 'localized_series': '', 'publication_status': '',
    }
    if not path:
        return result

    if file_format == 'pdf':
        try:
            return _read_pdf_series_metadata(path, result)
        except Exception:
            # Invalid, encrypted, or unsupported PDF metadata must not break detail rendering.
            return result

    try:
        with zipfile.ZipFile(path) as archive:
            if file_format == 'epub':
                container_name = next((name for name in archive.namelist()
                                       if name.casefold() == 'meta-inf/container.xml'), None)
                opf_name = ''
                if container_name:
                    container = ET.fromstring(archive.read(container_name))
                    rootfile = next((element for element in container.iter()
                                     if element.tag.rsplit('}', 1)[-1].casefold() == 'rootfile'), None)
                    opf_name = str(rootfile.get('full-path') or '') if rootfile is not None else ''
                if not opf_name:
                    opf_name = next((name for name in archive.namelist()
                                     if name.casefold().endswith('.opf')), '')
                if not opf_name or archive.getinfo(opf_name).file_size > 2_000_000:
                    return result
                root = ET.fromstring(archive.read(opf_name))
                dates, meta, metadata_entries = [], {}, []
                for element in root.iter():
                    key = element.tag.rsplit('}', 1)[-1].casefold()
                    if key == 'date' and element.text and element.text.strip():
                        dates.append(element.text.strip())
                    if key == 'title' and element.text:
                        metadata_entries.append(('title', element.text.strip()))
                    if key == 'meta':
                        name = str(element.get('name') or element.get('property') or '').casefold()
                        value = (element.get('content') or element.text or '').strip()
                        if name and value:
                            metadata_entries.append((name, value))
                            meta[name] = value
                metadata_entries.append(('filename', Path(path).stem))
                result['volume'], result['count'] = _series_volume_and_count(metadata_entries)
                result['number'] = _series_number(metadata_entries)
                result['localized_series'] = meta.get('comic-book-butler:localizedseries', '')
                for raw_date in dates:
                    result['date'] = _metadata_publication_date([('date', raw_date)])
                    if result['date']:
                        break
                return result

            if file_format not in ('cbz', 'zip'):
                return result
            xml_name = next((name for name in archive.namelist()
                             if name.replace('\\', '/').rsplit('/', 1)[-1].casefold() == 'comicinfo.xml'), None)
            if not xml_name or archive.getinfo(xml_name).file_size > 2_000_000:
                entries = [('filename', Path(path).stem)]
                result['volume'], result['count'] = _series_volume_and_count(entries)
                result['number'] = _series_number(entries)
                if re.search(r'(?i)(?:\(|\[|\s)(?:완결|complete|completed|finished|final)(?:\)|\]|\s|$)', Path(path).stem):
                    result['publication_status'] = '2'
                    result['count'] = result['count'] or result['volume']
                return result
            root = ET.fromstring(archive.read(xml_name))
        summary = next((element for element in root.iter()
                        if element.tag.rsplit('}', 1)[-1].casefold() == 'summary'), None)
        values = {}
        for element in root.iter():
            key = element.tag.rsplit('}', 1)[-1].casefold()
            if element.text and key not in values:
                values[key] = element.text.strip()

        def positive_number(value):
            try:
                number = float(value)
                return int(number) if number > 0 and number.is_integer() else None
            except (TypeError, ValueError, OverflowError):
                return None

        result.update({
            'artist': values.get('artist') or values.get('penciller') or values.get('coverartist') or result['artist'],
            'format': values.get('format', ''),
            'count': positive_number(values.get('count')),
            'volume': positive_number(values.get('volume')),
            'number': positive_number(values.get('number')),
            'summary': _metadata_summary(_comicinfo_summary(summary), values.get('title', '')),
            'localized_series': values.get('localizedseries', ''),
        })
        if result['volume'] is None or result['count'] is None:
            file_volume, file_count = _series_volume_and_count([('filename', Path(path).stem)])
            result['volume'] = result['volume'] or file_volume
            result['count'] = result['count'] or file_count
        if re.search(r'(?i)(?:\(|\[|\s)(?:완결|complete|completed|finished|final)(?:\)|\]|\s|$)', Path(path).stem):
            result['publication_status'] = '2'
            result['count'] = result['count'] or result['volume']
        if values.get('year'):
            try:
                result['date'] = date(
                    int(values['year']), int(values.get('month') or 1), int(values.get('day') or 1)
                ).isoformat()
            except (ValueError, OverflowError):
                pass
    except (OSError, RuntimeError, ValueError, KeyError, zipfile.BadZipFile, ET.ParseError):
        pass
    return result


class _MetadataPageParser(HTMLParser):
    """Small dependency-free HTML parser for the four manual-search sources."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.meta = {}
        self._link = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        tag = tag.casefold()
        if tag == 'meta':
            key = attrs.get('property') or attrs.get('name')
            content = attrs.get('content')
            if key and content:
                self.meta[str(key).casefold()] = html_lib.unescape(str(content)).strip()
        elif tag == 'a' and attrs.get('href'):
            self._link = {'href': attrs['href'], 'text': []}

    def handle_data(self, data):
        if self._link is not None:
            self._link['text'].append(data)

    def handle_endtag(self, tag):
        if tag.casefold() == 'a' and self._link is not None:
            self.links.append({
                'href': str(self._link.get('href') or '').strip(),
                'text': re.sub(r'\s+', ' ', ' '.join(self._link.get('text') or [])).strip(),
            })
            self._link = None


def _html_text(value):
    value = html_lib.unescape(re.sub(r'<[^>]+>', ' ', str(value or '')))
    return re.sub(r'\s+', ' ', value).strip()


def _inline_json_object(source, variable):
    """Read a JSON object assigned to a small inline provider variable."""
    match = re.search(
        rf'(?:var|let|const)\s+{re.escape(variable)}\s*=\s*',
        html_lib.unescape(str(source or '')), re.I)
    if not match:
        return {}
    payload = html_lib.unescape(str(source or ''))[match.end():].lstrip()
    try:
        value, _ = json.JSONDecoder().raw_decode(payload)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _ridi_genres(source):
    """Extract Ridi's parent and child categories as genres.

    Ridi exposes the useful category (for example ``만화 e북``) in the
    category-link list, while JSON-LD commonly contains only ``성인``.  Keep
    the same parent-first ordering used by Comic Book Butler and fall back to
    the inline ``bookDetail`` payload when the links are not rendered.
    """
    html = html_lib.unescape(str(source or ''))
    genres = []
    containers = re.findall(
        r'<ul\b[^>]*class=["\'][^"\']*\brigrid-kzglsd\b[^"\']*["\'][^>]*>(.*?)</ul>',
        html, re.I | re.S)
    for container in containers:
        links = re.findall(
            r'<a\b[^>]*class=["\'][^"\']*\brigrid-8ycyyy\b[^"\']*["\'][^>]*>(.*?)</a>',
            container, re.I | re.S)
        for value in links:
            text = _html_text(value)
            if text and text != '미정' and text not in genres:
                genres.append(text)
        if genres:
            break

    detail = _inline_json_object(html, 'bookDetail')
    if not detail:
        detail = _inline_json_object(html, 'book_detail')
    for key in ('parent_category_name', 'genre_name', 'category_name', 'category_name2', 'genre2_name'):
        value = detail.get(key)
        if isinstance(value, (list, tuple)):
            values = value
        else:
            values = re.split(r'[,|]', str(value or ''))
        for item in values:
            text = _html_text(item)
            if text and text != '미정' and text not in genres:
                genres.append(text)
    return genres


def _json_ld_metadata(source):
    result = {}
    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        source or '', re.I | re.S)
    for raw in scripts:
        try:
            value = json.loads(html_lib.unescape(raw.strip()))
        except (TypeError, ValueError):
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get('@graph'), list):
                values.extend(item['@graph'])
            kind = str(item.get('@type') or '').casefold()
            if kind and kind not in ('book', 'product', 'creativework', 'comicstory'):
                continue
            author = item.get('author')
            publisher = item.get('publisher')
            image = item.get('image')
            if isinstance(author, list):
                author = ', '.join(str(x.get('name') if isinstance(x, dict) else x) for x in author)
            elif isinstance(author, dict):
                author = author.get('name')
            if isinstance(publisher, dict):
                publisher = publisher.get('name')
            if isinstance(image, list):
                image = image[0] if image else ''
            result.update({
                'title': item.get('name') or '',
                'author': author or '', 'publisher': publisher or '',
                'summary': item.get('description') or '', 'cover': image or '',
                'isbn': item.get('isbn') or '',
                'release_date': item.get('datePublished') or '',
                'genre': item.get('genre') or '', 'link': item.get('url') or '',
            })
            if result.get('title'):
                return _metadata_clean(result)
    return {}


def _next_data_books(source):
    """Read the small result objects exposed by Ridi's __NEXT_DATA__ payload."""
    scripts = re.findall(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', source or '', re.I | re.S)
    found = []
    for raw in scripts:
        try:
            value = json.loads(html_lib.unescape(raw.strip()))
        except (TypeError, ValueError):
            continue

        def walk(item):
            if isinstance(item, dict):
                for key, child in item.items():
                    if str(key).casefold() in ('books', 'initialbooks') and isinstance(child, list):
                        found.extend(value for value in child if isinstance(value, dict))
                    walk(child)
            elif isinstance(item, list):
                for child in item:
                    walk(child)
        walk(value)
    return found


def _next_data_detail_metadata(source, book_id=''):
    """Read the full Ridi introduction instead of its truncated meta preview."""
    scripts = re.findall(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', source or '', re.I | re.S)
    introductions = []
    descriptions = []
    keywords = []
    covers = []
    target_id = str(book_id or '').strip()
    for raw in scripts:
        try:
            value = json.loads(html_lib.unescape(raw.strip()))
        except (TypeError, ValueError):
            continue

        def walk(item):
            if isinstance(item, dict):
                for key, child in item.items():
                    normalized = str(key).casefold().replace('_', '')
                    if normalized == 'introductionhtml' and isinstance(child, str) and child.strip():
                        introductions.append(child.strip())
                    elif normalized == 'description' and isinstance(child, str) and child.strip():
                        descriptions.append(child.strip())
                    elif normalized in ('keywords', 'keyword'):
                        if isinstance(child, (list, tuple)):
                            keywords.extend(str(value).strip() for value in child if str(value).strip())
                        elif isinstance(child, str) and child.strip():
                            keywords.append(child.strip())
                    elif (
                        normalized == 'bookdetailpagecover' and isinstance(child, dict)
                        and (not target_id or str(item.get('id') or '').strip() == target_id)
                    ):
                        cover = child.get('xxlarge') or child.get('large') or child.get('small')
                        if cover:
                            covers.append(str(cover).strip())
                    walk(child)
            elif isinstance(item, list):
                for child in item:
                    walk(child)

        walk(value)
    summary = max(introductions, key=len) if introductions else max(descriptions, key=len, default='')
    return {
        'summary': summary,
        'tags': _join_terms(keywords),
        'cover': next((cover for cover in covers if cover), ''),
    }


def _ridi_volume_cover_maps(source):
    """Extract Ridi's exact per-volume covers from a series detail page.

    Current pages expose the episode list in Next.js data. Older pages have
    server-rendered series rows, so retain that parser as a fallback.
    """
    by_volume = {}
    by_title = {}
    next_data = re.search(
        r'<script\b[^>]*\bid=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
        str(source or ''), re.I | re.S)
    if next_data:
        try:
            page = json.loads(html_lib.unescape(next_data.group(1)))
            cells = (page['props']['pageProps']['sectionProps']['gridQuery']
                    ['riGrid']['grid']['cells'])
            for cell in cells:
                episode_list = cell.get('cell__BookDetailHomeEpisodeBookList') or {}
                for book in episode_list.get('books') or ():
                    title = str(book.get('title') or '').strip()
                    cover = str((book.get('cover') or {}).get('xxlarge') or '').strip()
                    volume = _volume_key(_volume_number(title))
                    cover_host = (urlparse(cover).hostname or '').casefold()
                    if (not volume or not cover or urlparse(cover).scheme != 'https'
                            or cover_host != 'img.ridicdn.net'):
                        continue
                    by_volume[volume] = cover
                    by_title[_title_key(title)] = cover
        except (KeyError, TypeError, ValueError, AttributeError):
            pass
    starts = list(re.finditer(
        r'<li\b(?=[^>]*\bclass=["\'][^"\']*\bjs_series_book_list\b[^"\']*["\'])[^>]*>',
        str(source or ''), re.I | re.S))
    for index, match in enumerate(starts):
        opening = match.group(0)
        block_end = starts[index + 1].start() if index + 1 < len(starts) else len(source or '')
        block = str(source or '')[match.start():block_end]
        volume_match = re.search(r'\bdata-volume=["\']([^"\']+)', opening, re.I)
        title_match = re.search(
            r'<div\b[^>]*class=["\'][^"\']*\bjs_book_title\b[^"\']*["\'][^>]*>(.*?)</div>',
            block, re.I | re.S)
        image_match = re.search(
            r'<img\b[^>]*(?:data-original-cover|data-fallback|data-src)=["\'](https?://[^"\']+)["\']',
            block, re.I | re.S)
        if not image_match:
            continue
        cover = html_lib.unescape(image_match.group(1)).strip()
        volume = _volume_key(volume_match.group(1)) if volume_match else ''
        if volume and cover:
            by_volume[volume] = cover
        title = _html_text(title_match.group(1)) if title_match else ''
        title_key = _title_key(title)
        if title_key and cover:
            by_title[title_key] = cover
    return by_volume, by_title


def _ridi_role_metadata(source):
    """Read Ridi's separate story-writer and illustrator credits.

    JSON-LD exposes both people as one ``author`` string, but the page's
    ``bookDetail`` object keeps the role-specific maps.  Prefer those maps so
    the core's ``author`` and ``cover_artist`` columns stay separate.
    """
    detail = _inline_json_object(source, 'bookDetail')
    authors = detail.get('authors') if isinstance(detail, dict) else {}
    if not isinstance(authors, dict):
        return {}

    def names(value):
        if isinstance(value, dict):
            value = list(value.values())
        elif not isinstance(value, (list, tuple)):
            value = [value]
        return _join_terms(value)

    result = {}
    writer = names(authors.get('story_writer') or authors.get('writer'))
    artist = names(authors.get('illustrator') or authors.get('artist') or authors.get('penciller'))
    generic = names(authors.get('author'))
    if generic and not writer:
        writer = generic
    # Ridi's single-author comic pages expose ``authors.author`` and render
    # the role as ``글, 그림``.  Treat that Korean value as both roles so a
    # later Naver result cannot replace it with an English artist name.
    if generic and not artist and re.search(r'글\s*,\s*그림', str(source or ''), re.I):
        artist = generic
    if writer:
        result['author'] = writer
    if artist:
        result['cover_artist'] = artist
    return result


def _ridi_publication_metadata(source):
    detail = _inline_json_object(source, 'bookDetail')
    result = {}
    age = str(detail.get('age_limit', ''))
    if detail.get('is_adult_only') is True or age in ('18', '19'):
        result['books_lv'] = 'm'
    elif age == '15':
        result['books_lv'] = 'ma15+'
    elif age == '0':
        result['books_lv'] = 'everyone'
    complete = str(detail.get('is_series_complete', ''))
    if complete in ('0', '1'):
        result['publication_status'] = '2' if complete == '1' else '0'
    raw_date = str(detail.get('pub_date') or '')
    published = _metadata_date(f'{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}') if re.fullmatch(r'\d{8}', raw_date) else _metadata_date(raw_date)
    if published:
        result['release_date'] = published
        if str(detail.get('volume')) == '1':
            result['publication_start_date'] = published
    return result


def _naver_role_metadata(source):
    """Read Naver Series' separate 글/그림 credit rows."""
    result = {}
    for label, field in (('글', 'author'), ('그림', 'cover_artist'), ('출판사', 'publisher')):
        match = re.search(
            rf'<li\b[^>]*>\s*<span\b[^>]*>\s*{label}\s*</span>(.*?)</li>',
            str(source or ''), re.I | re.S)
        if not match:
            continue
        names = [
            _html_text(value) for value in re.findall(
                r'<a\b[^>]*>(.*?)</a>', match.group(1), re.I | re.S)
        ]
        names = [name for name in names if name]
        if names:
            result[field] = _join_terms(names)
    # Restrict categories to this work's detail block, excluding navigation.
    detail = re.search(r'<ul\b[^>]*class=["\'][^"\']*end_info[^"\']*["\'][^>]*>(.*?)<div\b[^>]*class=["\']end_dsc', str(source or ''), re.I | re.S)
    if detail:
        genres = re.findall(r'<a\b[^>]*href=["\'][^"\']*genreCode=[^"\']*["\'][^>]*>(.*?)</a>', detail.group(1), re.I | re.S)
        if genres:
            result['genre'] = _join_terms([_html_text(value) for value in genres])
        text = _html_text(detail.group(1))
        for label, value in [('완결', '2'), ('휴재', '1'), ('연재중', '0')]:
            if label in text:
                result['publication_status'] = value
                break
        if '전체 이용가' in text:
            result['books_lv'] = 'everyone'
        else:
            age = re.search(r'(15|18|19)\s*세', text)
            if age:
                result['books_lv'] = {'15': 'ma15+', '18': 'r18', '19': 'adult only'}[age.group(1)]
    synopsis = re.findall(r'<div\b[^>]*class=["\']_synopsis["\'][^>]*>(.*?)</div>', str(source or ''), re.I | re.S)
    if synopsis:
        text = re.sub(r'<span\b[^>]*class=["\']al_r["\'][^>]*>.*?</span>', '', synopsis[-1], flags=re.I | re.S)
        result['summary'] = _html_text(text)
    return result


def _naver_search_cover(source, href):
    """Extract the thumbnail attached to one Naver Series search result.

    Naver leaves the search page's ``og:image`` empty.  The result thumbnail
    is rendered inside the detail link instead, so use the product number to
    keep each candidate's cover paired with its own link.
    """
    product = re.search(r'[?&]productNo=(\d+)', str(href or ''), re.I)
    if not product:
        return ''
    product_no = re.escape(product.group(1))
    pattern = (
        rf'<a\b[^>]*href=["\'][^"\']*productNo={product_no}[^"\']*["\'][^>]*>'
        rf'.*?<img\b[^>]*?(?:data-src|src)=["\']([^"\']+)["\']'
    )
    match = re.search(pattern, str(source or ''), re.I | re.S)
    if not match:
        return ''
    value = html_lib.unescape(str(match.group(1) or '')).strip()
    if not value or re.search(r'(?:noimg|blank|transparent)', value, re.I):
        return ''
    if value.startswith('//'):
        value = 'https:' + value
    if not re.match(r'https?://', value, re.I):
        value = urljoin('https://series.naver.com', value)
    return value


def _kyobo_search_cover(source, href):
    """Find the image belonging to one Kyobo search result, not page og:image."""
    product = re.search(r'/detail/([A-Za-z0-9]+)', str(href or ''), re.I)
    if not product:
        return ''
    product_id = re.escape(product.group(1))
    for match in re.finditer(
            rf'<a\b(?=[^>]*href=["\'][^"\']*/detail/{product_id}(?:[?"\']))'
            r'[^>]*>(.*?)</a>', str(source or ''), re.I | re.S):
        image = re.search(r'<img\b[^>]*>', match.group(1), re.I | re.S)
        if not image:
            continue
        attrs = dict((name.casefold(), html_lib.unescape(value)) for name, value in
                     re.findall(r'([\w-]+)=["\']([^"\']*)["\']', image.group(0)))
        for key in ('data-src', 'data-original', 'src'):
            value = str(attrs.get(key) or '').strip()
            if value and not re.search(r'(?:noimg|blank|transparent)', value, re.I):
                image_url = urljoin('https://search.kyobobook.co.kr', value)
                if urlparse(image_url).scheme in ('http', 'https'):
                    return image_url
        # Kyobo's image loader fills src in the browser from this ISBN field.
        isbn = str(attrs.get('data-kbbfn-bid') or '').strip()
        if re.fullmatch(r'\d{10}|\d{13}', isbn):
            return f'https://contents.kyobobook.co.kr/sih/fit-in/300x0/pdt/{isbn}.jpg'
    return ''


def _ridi_search_cover(item, book, series, book_id):
    """Use the result's cover, then Ridi's product-specific cover endpoint."""
    for payload in (series, book, item):
        for key in ('thumbnail', 'cover', 'bookDetailPageCover', 'cover_url', 'coverUrl', 'image'):
            value = payload.get(key) if isinstance(payload, dict) else ''
            if isinstance(value, dict):
                value = next((value.get(size) for size in ('xxlarge', 'large', 'small')
                              if value.get(size)), '')
            if isinstance(value, str) and value.strip():
                url = urljoin('https://ridibooks.com', value.strip())
                if urlparse(url).scheme in ('http', 'https'):
                    return url
    if re.fullmatch(r'\d+', str(book_id or '')):
        return f'https://img.ridicdn.net/cover/{book_id}/xxlarge#1'
    return ''


def _naver_variant_label(content_kind, title='', href=''):
    """Describe the Naver result's catalogue type beside its source."""
    kind = str(content_kind or '').strip().casefold()
    text = f'{title} {href}'
    if re.search(r'\[\s*단행본\s*\]|단행본', text, re.I):
        return '만화 e북'
    if re.search(r'총\s*\d+\s*화|연재|comic/detail', text, re.I):
        return '만화 연재'
    if kind == 'novel':
        return '라이트노벨'
    if kind == 'book':
        return '도서'
    if kind == 'manhwa':
        return '웹툰'
    return '만화 e북'


def _naver_catalogue_metadata(title):
    """Read completion and a final count from a Naver Series result title."""
    text = _html_text(title)
    result = {}
    if '미완결' in text:
        result['publication_status'] = '0'
    elif '완결' in text:
        result['publication_status'] = '2'
    count = re.search(r'총\s*(\d+)\s*(?:화|권)', text)
    if count and result.get('publication_status') == '2':
        result['total_chapters'] = int(count.group(1))
    return result


def _source_result_filter(source, href):
    href = str(href or '').strip()
    if source == 'ridi':
        return '/books/' in href
    if source == 'naver':
        return bool(re.search(r'series\.naver\.com/(?:comic|novel|ebook)/detail\.series\?[^#]*productNo=\d+', href))
    if source == 'kyobo':
        return ('product.kyobobook.co.kr/detail/' in href or
                'ebook-product.kyobobook.co.kr/' in href)
    return False


def _source_url_allowed(url, source):
    host = urlparse(str(url or '')).hostname or ''
    host = host.casefold().removeprefix('www.')
    allowed = {
        'ridi': {'ridibooks.com'}, 'naver': {'series.naver.com', 'naver.com'},
        'kyobo': {'kyobobook.co.kr'},
    }.get(source, set())
    return any(host == domain or host.endswith('.' + domain) for domain in allowed)


def _source_link(value, source):
    for link in re.split(r'[,;|\n]', str(value or '')):
        link = html_lib.unescape(link).strip()
        if link and _source_url_allowed(link, source):
            return link
    return ''


def _metadata_book_type(content_kind, title='', file_path=''):
    """Map a library/file to the Ridi search category used by the crawlers."""
    kind = _metadata_content_kind(content_kind)
    if kind in ('', 'unspecified'):
        extension = Path(str(file_path or '').replace('\\', '/')).suffix.casefold()
        # Older cores may return ``unspecified`` while the library actually
        # contains text ebooks.  Use the file format as a search hint so a
        # manual Ridi search is not incorrectly sent to the comic catalogue.
        if extension in {'.epub', '.epub3', '.mobi', '.azw', '.azw3', '.fb2', '.txt', '.rtf'}:
            return 'novel'
    title_text = str(title or '').strip()
    file_name = os.path.basename(str(file_path or '').replace('\\', '/')).strip()
    text = f'{title_text} {file_name}'.strip()
    episode_file = bool(re.search(r'(?<!\d)\d+(?:[.,]\d+)?\s*화(?:\D|$)', text, re.I))
    volume_file = bool(re.search(r'(?<!\d)\d+(?:[.,]\d+)?\s*권(?:\D|$)', text, re.I))
    if kind == 'manhwa' or re.search(r'(?:^|[\s(\[【])웹툰(?:$|[\s)\]】])', text, re.I):
        if episode_file or '연재' in text:
            return 'series'
        if volume_file or '단행본' in text:
            return 'single'
        return 'webtoon'
    if kind == 'novel':
        return 'novel'
    if kind == 'manga':
        # Ridi distinguishes 만화 e북 and 만화 연재.  The scanner's title may
        # not retain the filename marker, so keep the filename in the decision
        # and treat any explicit ``연재`` marker as the serialized search.
        return 'series' if episode_file or '연재' in text else 'single'
    if kind == 'book':
        return 'book'
    return ''


def _ridi_variant_label(title='', book=None, item=None, book_type=''):
    """Return the Ridi media category shown next to a search candidate.

    Ridi's search payload does not expose one stable category key across all
    result versions.  The selected search tab is authoritative when it is
    known, while the small category/type fields are used as a fallback for
    older payloads.
    """
    kind = str(book_type or '').strip().casefold()
    by_type = {
        'webtoon': '웹툰',
        'series': '만화 연재',
        'single': '만화 e북',
        'book': '도서',
    }
    if kind in by_type:
        return by_type[kind]

    values = [title]
    for payload in (book, item):
        if not isinstance(payload, dict):
            continue
        for key in (
            'category', 'category_name', 'categoryName', 'type', 'book_type',
            'bookType', 'series_type', 'seriesType', 'publication_type',
            'publicationType', 'kind', 'tab', 'tab_name', 'tabName',
        ):
            value = payload.get(key)
            if isinstance(value, dict):
                value = value.get('name') or value.get('label') or value.get('title')
            if isinstance(value, (list, tuple)):
                value = ' '.join(str(part) for part in value)
            if value:
                values.append(str(value))
        categories = payload.get('categories')
        if isinstance(categories, list):
            values.extend(
                str(category.get('name') if isinstance(category, dict) else category)
                for category in categories if category
            )
    text = ' '.join(values).casefold()
    if kind == 'novel':
        if '라이트노벨' in text or '라노벨' in text:
            return '라이트노벨'
        if '웹소설' in text:
            return '웹소설'
        if 'e북' in text or '전자책' in text or '단행본' in text:
            return '소설 e북'
        return '소설'
    if '웹툰' in text:
        return '웹툰'
    if '웹소설' in text:
        return '웹소설'
    if '라이트노벨' in text or '라노벨' in text:
        return '라이트노벨'
    if '연재' in text:
        return '만화 연재'
    if 'e북' in text or '전자책' in text or '단행본' in text:
        return '만화 e북'
    if '소설' in text:
        return '소설'
    if kind == 'novel':
        return '소설'
    return ''


def _ridi_book_type_allowed(book_type, title='', book=None, item=None):
    """Keep novel searches from surfacing a comic edition from mixed pages."""
    if _metadata_book_type_value(book_type) != 'novel':
        return True
    values = [title]
    category_values = []
    for payload in (book, item):
        if not isinstance(payload, dict):
            continue
        categories = payload.get('categories')
        if isinstance(categories, list):
            category_values.extend(
                str(category.get('name') if isinstance(category, dict) else category)
                for category in categories if category
            )
        for key in ('category', 'category_name', 'categoryName', 'type', 'book_type',
                    'bookType', 'series_type', 'seriesType', 'publication_type',
                    'publicationType', 'kind', 'tab', 'tab_name', 'tabName'):
            value = payload.get(key)
            if isinstance(value, dict):
                value = value.get('name') or value.get('label') or value.get('title')
            if isinstance(value, (list, tuple)):
                value = ' '.join(str(part) for part in value)
            if value:
                values.append(str(value))
    text = ' '.join(values + category_values).casefold()
    # Ridi files Korean fantasy, romance and martial-arts e-books under the
    # WEBNOVEL catalogue, but their category names need not contain "소설".
    novel_category = r'웹소설|라이트노벨|라노벨|소설|(?:판타지|로맨스|로판|무협|BL)\s*e북'
    has_novel = bool(re.search(novel_category, text, re.I))
    has_comic = bool(re.search(r'만화|웹툰', text))
    if category_values:
        # Ridi's mixed search response can include a comic edition even on
        # the WEBNOVEL page. Keep comics out while admitting fantasy e-books.
        category_text = ' '.join(category_values).casefold()
        return bool(re.search(novel_category, category_text, re.I)) and not bool(
            re.search(r'만화|웹툰', category_text))
    return not has_comic or has_novel


def _metadata_display_title(query, title):
    """Use the searched spelling for a title that differs only by spacing."""
    query_text = _metadata_title(query)
    title_text = _metadata_title(title)
    if (query_text and title_text and _metadata_match_key(query_text) == _metadata_match_key(title_text)
            and _title_key(query_text) == _title_key(title_text)
            and not re.search(r'\d|총|완결|미완결|단행본', title_text, re.IGNORECASE)):
        return query_text
    return title_text


def _remote_source_url(source, query, content_kind, book_type='',
                       all_categories=False):
    encoded = quote_plus(str(query or '').strip())
    if source == 'ridi':
        kind = _metadata_content_kind(content_kind)
        ridi_type = _metadata_book_type_value(book_type)
        if all_categories and not ridi_type:
            # The core's generic manual-search endpoint does not pass the
            # selected book's library content kind.  Search all Ridi tabs in
            # that case so a novel is not incorrectly forced into COMIC.
            return f'https://ridibooks.com/search?q={encoded}&tab=ALL&page=1'
        if ridi_type == 'webtoon' or kind == 'manhwa':
            return f'https://ridibooks.com/search?q={encoded}&adult_exclude=n&tab=WEBTOON&page=1'
        if ridi_type == 'novel' or kind == 'novel':
            # Ridi's current search page has no populated NOVEL parent tab.
            # Web novels, light novels, and general novel e-books are exposed
            # through WEBNOVEL, LIGHT_NOVEL, and BOOK/NOVEL respectively.
            tab = 'WEBNOVEL'
            return f'https://ridibooks.com/search?q={encoded}&tab={tab}&page=1'
        if ridi_type == 'book' or kind == 'book':
            return f'https://ridibooks.com/search?q={encoded}&tab=BOOK&page=1'
        tab = 'COMIC'
        child_tab = 'SERIAL' if ridi_type == 'series' else 'EBOOK' if ridi_type == 'single' else ''
        suffix = f'&child_tab={child_tab}' if child_tab else ''
        return f'https://ridibooks.com/search?q={encoded}&tab={tab}&page=1{suffix}'
    if source == 'naver':
        query_type = {'novel': 'novel', 'book': 'ebook', 'manhwa': 'comic'}.get(str(content_kind or '').casefold(), 'comic')
        return f'https://series.naver.com/search/search.series?t={query_type}&q={encoded}'
    if source == 'kyobo':
        return f'https://search.kyobobook.co.kr/search?keyword={encoded}&gbCode=EBK&target=all'
    return ''


def _remote_source_urls(source, query, content_kind, book_type='',
                        all_categories=False):
    """Return the provider pages needed for one metadata search.

    Ridi splits the novel catalogue across three tabs. Query all of them for
    a novel library so a web novel is not hidden by a light-novel-only or
    invalid aggregate tab.
    """
    primary = _remote_source_url(
        source, query, content_kind, book_type,
        all_categories=all_categories)
    if source != 'ridi':
        return [primary] if primary else []
    kind = _metadata_content_kind(content_kind)
    ridi_type = _metadata_book_type_value(book_type)
    if ridi_type != 'novel' and kind != 'novel':
        return [primary] if primary else []
    encoded = quote_plus(str(query or '').strip())
    return [
        primary,
        f'https://ridibooks.com/search?q={encoded}&tab=LIGHT_NOVEL&page=1',
        f'https://ridibooks.com/search?q={encoded}&tab=BOOK&child_tab=NOVEL&page=1',
    ]


def _remote_search_page(source, query, content_kind, book_type='', limit=8,
                        allow_partial=False, url_override=''):
    url = url_override or _remote_source_url(source, query, content_kind, book_type)
    if not url:
        return []
    try:
        import requests
        response = requests.get(url, headers={
            'User-Agent': 'Mozilla/5.0 RabbitPlugins/1.0',
            'Accept-Language': 'ko-KR,ko;q=0.9,en;q=0.6',
        }, timeout=12)
        response.raise_for_status()
        parser = _MetadataPageParser()
        parser.feed(response.text)
    except Exception as error:
        # A provider timeout, DNS failure, or proxy HTML response used to look
        # exactly like a valid search with no matches.  Keep the UI resilient,
        # but leave a useful reason in the server log so a NAS deployment can
        # distinguish network access from title matching problems.
        print(f'[RabbitPlugins-Metadata] {source} 검색 실패 query={query!r} '
              f'url={url!r}: {type(error).__name__}: {error}')
        return []
    result = []
    seen = set()
    if source == 'ridi':
        next_data_items = _next_data_books(response.text)
        for item in next_data_items:
            book_id = item.get('id') or item.get('book_id') or item.get('bookId')
            if not book_id:
                continue
            href = f'https://ridibooks.com/books/{book_id}'
            if href in seen:
                continue
            book = item.get('book') if isinstance(item.get('book'), dict) else {}
            series = book.get('series') if isinstance(book.get('series'), dict) else {}
            title_value = series.get('title') or book.get('title') or item.get('title') or item.get('name') or ''
            if isinstance(title_value, dict):
                title_value = title_value.get('main') or title_value.get('title') or title_value.get('name') or ''
            title = _ridi_search_title(title_value)
            # NextData sometimes contains a book description or a related
            # product in the same ``books`` array.  Automatic collection only
            # accepts exact titles; manual searches may use a shortened title.
            if (len(title) < 2 or _metadata_variant_excluded(title)
                    or not _ridi_book_type_allowed(book_type, title, book, item)
                    or not _metadata_title_matches(query, title,
                                                    allow_partial=allow_partial)):
                continue
            title = _metadata_display_title(query, title)
            authors = book.get('authors') or item.get('author') or item.get('authors') or item.get('writer')
            if isinstance(authors, list):
                authors = [author.get('name') if isinstance(author, dict) else author for author in authors]
            publication = book.get('publicationInfo') if isinstance(book.get('publicationInfo'), dict) else {}
            categories = book.get('categories') if isinstance(book.get('categories'), list) else []
            category_names = [
                str(category.get('name') if isinstance(category, dict) else category)
                for category in categories if category
            ]
            category_genres = _join_terms(
                (['웹소설'] if any('웹소설' in name for name in category_names) else [])
                + category_names)
            seen.add(href)
            result.append({
                'id': f'ridi:{book_id}', 'title': title[:500], 'url': href, 'link': href,
                'source': 'ridi', 'source_label': METADATA_SOURCE_LABELS['ridi'],
                'variant_label': _ridi_variant_label(title, book, item, book_type),
                'author': _join_terms(authors),
                'publisher': str(publication.get('name') or item.get('publisher') or '').strip(),
                'genre': category_genres,
                'cover': _ridi_search_cover(item, book, series, book_id),
            })
            if len(result) >= limit:
                return result
        # The current Ridi page always includes structured Next.js results.
        # Do not fall through to navigation links, whose labels can describe
        # a neighboring edition and reintroduce a comic as a novel result.
        if next_data_items:
            return result
    for link in parser.links:
        href = urljoin(url, link.get('href') or '')
        if not _source_result_filter(source, href) or href in seen:
            continue
        if source == 'ridi':
            book_id_match = re.search(r'/books/(\d+)', href)
            if book_id_match:
                href = f'https://ridibooks.com/books/{book_id_match.group(1)}'
                if href in seen:
                    continue
        title = _ridi_search_title(_html_text(link.get('text') or '')) if source == 'ridi' else _metadata_title(_html_text(link.get('text') or ''))
        if len(title) < 2 or title.casefold() in {'검색', '상세보기', '더보기'} or _metadata_variant_excluded(title):
            continue
        # Keep results tied to the title.  Partial matching is enabled only
        # for an explicit manual search, so automatic collection cannot merge
        # an unrelated work whose description happens to contain the query.
        if not _metadata_title_matches(query, title, allow_partial=allow_partial):
            continue
        title = _metadata_display_title(query, title)
        seen.add(href)
        # The search page's og:image is a site-wide logo, not a product cover.
        cover = ''
        variant_label = _ridi_variant_label(title, book_type=book_type) if source == 'ridi' else ''
        if source == 'naver':
            cover = _naver_search_cover(response.text, href)
            variant_label = _naver_variant_label(content_kind, title, href)
        elif source == 'kyobo':
            cover = _kyobo_search_cover(response.text, href)
        if not cover:
            detail_cover = _remote_fetch_metadata(href, source).get('cover', '')
            if not (source == 'kyobo' and '/resources/fo/images/common/' in detail_cover):
                cover = detail_cover
        candidate = {
            'id': f'{source}:{hashlib.sha1(href.encode()).hexdigest()[:12]}',
            'title': title[:500], 'url': href, 'link': href,
            'source': source, 'source_label': METADATA_SOURCE_LABELS[source],
            'variant_label': variant_label,
            'publisher': '네이버시리즈' if source == 'naver' else '',
            'cover': cover,
        }
        if not _metadata_source_variant_allowed(candidate, content_kind, book_type):
            continue
        result.append(candidate)
        if len(result) >= limit:
            break
    return result


def _remote_search(source, query, content_kind, book_type='', limit=8,
                   allow_partial=False, all_categories=False):
    """Search one provider, combining Ridi's split novel catalogues."""
    results = []
    seen = set()
    for url in _remote_source_urls(
            source, query, content_kind, book_type,
            all_categories=all_categories):
        remaining = max(0, int(limit or 0) - len(results))
        if remaining <= 0:
            break
        page_results = _remote_search_page(
            source, query, content_kind, book_type,
            limit=remaining, allow_partial=allow_partial,
            url_override=url)
        for candidate in page_results:
            key = str(candidate.get('url') or candidate.get('id') or '').casefold()
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            results.append(candidate)
            if len(results) >= limit:
                break
    return results[:limit]


def _yes24_credits(value):
    """Separate YES24's display suffixes from writer and illustrator names."""
    writers = []
    artists = []
    for part in re.split(r'[,;|\n]+', str(value or '')):
        name = _html_text(part)
        if not name:
            continue
        match = re.fullmatch(r'(.+?)\s+(저|지음|글|공저|그림|역|옮김|감수|편저|편)', name)
        if match:
            name, role = match.groups()
            if role == '그림':
                artists.append(name)
                continue
            if role in ('역', '옮김', '감수', '편저', '편'):
                continue
        writers.append(name)
    return _join_terms(writers), _join_terms(artists)


def _yes24_public_summary(item_id):
    """Read the public introduction when the official Goods API omits it."""
    if not re.fullmatch(r'\d+', str(item_id or '')):
        return ''
    url = f'https://www.yes24.com/product/goods/{item_id}'
    try:
        import requests
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=8)
        response.raise_for_status()
        if urlparse(getattr(response, 'url', url)).path != f'/product/goods/{item_id}':
            return ''
    except Exception as error:
        print(f'[RabbitPlugins-Metadata] 예스24 공개 소개 조회 실패: {type(error).__name__}')
        return ''
    match = re.search(
        r'<div\b[^>]*\bid=["\']infoset_introduce["\'][^>]*>'
        r'.*?<textarea\b[^>]*\bclass=["\'][^"\']*\btxtContentText\b[^"\']*["\'][^>]*>'
        r'(.*?)</textarea>', response.text, re.I | re.S)
    if not match:
        return ''
    intro = html_lib.unescape(match.group(1))
    intro = re.sub(r'<br\s*/?>', '\n', intro, flags=re.I)
    intro = re.sub(r'<[^>]+>', ' ', intro)
    intro = re.sub(r'[^\S\n]+', ' ', intro)
    intro = re.sub(r'\n\s*\n+', '\n', intro).strip()
    return _metadata_summary(intro[:20000])


def _yes24_candidate_item_id(candidate):
    value = str(candidate.get('id') or '')
    match = re.fullmatch(r'yes24:(\d+)', value)
    if match:
        return match.group(1)
    match = re.fullmatch(
        r'https://www\.yes24\.com/product/goods/(\d+)',
        str(candidate.get('url') or candidate.get('link') or ''))
    return match.group(1) if match else ''


def _yes24_item_kind(item):
    """Classify a product from YES24's catalogue path, not its eBook flag."""
    goods_type = str(item.get('goodsType') or '').strip().casefold()
    parts = [part.strip().casefold() for part in re.split(r'\s*[-＞>]\s*',
             str(item.get('goodsSortNm') or '')) if part.strip()]
    if goods_type not in {'ebook', '도서', '국내도서', '외국도서', '만화'} or len(parts) < 2:
        return ''
    if parts[0] not in {'ebook', '국내도서', '외국도서'}:
        return ''
    section = parts[1]
    rest = parts[2:]
    if section == '무료ebook' and rest:
        section, rest = rest[0], rest[1:]
    if any('웹툰' in part for part in parts[1:]):
        return 'manhwa'
    if section in {'만화', 'bl만화'} or (parts[0] == '외국도서' and
            any('만화' in part for part in rest)):
        return 'manga'
    if section == '만화/라이트노벨':
        if any('라이트노벨' in part for part in rest):
            return 'novel'
        return 'manga' if goods_type == '만화' else ''
    if section in YES24_NOVEL_SECTIONS or section in {'소설', '소설/시/희곡', 'bl'}:
        return 'novel'
    if section == '문학' and any('소설' in part for part in rest):
        return 'novel'
    if section == '잡지':
        return 'magazine'
    if section in {'무료ebook', '오디오북', 'ebook 리더기', 'ebook 대량/법인',
                   '대여', 'gl'}:
        return ''
    return 'book'


def _yes24_edition(title):
    """A numbered episode is serialized; a volume or bare title is an e-book."""
    title = unicodedata.normalize('NFKC', html_lib.unescape(str(title or '')))
    if (re.search(r'^\s*(?:\[[^\]]*연재[^\]]*\]|【[^】]*연재[^】]*】)', title)
            or re.search(r'(?<!\d)\d+(?:[.,]\d+)?\s*화(?:\D|$)', title)
            or re.search(r'총\s*\d+\s*화', title)):
        return 'series'
    return 'single'


def _yes24_local_edition(file_names):
    """Use local episode markers; an unnumbered file defaults to e-book."""
    names = [os.path.basename(str(name).replace('\\', '/')) for name in file_names or ()]
    editions = {_yes24_edition(Path(name).stem) for name in names if name}
    return editions.pop() if len(editions) == 1 else ('single' if not editions else '')


def _yes24_search(query, content_kind, api_key, allow_partial=False, limit=8,
                  edition=''):
    """Search the official YES24 Goods API without exposing its key in URLs."""
    api_key = str(api_key or '').strip()
    if not api_key:
        return []
    try:
        import requests
        response = requests.get(
            'https://apis.yes24.com/v1/goods/itemList',
            params={
                'query': query,
                'category': 'ALL' if content_kind == 'book' else 'EBOOK',
                'page': 1, 'pageSize': 40, 'detail': 'Y',
            },
            headers={'X-Api-Key': api_key, 'Accept': 'application/json'},
            timeout=12,
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get('success') is not True:
            print('[RabbitPlugins-Metadata] 예스24 검색 응답 실패: '
                  f'{str(payload.get("errorCode") or "invalid response") if isinstance(payload, dict) else "invalid response"}')
            return []
    except Exception as error:
        print(f'[RabbitPlugins-Metadata] 예스24 검색 실패: {type(error).__name__}')
        return []
    data = payload.get('data') or {}
    items = data.get('items') if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    results = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get('itemId') or '').strip()
        title = str(item.get('title') or '').strip()
        if (not item_id.isdigit() or item_id in seen or len(title) < 2
                or _metadata_variant_excluded(title)
                or not _metadata_title_matches(query, title, allow_partial=allow_partial)):
            continue
        item_kind = _yes24_item_kind(item)
        requested_kind = {'잡지': 'magazine'}.get(content_kind, content_kind)
        if not item_kind or (requested_kind in {'book', 'manga', 'novel', 'manhwa', 'magazine'}
                             and item_kind != requested_kind):
            continue
        item_edition = _yes24_edition(title)
        if edition and item_edition != edition:
            continue
        seen.add(item_id)
        link = f'https://www.yes24.com/product/goods/{item_id}'
        content = item.get('contentDetail') if isinstance(item.get('contentDetail'), dict) else {}
        author, cover_artist = _yes24_credits(item.get('author'))
        original_title = str(item.get('originalTitle') or '').strip()
        cover = str(item.get('cover') or '').strip()
        if urlparse(cover).scheme != 'https':
            cover = ''
        metadata = _metadata_clean({
            'title': title,
            'localized_series': original_title if _title_key(original_title) != _title_key(title) else '',
            'author': author, 'cover_artist': cover_artist,
            'publisher': item.get('publisher'),
            'summary': content.get('bookIntroduction') or content.get('bookSummary') or '',
            'cover': cover, 'isbn': item.get('isbn13') or item.get('isbn10'),
            'release_date': item.get('publishDate'), 'link': link,
            'books_lv': 'adult only' if str(item.get('adultYn') or '').upper() == 'Y' else '',
        })
        media_type = {'novel': '소설 e북', 'manga': '만화 e북',
                      'manhwa': '웹툰', 'book': '도서', 'magazine': '잡지'}.get(item_kind, '전자책')
        if item_edition == 'series':
            media_type = {'novel': '웹소설', 'manga': '만화 연재',
                          'manhwa': '웹툰 연재'}.get(item_kind, media_type)
        results.append({
            'id': f'yes24:{item_id}', 'title': title, 'url': link, 'link': link,
            'source': 'yes24', 'source_label': METADATA_SOURCE_LABELS['yes24'],
            'variant_label': media_type, 'author': metadata.get('author', ''),
            'publisher': metadata.get('publisher', ''), 'cover': metadata.get('cover', ''),
            'metadata': metadata, '_metadata_fetched': True,
        })
        if len(results) >= limit:
            break
    return results


class _KyoboBookParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}
        self.depth = 0
        self.text = []
        self.in_button = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'input' and attrs.get('id'):
            self.values[attrs['id']] = attrs.get('value', '')
        if tag == 'div':
            if self.depth:
                self.depth += 1
            elif attrs.get('id') == 'bookIntc':
                self.depth = 1
        if self.depth and tag == 'button':
            self.in_button = True
        if self.depth and not self.in_button and tag in ('br', 'p'):
            self.text.append('\n')

    def handle_endtag(self, tag):
        if tag == 'button':
            self.in_button = False
        if tag == 'div' and self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth and not self.in_button:
            self.text.append(data)


def _kyobo_detail_metadata(source):
    parser = _KyoboBookParser()
    parser.feed(source)
    fields = {'cmdtHnglName': 'title', 'autrNm': 'author', 'pbcmName': 'publisher',
              'stdBksIdnfNum': 'isbn'}
    result = {field: parser.values[key].strip() for key, field in fields.items() if parser.values.get(key)}
    genre = _join_terms([parser.values.get(key, '') for key in ('largeCtgrName', 'middleCtgrName', 'subCtgrName')])
    if genre:
        result['genre'] = genre
    published = parser.values.get('elbkPublDate', '')
    if re.fullmatch(r'\d{8}', published):
        result['release_date'] = _metadata_date(f'{published[:4]}-{published[4:6]}-{published[6:]}')
    summary = '\n'.join(line.strip() for line in ''.join(parser.text).splitlines() if line.strip())
    if summary:
        result['summary'] = summary
    return result


def _remote_fetch_metadata(url, source):
    if not _source_url_allowed(url, source):
        return {'link': url} if url else {}
    try:
        import requests
        response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0 RabbitPlugins/1.0'}, timeout=12)
        response.raise_for_status()
        parser = _MetadataPageParser()
        parser.feed(response.text)
        metadata = _json_ld_metadata(response.text)
        book_id_match = re.search(r'/books/(\d+)', str(url or ''))
        detail_data = _next_data_detail_metadata(
            response.text, book_id_match.group(1) if book_id_match else '')
        # Ridi exposes ISBN in a books:isbn meta tag on pages where JSON-LD
        # has no isbn value. Keep this source-specific value before cleaning
        # the response into the common metadata shape.
        if not metadata.get('isbn'):
            metadata['isbn'] = (
                parser.meta.get('books:isbn')
                or parser.meta.get('book:isbn')
                or parser.meta.get('isbn')
                or ''
            )
        metadata.update({
            'title': metadata.get('title') or parser.meta.get('og:title') or parser.meta.get('twitter:title'),
            'summary': detail_data.get('summary') or metadata.get('summary') or parser.meta.get('description') or parser.meta.get('og:description'),
            'cover': (
                detail_data.get('cover') if source == 'ridi' and detail_data.get('cover')
                else metadata.get('cover') or parser.meta.get('og:image') or parser.meta.get('twitter:image')
            ),
            'link': url, 'source': source,
        })
        ridi_cover_by_volume = {}
        ridi_cover_by_title = {}
        if source == 'ridi':
            ridi_genres = _ridi_genres(response.text)
            if ridi_genres:
                # JSON-LD often reports only the adult marker.  Ridi's
                # category links are authoritative for the media category,
                # so keep the parent (for example ``만화 e북``) and children.
                metadata['genre'] = _join_terms(ridi_genres)
            metadata.update(_ridi_role_metadata(response.text))
            metadata.update(_ridi_publication_metadata(response.text))
            ridi_cover_by_volume, ridi_cover_by_title = _ridi_volume_cover_maps(response.text)
        elif source == 'naver':
            metadata.update(_naver_role_metadata(response.text))
            # The Naver catalogue provider is Naver Series. Keep this stable
            # even when a detail page exposes a separate content imprint.
            metadata['publisher'] = '네이버시리즈'
        elif source == 'kyobo':
            metadata.update(_kyobo_detail_metadata(response.text))
        keyword_text = parser.meta.get('keywords', '') or detail_data.get('tags', '')
        hashtag_text = re.findall(r'#([^\s,#]+)', str((parser.meta.get('description') if source == 'naver' else metadata.get('summary')) or ''))
        if keyword_text or hashtag_text:
            metadata['tags'] = keyword_text or ', '.join(hashtag_text)
        if source == 'ridi' and any(
                '웹소설' in str(value or '')
                for value in (metadata.get('genre'), metadata.get('tags'))):
            metadata['genre'] = _join_terms(['웹소설', metadata.get('genre')])
        if source == 'naver':
            metadata['tags'] = _metadata_remove_terms(metadata.get('tags', ''), 'NOVEL, COMIC, WEBTOON')
        result = _metadata_clean(metadata)
        if ridi_cover_by_volume:
            result['cover_by_volume'] = ridi_cover_by_volume
        if ridi_cover_by_title:
            result['cover_by_title'] = ridi_cover_by_title
        return result
    except Exception as error:
        print(f'[RabbitPlugins-Metadata] {source} 상세 수집 실패 url={url!r}: '
              f'{type(error).__name__}: {error}')
        return {'link': url} if url else {}


def _kyobo_code_candidate(query, code):
    """Create a search candidate for a Kyobo S/E code.

    The normal provider search is title based, but a code identifies one
    volume exactly.  Fetching the direct page here gives the manual result a
    real title/cover while the ``_match_titles`` fallback still lets automatic
    collection accept a provider title that differs in spacing or volume text.
    """
    normalized_code = _metadata_ebook_code(code)
    url = _kyobo_product_url(normalized_code)
    if not url:
        return None
    fetched = _remote_fetch_metadata(url, 'kyobo')
    metadata = _metadata_clean(fetched)
    title = metadata.get('title') or str(query or '').strip() or normalized_code
    fetched_ok = bool(metadata.get('title') or metadata.get('author')
                      or metadata.get('publisher') or metadata.get('cover'))
    return {
        'id': f'kyobo:{normalized_code}',
        'title': title,
        'url': url,
        'link': url,
        'source': 'kyobo',
        'source_label': METADATA_SOURCE_LABELS['kyobo'],
        'author': metadata.get('author', ''),
        'publisher': metadata.get('publisher', ''),
        'cover': metadata.get('cover', ''),
        'metadata': metadata,
        '_match_titles': [str(query or '').strip(), title],
        '_metadata_fetched': fetched_ok,
    }


class RabbitPluginsMetadataProvider(BaseMetadataProvider):
    id = 'rabbit_plugins'
    name = 'Rabbit Plugins · 상세페이지'
    version = PLUGIN_VERSION
    is_searchable = True
    config_schema = [
        {'key': 'support_summary_html', 'label': 'ComicInfo 소개 이미지 표시', 'type': 'checkbox', 'default': True},
        {'key': 'series_db_path', 'label': 'Series.db 경로', 'type': 'text', 'default': ''},
        {'key': 'exclude_tags', 'label': '제외할 태그', 'type': 'text', 'default': ''},
        {'key': 'exclude_genres', 'label': '제외할 장르', 'type': 'text', 'default': ''},
        {'key': 'metadata_auto_enabled', 'label': '메타데이터 자동 수집', 'type': 'checkbox', 'default': False},
        {'key': 'novelpia_author_search_url', 'label': '노벨피아 작가 보조 검색 URL (선택)', 'type': 'text', 'default': ''},
        {'key': 'metadata_sources', 'label': '메타데이터 제공처 우선순위', 'type': 'text', 'default': 'series_db,ridi,naver,naver_webtoon,kyobo,yes24,kakaopage,kakao_webtoon,munpia,novelpia'},
        {'key': 'yes24_api_key', 'label': '예스24 Open API 키', 'type': 'password', 'default': ''},
        {'key': 'metadata_fields', 'label': '자동으로 가져올 필드', 'type': 'text', 'default': 'title,localized_series,author,cover_artist,publisher,genre,tags,release_date,isbn,link,cover'},
        {'key': 'metadata_genre_map', 'label': '장르 변환', 'type': 'text', 'default': ''},
        {'key': 'metadata_publisher_map', 'label': '출판사 변환', 'type': 'text', 'default': ''},
        {'key': 'metadata_search_remove_keywords', 'label': '검색에서 제거할 키워드', 'type': 'text', 'default': ''},
        {'key': 'metadata_search_keep_keywords', 'label': '검색에서 유지할 키워드', 'type': 'text', 'default': ''},
        {'key': 'metadata_collect_cover', 'label': '웹툰 표지 가져오기', 'type': 'checkbox', 'default': True},
        {'key': 'metadata_cover_kinds', 'label': '표지를 가져올 자료 유형', 'type': 'text', 'default': 'manga,novel,manhwa,unspecified'},
        {'key': 'metadata_manual_overwrite', 'label': '수동 메타데이터 적용 시 기존 값 덮어쓰기', 'type': 'checkbox', 'default': False},
        {'key': 'metadata_overwrite', 'label': '기존 메타데이터 덮어쓰기', 'type': 'checkbox', 'default': False},
        {'key': 'metadata_overwrite_kinds', 'label': '기존 메타데이터 덮어쓰기 자료 유형', 'type': 'text', 'default': ''},
        {'key': 'metadata_cover_overwrite', 'label': '기존 표지 덮어쓰기', 'type': 'checkbox', 'default': False},
        {'key': 'metadata_cover_overwrite_kinds', 'label': '기존 표지 덮어쓰기 자료 유형', 'type': 'text', 'default': ''},
    ]
    detail_view = {'title': 'Rabbit Plugins · 상세페이지', 'sessions': ['general', 'adult', 'audiobook', 'video']}
    home_widget = {
        'title': '라이브러리별 신규 도서',
        'subtitle': '선택한 라이브러리의 최근 추가 작품',
        'icon': 'fa-solid fa-square-plus',
        'order': 30,
        'limit': 20,
        'sessions': ['general'],
        'layout': 'full',
        # 홈 화면 HTML에 첫 데이터를 함께 주입해 초기 API 요청과 로딩 깜빡임을 줄인다.
        'initial_data': True,
    }
    dashboard_widget = None
    category_tab = None
    # 저장소 설치 검증과 플러그인 매니저의 자동 업데이트에 사용하는 계약.
    # 저장소 루트가 곧 플러그인 루트이므로 raw_base_url 아래에 런타임 파일을
    # 직접 나열한다. README/CHANGELOG는 실행 파일이 아니어서 업데이트 대상에서
    # 제외한다.
    update_manifest = {
        'enabled': True,
        'provider': 'github-raw',
        'raw_base_url': 'https://raw.githubusercontent.com/daydreamrabbit/rabbit_plugins/main',
        'files': [
            'rabbit_plugins.py', 'provider_search.py', '__init__.py', 'VERSION',
            'settings.html', 'settings.css', 'settings.js',
            'dashboard.html', 'dashboard.css', 'dashboard.js',
            'detail/index.html', 'detail/style.css', 'detail/script.js',
        ],
        'version_file': 'VERSION',
        'version_key': 'plugin version',
        'show_sample_update_button': True,
    }

    def start_background_service(self, db_type):
        """Keep external cover overwrite authoritative after remote Lazy scans.

        BookOasis starts this hook once per application worker.  A process-wide
        guard plus an advisory file lock leaves one lightweight reconciler for
        the whole container.  It reads only saved DB preferences and never
        repeats provider HTTP requests.
        """
        global _COVER_RECONCILE_STARTED
        with _COVER_RECONCILE_START_LOCK:
            if _COVER_RECONCILE_STARTED:
                return
            _COVER_RECONCILE_STARTED = True

        def worker():
            lock_path = os.path.join(tempfile.gettempdir(), 'rabbit_plugins_cover_reconcile.lock')
            try:
                lock_file = open(lock_path, 'a+b')
            except OSError:
                return
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError):
                lock_file.close()
                return
            try:
                while True:
                    for target_db in ('general', 'adult'):
                        try:
                            _reconcile_external_covers(self, target_db)
                        except Exception as error:
                            print(f'[RabbitPlugins-Metadata] cover reconcile failed db={target_db}: '
                                  f'{type(error).__name__}: {error}')
                    time.sleep(10)
            finally:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                finally:
                    lock_file.close()

        threading.Thread(
            target=worker,
            name='rabbit-cover-reconcile',
            daemon=True,
        ).start()

    def search(self, db_type, query):
        config = self.get_plugin_config(db_type or 'general', {}) or {}
        # The provider search endpoint is an explicit manual search.  It may
        # return a longer canonical title for a shortened query; automatic
        # scan collection keeps its exact-title policy below.
        return self._search_metadata(
            str(query or '').strip(), config, db_type, manual=True)

    def apply(self, db_type, book_id, item_data):
        if not has_request_context() or session.get('role') != 'admin':
            return False, '관리자만 메타데이터를 저장할 수 있습니다.'
        try:
            book_id = int(book_id)
        except (TypeError, ValueError):
            return False, '올바른 도서를 선택해 주세요.'
        config = self.get_plugin_config(db_type or 'general', {}) or {}
        gateway = self.get_db_gateway(db_type or 'general')
        return self._apply_metadata(gateway, book_id, item_data or {}, config, manual=True)

    def _search_metadata(self, query, config, db_type='general', content_kind=None,
                         book_type='', manual=False, ebook_code='', author_hint='', refresh=False,
                         yes24_edition=''):
        ebook_code = _metadata_ebook_code(ebook_code or query)
        query, query_author = _metadata_book_folder_identity(query, content_kind)
        author_hint = author_hint or query_author
        query = _metadata_search_query(query, config)
        if len(query) < 2:
            if not ebook_code:
                return []
            # A code-only manual search still needs an identity for candidate
            # matching; providers other than Kyobo will simply return no
            # title result for this fallback query.
            query = ebook_code
        content_kind = _metadata_content_kind(
            content_kind or ('novel' if db_type == 'adult' else 'manga'))
        book_type = _metadata_book_type_value(book_type)
        if not manual and not yes24_edition:
            yes24_edition = 'series' if book_type == 'series' else 'single'
        yes24_api_key = str(config.get('yes24_api_key') or '').strip()
        if not yes24_api_key and db_type != 'general' and 'yes24' in _metadata_source_order(config):
            # Plugin management saves its settings in the general DB; the
            # adult detail view can reuse that key without copying it to a
            # second library database.
            try:
                yes24_api_key = str((self.get_plugin_config('general', {}) or {}).get('yes24_api_key') or '').strip()
            except Exception:
                yes24_api_key = ''
        # Candidate filtering and media-type labels are part of the result
        # shape; invalidate older cached cards that may contain description
        # rows from the unfiltered Ridi response.
        # v10 intentionally invalidates the older cache, which could contain an
        # empty response from a transient NAS/proxy failure.  Empty searches
        # are not cached so a later scan can retry the provider immediately.
        # Manual and automatic searches have different title matching rules,
        # so they must never share a cache entry.
        cache_key = 'metadata-search:v30:' + hashlib.sha256(
            json.dumps([
                query, content_kind, book_type,
                _metadata_source_order(config), bool(manual), ebook_code, author_hint,
                yes24_edition,
                config.get("novelpia_author_search_url", ""),
                hashlib.sha256(yes24_api_key.encode()).hexdigest(),
            ], ensure_ascii=False).encode()
        ).hexdigest()
        # Manual searches must query every selected provider on every submit.
        # Caching a successful Naver response while Ridi is temporarily
        # unreachable makes the missing Ridi card look permanent for the next
        # five minutes, even after the provider is reachable again.
        if not manual and not refresh:
            try:
                cached = self.cache_get(cache_key)
                if cached:
                    return json.loads(cached)
            except (TypeError, ValueError):
                pass
        results = []
        seen = set()
        seen_work_keys = set()
        source_order = _metadata_source_order(config)
        for source in source_order:
            if not _metadata_source_supports_kind(source, content_kind):
                continue
            if source in SOURCE_KINDS:
                try:
                    candidates = search_additional_provider(
                        source, query,
                        lambda q, title: _metadata_title_matches(q, title, allow_partial=manual),
                        content_kind=content_kind)
                except Exception as error:
                    print(f'[RabbitPlugins-Metadata] {source} 검색 실패: {type(error).__name__}: {error}')
                    candidates = []
                if source == 'novelpia':
                    author_key = 'novelpia-author:v1:' + hashlib.sha256(
                        re.sub(r'\s+', '', author_hint).casefold().encode()).hexdigest()
                    try:
                        known_ids = json.loads(self.cache_get(author_key) or '[]') if author_hint else []
                        if not isinstance(known_ids, list):
                            known_ids = []
                    except (TypeError, ValueError):
                        known_ids = []
                    # A successful title search also teaches subsequent searches
                    # the author profile, without any external search service.
                    found_ids = [str(c.get('author_id')) for c in candidates
                        if str(c.get('author_id') or '').isdigit()
                        and _metadata_author_matches(author_hint, _metadata_candidate_author(c))]
                    if found_ids and author_hint:
                        self.cache_set(author_key, json.dumps(list(dict.fromkeys(found_ids))[:3]), ttl=604800)
                    matched = bool(candidates) if not author_hint else any(
                        _metadata_author_matches(author_hint, _metadata_candidate_author(c)) for c in candidates)
                    search_url = str(config.get('novelpia_author_search_url') or '').strip()
                    if not matched:
                        try:
                            extra, verified = search_novelpia_author(
                                query, author_hint,
                                lambda q, title: _metadata_title_matches(q, title, allow_partial=manual),
                                search_url=search_url, known_ids=known_ids)
                            if not verified and known_ids and search_url:
                                extra, verified = search_novelpia_author(
                                    query, author_hint,
                                    lambda q, title: _metadata_title_matches(q, title, allow_partial=manual),
                                    search_url=search_url)
                            candidates.extend(extra)
                            if not verified:
                                print('[RabbitPlugins-Metadata] novelpia 공개 검색에서 작가 번호를 확인하지 못함: 작품 없음으로 확정하지 않음')
                            if verified and author_hint:
                                self.cache_set(author_key, json.dumps(verified), ttl=604800)
                        except Exception as error:
                            print(f'[RabbitPlugins-Metadata] novelpia 작가 보조 검색 실패: {type(error).__name__}')
            elif source == 'series_db':
                candidates = self._series_db_search(query, content_kind)
            elif source == 'yes24':
                candidates = _yes24_search(
                    query, content_kind, yes24_api_key,
                    allow_partial=bool(manual), edition=yes24_edition)
            elif source == 'kyobo' and ebook_code:
                direct = _kyobo_code_candidate(query, ebook_code)
                candidates = [direct] if direct else []
            else:
                candidates = _remote_search(
                    source, query, content_kind, book_type,
                    allow_partial=bool(manual),
                    all_categories=bool(manual and source == 'ridi'
                                       and not book_type
                                       and content_kind == 'manga'))
            for candidate in candidates:
                if (author_hint and _metadata_candidate_author(candidate)
                        and not _metadata_author_matches(author_hint, _metadata_candidate_author(candidate))):
                    continue
                key = str(candidate.get('url') or candidate.get('id') or '').casefold()
                if not key or key in seen:
                    continue
                source = str(candidate.get('source') or '').casefold()
                title_key = _metadata_match_key(
                    candidate.get('title') or (candidate.get('metadata') or {}).get('title'))
                work_key = ((source, title_key, _yes24_edition(candidate.get('title')))
                            if source == 'yes24' and title_key else
                            (source, title_key) if source in METADATA_SOURCES and source != 'series_db' and title_key
                            else None)
                if work_key and work_key in seen_work_keys:
                    continue
                seen.add(key)
                if work_key:
                    seen_work_keys.add(work_key)
                candidate['metadata'] = _metadata_clean(candidate.get('metadata') or candidate)
                candidate['variant_label'] = _metadata_candidate_variant_label(
                    candidate, content_kind, book_type)
                results.append(candidate)
                if len(results) >= 24:
                    break
            if len(results) >= 24:
                break
        if 'naver' in source_order and 'naver_webtoon' in source_order:
            results = self._combine_naver_candidates(results, book_type)
        if 'kakaopage' in source_order and 'kakao_webtoon' in source_order:
            results = self._combine_kakao_candidates(results)
        if results and not manual and not refresh:
            try:
                self.cache_set(cache_key, json.dumps(results, ensure_ascii=False), ttl=300)
            except Exception:
                pass
        return results

    @staticmethod
    def _combine_naver_candidates(candidates, book_type=''):
        """Attach Naver Webtoon serialization fields to Naver Series cards.

        Naver Series remains the primary catalogue for title, edition, cover,
        credits and publisher. Naver Webtoon supplies the reliable hiatus,
        completion and publication timeline. Episode totals are not copied to
        a collected-volume candidate because the units are different.
        """
        series_rows = [row for row in candidates if row.get('source') == 'naver']
        webtoon_rows = [row for row in candidates if row.get('source') == 'naver_webtoon']
        if not series_rows or not webtoon_rows:
            return candidates
        replacements = {}
        consumed_webtoon = set()
        for primary in series_rows:
            fallback = next((row for row in webtoon_rows if _metadata_title_matches(
                primary.get('title'), row.get('title'))), None)
            if not fallback:
                continue
            fetched = _remote_fetch_metadata(primary.get('url'), 'naver') if primary.get('url') else {}
            primary_values = _metadata_clean({
                **primary,
                **fetched,
                **_naver_catalogue_metadata(primary.get('title')),
            })
            fallback_values = _metadata_clean(fallback.get('metadata') or fallback)
            merged = dict(fallback_values)
            merged.update({key: value for key, value in primary_values.items() if value})
            # Naver Webtoon is authoritative for these fields, which Naver
            # Series either omits or reduces to a generic ongoing value.
            for field in ('publication_status', 'publication_start_date',
                          'publication_end_date'):
                if fallback_values.get(field):
                    merged[field] = fallback_values[field]
            if book_type != 'single' and fallback_values.get('total_chapters'):
                merged['total_chapters'] = fallback_values['total_chapters']
            elif book_type == 'single' and not primary_values.get('total_chapters'):
                merged.pop('total_chapters', None)
            merged['link'] = _join_links(
                primary.get('url'), primary_values.get('link'), fallback_values.get('link'))
            combined = dict(primary)
            combined.update({key: value for key, value in merged.items() if value})
            combined.update({
                'source': 'naver',
                'source_label': '네이버시리즈 + 네이버웹툰',
                'title': primary.get('title'),
                'url': primary.get('url'),
                'metadata': merged,
                '_metadata_fetched': True,
                '_combined_sources': ['naver', 'naver_webtoon'],
            })
            replacements[id(primary)] = combined
            consumed_webtoon.add(id(fallback))
        result = []
        for candidate in candidates:
            if id(candidate) in consumed_webtoon:
                continue
            result.append(replacements.get(id(candidate), candidate))
        return result

    @staticmethod
    def _combine_kakao_candidates(candidates):
        """Combine matching KakaoPage and Kakao Webtoon results safely.

        KakaoPage remains the primary catalogue. A Kakao Webtoon result fills
        only missing fields and contributes its link. Same-title works with
        conflicting known authors remain separate candidates.
        """
        page_rows = [row for row in candidates if row.get('source') == 'kakaopage']
        webtoon_rows = [row for row in candidates if row.get('source') == 'kakao_webtoon']
        if not page_rows or not webtoon_rows:
            return candidates
        replacements = {}
        consumed_webtoon = set()
        for primary in page_rows:
            primary_author = _metadata_candidate_author(primary)
            fallback = next((row for row in webtoon_rows
                if id(row) not in consumed_webtoon
                and _metadata_title_matches(primary.get('title'), row.get('title'))
                and (
                    not primary_author
                    or not _metadata_candidate_author(row)
                    or _metadata_author_matches(primary_author, _metadata_candidate_author(row))
                )), None)
            if not fallback:
                continue
            primary_values = _metadata_clean(primary.get('metadata') or primary)
            fallback_values = _metadata_clean(fallback.get('metadata') or fallback)
            merged = dict(fallback_values)
            merged.update({key: value for key, value in primary_values.items() if value})
            merged['link'] = _join_links(
                primary.get('url'), primary_values.get('link'),
                fallback.get('url'), fallback_values.get('link'))
            combined = dict(primary)
            combined.update({key: value for key, value in merged.items() if value})
            combined.update({
                'source': 'kakaopage',
                'source_label': '카카오페이지 + 카카오웹툰',
                'title': primary.get('title'),
                'url': primary.get('url'),
                'metadata': merged,
                '_metadata_fetched': True,
                '_combined_sources': ['kakaopage', 'kakao_webtoon'],
            })
            replacements[id(primary)] = combined
            consumed_webtoon.add(id(fallback))
        result = []
        for candidate in candidates:
            if id(candidate) in consumed_webtoon:
                continue
            result.append(replacements.get(id(candidate), candidate))
        return result

    @staticmethod
    def _select_auto_candidates(candidates, query, content_kind, book_type,
                                source_order, author_hint='', diagnostics=None):
        """Select exact works before merging fields from configured sources.

        Manual search intentionally returns several candidates so an admin can
        choose one.  Automatic collection must be stricter: merging every
        result from a search page used to combine unrelated works and all
        Ridi/Naver editions.  Keep at most one exact remote work per source;
        Series.db may contribute multiple exact rows because it can contain
        separate records for the same work and their links are deduplicated
        later.  For book folders using ``series [author]``, a provider author
        match is preferred and known non-matching author results are rejected.
        """
        selected = []
        identities = []
        author_candidates = []
        ordered_sources = list(source_order or METADATA_SOURCES)
        by_source = {}
        for candidate in candidates or []:
            source = str(candidate.get('source') or '').strip().casefold()
            if source:
                by_source.setdefault(source, []).append(candidate)

        for source in ordered_sources:
            exact = [
                candidate for candidate in by_source.get(source, [])
                if any(
                    _metadata_title_matches(query, title)
                    for title in (
                        candidate.get('_match_titles')
                        or [
                            candidate.get('title')
                            or (candidate.get('metadata') or {}).get('title')
                            or candidate.get('original_title')
                        ]
                    )
                ) and _metadata_source_variant_allowed(candidate, content_kind, book_type)
                and not _metadata_variant_excluded(
                    candidate.get('title') or (candidate.get('metadata') or {}).get('title'))
            ]
            if not exact:
                continue
            if source in ('ridi', 'naver', 'kyobo'):
                hydrated = []
                for candidate in exact:
                    candidate = dict(candidate)
                    if candidate.get('url') and not candidate.get('_metadata_fetched'):
                        detail = _remote_fetch_metadata(candidate['url'], source)
                        search_author = _metadata_candidate_author(candidate)
                        detail_author = str(detail.get('author') or '').strip()
                        if (detail_author and search_author
                                and re.search(r'외\s*\d+\s*명', detail_author)
                                and len(_metadata_author_parts(search_author)) > 1
                                and _metadata_author_matches(
                                    _metadata_author_parts(detail_author)[0], search_author)):
                            # Some Ridi detail pages collapse several credited
                            # creators to "A 외 N명" even though the search
                            # result has their full names. Keep those names
                            # for both identity checks and saved metadata.
                            detail['author'] = search_author
                        if source == 'ridi' and '웹소설' in str(
                                (candidate.get('metadata') or {}).get('genre') or ''):
                            # Search-card categories identify the precise
                            # edition. A detail page can expose the adjacent
                            # e-book category instead of the serial one.
                            detail['genre'] = (candidate.get('metadata') or {}).get('genre')
                        candidate['metadata'] = {**(candidate.get('metadata') or candidate),
                                                 **{k: v for k, v in detail.items() if v}}
                        candidate['_metadata_fetched'] = True
                        if detail.get('author'):
                            candidate['author'] = detail['author']
                    hydrated.append(candidate)
                exact = hydrated
            if author_hint:
                with_author = [
                    candidate for candidate in exact
                    if _metadata_candidate_author(candidate)
                ]
                author_exact = [
                    candidate for candidate in with_author
                    if _metadata_author_matches(
                        author_hint, _metadata_candidate_author(candidate))
                ]
                if author_exact:
                    exact = author_exact
                elif with_author and len(with_author) == len(exact):
                    # The provider gave an author for every exact title, but
                    # none agrees with the folder hint. Do not auto-apply a
                    # same-title work by another author.
                    continue
                elif source != 'series_db':
                    # An author hint requires a verified author, not title alone.
                    continue
            if source != 'series_db':
                for candidate in exact:
                    author = _metadata_candidate_author(candidate)
                    if author:
                        identities.append(author)
                        author_candidates.append({
                            'source': source,
                            'title': str(candidate.get('title') or '').strip(),
                            'author': author,
                        })
            if source == 'series_db':
                selected.extend(exact)
                continue
            exact.sort(
                key=lambda candidate: _metadata_source_variant_score(
                    candidate, content_kind, book_type),
                reverse=True,
            )
            verified = [candidate for candidate in exact if _metadata_candidate_author(candidate)]
            if verified:
                selected.append(verified[0])
        if not author_hint and any(not _metadata_author_matches(a, b)
                                   for i, a in enumerate(identities) for b in identities[i + 1:]):
            if diagnostics is not None:
                diagnostics.append({
                    'reason': 'author_conflict',
                    'query': query,
                    'candidates': list({(item['source'], item['title'], item['author']): item
                                        for item in author_candidates}.values())[:12],
                })
            print('[RabbitPlugins-Metadata] 동명 작품의 작가가 달라 자동 적용 보류: 수동 선택 필요')
            return []
        return selected

    def _merge_metadata_candidates(self, candidates, content_kind=''):
        """Use source order as priority while filling fields absent in earlier results."""
        if not candidates:
            return None
        merged = {}
        links = []
        cover_sources = []
        cover_by_volume = {}
        cover_by_title = {}
        cover_maps_by_source = {}
        genre_source = ''
        for candidate in candidates:
            values = candidate.get('metadata') or candidate
            source = str(candidate.get('source') or '').strip().casefold()
            if source == 'yes24' and not values.get('summary') and not merged.get('summary'):
                values = dict(values)
                values['summary'] = _yes24_public_summary(
                    _yes24_candidate_item_id(candidate))
            if candidate.get('source') in ('ridi', 'naver', 'kyobo') and candidate.get('url'):
                fetched = (
                    values if candidate.get('_metadata_fetched')
                    else _remote_fetch_metadata(candidate['url'], candidate['source'])
                )
                # Some remote pages expose only the Korean title.  Fill the
                # original title and Series.db links when a strict local
                # identity can be established from total volume and credits.
                identity = self._series_db_remote_identity(
                    candidate, fetched, content_kind)
                if identity.get('localized_series'):
                    fetched['localized_series'] = identity['localized_series']
                if identity.get('link'):
                    fetched['link'] = _join_links(identity['link'], fetched.get('link'))
                values = {**values, **fetched}
            for key, destination in (
                ('cover_by_volume', cover_by_volume),
                ('cover_by_title', cover_by_title),
            ):
                mapping = values.get(key) or candidate.get(key)
                if isinstance(mapping, dict):
                    clean_mapping = {
                        str(map_key): str(map_value).strip()
                        for map_key, map_value in mapping.items()
                        if str(map_key).strip() and str(map_value).strip()
                    }
                    destination.update(clean_mapping)
                    source_maps = cover_maps_by_source.setdefault(
                        source, {'cover_by_volume': {}, 'cover_by_title': {}})
                    source_maps[key].update(clean_mapping)
            cleaned = _metadata_clean(values)
            if cleaned.get('cover') and source in METADATA_SOURCES:
                if source not in cover_sources:
                    cover_sources.append(source)
                if source in ('ridi', 'naver'):
                    volume = _volume_number(candidate.get('title') or cleaned.get('title'))
                    if volume is not None:
                        cover_by_volume[_volume_key(volume)] = cleaned['cover']
                        cover_maps_by_source.setdefault(
                            source, {'cover_by_volume': {}, 'cover_by_title': {}}
                        )['cover_by_volume'][_volume_key(volume)] = cleaned['cover']
                    title_key = _title_key(candidate.get('title') or cleaned.get('title'))
                    if title_key:
                        cover_by_title[title_key] = cleaned['cover']
                        cover_maps_by_source.setdefault(
                            source, {'cover_by_volume': {}, 'cover_by_title': {}}
                        )['cover_by_title'][title_key] = cleaned['cover']
            links.extend(_join_links(candidate.get('link'), candidate.get('url'), cleaned.get('link')).split(', '))
            for key, value in cleaned.items():
                if key == 'link':
                    continue
                if value and not merged.get(key):
                    merged[key] = value
                    if key == 'genre':
                        genre_source = source
        link_value = _join_links(links)
        if link_value:
            merged['link'] = link_value
        result = dict(candidates[0])
        result['source'] = 'merged'
        result['genre_source'] = genre_source
        result['metadata'] = merged
        kind = str(content_kind or '').strip().casefold()
        if kind == 'manhwa' and 'series_db' in cover_sources:
            result['cover_source'] = 'series_db'
            merged['cover'] = next((
                (candidate.get('metadata') or candidate).get('cover') for candidate in candidates
                if candidate.get('source') == 'series_db' and (candidate.get('metadata') or candidate).get('cover')
            ), merged.get('cover', ''))
        else:
            remote_cover = next(
                (source for source in cover_sources if source in ('ridi', 'naver')),
                '',
            )
            result['cover_source'] = remote_cover or (cover_sources[0] if cover_sources else '')
        if cover_by_volume:
            result['cover_by_volume'] = cover_by_volume
        if cover_by_title:
            result['cover_by_title'] = cover_by_title
        if cover_maps_by_source:
            result['cover_maps_by_source'] = cover_maps_by_source
        return result

    def _series_db_search(self, query, content_kind, limit=8):
        path = self._series_db_path()
        if not path.is_file():
            return []
        clean_query = re.sub(r'\s*\[[^\[\]]+\]\s*$', '', query).strip()
        pattern = '%' + query.replace('%', '%%') + '%'
        clean_pattern = '%' + clean_query.replace('%', '%%') + '%'
        key = _series_title_key(query)
        rows = []
        try:
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=12) as db:
                db.row_factory = sqlite3.Row
                rows = db.execute(
                    'SELECT id, title, native_title, secondary_titles_ko, titles, type, links, authors, artists, '
                    'cover_raw_url, status, final_volume, content_rating FROM series '
                    'WHERE title LIKE ? OR native_title LIKE ? OR secondary_titles_ko LIKE ? OR titles LIKE ? '
                    'ORDER BY id LIMIT 80', (clean_pattern, clean_pattern, clean_pattern, clean_pattern)).fetchall()
        except (OSError, sqlite3.Error):
            return []
        candidates = []
        for row in rows:
            if not _series_type_matches_library(row['type'], content_kind):
                continue
            titles = [str(row['title'] or '').strip(), str(row['native_title'] or '').strip()]
            titles.extend(_korean_titles(row['titles'], row['secondary_titles_ko']))
            titles = list(dict.fromkeys(item for item in titles if item))
            korean_titles = _korean_titles(row['titles'], row['secondary_titles_ko'])
            score = 0 if any(_series_title_key(item) == key for item in titles) else 1
            links = _json_names(row['links'])
            # Series.db supplies only the original title, links and cover.
            # Author/artist and other fields come from the selected external source.
            metadata = _metadata_clean({
                'localized_series': row['native_title'],
                'cover': row['cover_raw_url'], 'link': links,
            })
            candidates.append({
                'id': f"series_db:{row['id']}",
                'title': korean_titles[0] if korean_titles else (row['title'] or row['native_title']),
                'original_title': row['native_title'] or '', 'author': metadata.get('author', ''),
                'publisher': '', 'cover': metadata.get('cover', ''), 'url': metadata.get('link', ''),
                'link': metadata.get('link', ''), 'source': 'series_db',
                'source_label': METADATA_SOURCE_LABELS['series_db'], 'metadata': metadata,
                # Series.db author credits are used only to disambiguate a
                # book folder's ``series [author]`` hint.  The external
                # provider remains responsible for the saved author field.
                '_match_author': _join_terms(
                    _json_names(row['authors']) or row['authors']),
                '_match_titles': titles,
                '_score': score,
            })
        candidates.sort(key=lambda item: (item.get('_score', 1), str(item.get('title') or '')))
        for item in candidates:
            item.pop('_score', None)
        return candidates[:limit]

    def _series_db_remote_identity(self, candidate, metadata, content_kind):
        """Recover a Series.db identity when the provider has no Korean alias.

        Some Series.db rows contain only an English/native title.  A Korean
        provider result therefore cannot find them by title alone.  Use the
        provider's total-volume marker and the Latin author credit in its
        description as a strict secondary key, and only accept one row.
        """
        if not self._series_db_path().is_file():
            return {}
        candidate_title = str((candidate or {}).get('title') or '').strip()
        volume_match = re.search(r'총\s*(\d+)\s*권', candidate_title, re.IGNORECASE)
        final_volume = None
        if volume_match:
            try:
                final_volume = int(volume_match.group(1))
            except (TypeError, ValueError):
                final_volume = None

        credits = []
        for value in ((metadata or {}).get('author'), (metadata or {}).get('cover_artist')):
            credits.extend(re.split(r',|&|\band\b', str(value or ''), flags=re.IGNORECASE))
        summary = str((metadata or {}).get('summary') or '')
        for credit in re.findall(
            r'(?:©|작가\s*[:：])\s*([^©\r\n]+)', summary, re.IGNORECASE):
            credit = credit.split('/', 1)[0].split('...', 1)[0]
            for name in re.split(r',|&|\band\b', credit, flags=re.IGNORECASE):
                name = re.sub(r'\s+', ' ', name).strip(' .')
                if re.search(r'[A-Za-z]', name) and len(name) >= 3:
                    credits.append(name)
        credits = list(dict.fromkeys(credits))
        credits = [name for name in credits if re.search(r'[A-Za-z]', name) and len(name.strip()) >= 3]
        if not credits:
            return {}

        kind = str(content_kind or '').strip().casefold()
        allowed_types = SERIES_TYPES_BY_LIBRARY.get(kind) or {'manga', 'manhwa', 'manhua', 'oel'}
        placeholders = ','.join('?' for _ in allowed_types)
        credit_patterns = []
        for name in credits:
            name = str(name).strip()
            # Provider credits sometimes omit the space in names such as
            # ``NUMBER8`` while Series.db stores ``NUMBER 8``.
            spaced = re.sub(r'([A-Za-z])([0-9])', r'\1%\2', name)
            credit_patterns.extend([name, spaced] if spaced != name else [name])
        credit_patterns = list(dict.fromkeys(credit_patterns))
        where = ' OR '.join('authors LIKE ?' for _ in credit_patterns)
        volume_values = []
        if final_volume is not None:
            volume_values = list(dict.fromkeys(
                value for value in (final_volume, final_volume - 1, final_volume + 1)
                if value > 0
            ))
        volume_clause = ''
        volume_params = []
        if volume_values:
            volume_clause = 'AND CAST(final_volume AS INTEGER) IN (' + ','.join('?' for _ in volume_values) + ') '
            volume_params = volume_values
        path = self._series_db_path()
        try:
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=12) as db:
                db.row_factory = sqlite3.Row
                rows = db.execute(
                    'SELECT id, native_title, links, cover_raw_url, authors, final_volume '
                    f'FROM series WHERE type IN ({placeholders}) ' + volume_clause +
                    'AND (' + where + ')',
                    [*sorted(allowed_types), *volume_params, *[f'%{name}%' for name in credit_patterns]]).fetchall()
        except (OSError, sqlite3.Error):
            return {}

        matched = []
        credit_keys = list(dict.fromkeys(
            re.sub(r'[^a-z0-9]+', '', name.casefold()) for name in credits
        ))
        for row in rows:
            authors = _json_names(row['authors'])
            author_keys = [re.sub(r'[^a-z0-9]+', '', name.casefold()) for name in authors]
            overlap = sum(
                1 for key in credit_keys
                if key and any(key in author or author in key for author in author_keys)
            )
            required = 2 if len(credit_keys) >= 2 else 1
            if overlap >= required:
                matched.append((overlap, row))
        if not matched:
            return {}

        if final_volume is not None:
            distances = [
                (abs(int(row['final_volume']) - final_volume), overlap, row)
                for overlap, row in matched
                if str(row['final_volume'] or '').strip().isdigit()
            ]
            if not distances:
                return {}
            best_distance = min(item[0] for item in distances)
            matched = [(overlap, row) for distance, overlap, row in distances if distance == best_distance]
        if len(matched) != 1:
            return {}
        row = matched[0][1]
        result = _metadata_clean({
            'localized_series': row['native_title'],
            'link': _json_names(row['links']),
        })
        return {
            key: value for key, value in result.items()
            if key in {'localized_series', 'link'} and value
        }

    def _manual_series_db_metadata(self, query, content_kind):
        """Fill the local original title and links for a manual remote match."""
        candidates = self._series_db_search(query, content_kind, limit=80)
        exact = []
        for candidate in candidates:
            titles = candidate.get('_match_titles') or [
                candidate.get('title'), candidate.get('original_title')]
            if any(_metadata_title_matches(query, title) for title in titles):
                exact.append(candidate)
        if not exact:
            return {}
        localized = ''
        links = []
        for candidate in exact:
            values = _metadata_clean(candidate.get('metadata') or candidate)
            localized = localized or values.get('localized_series', '')
            links.append(values.get('link', ''))
        result = {}
        if localized:
            result['localized_series'] = localized
        link = _join_links(*links)
        if link:
            result['link'] = link
        return result

    def _apply_metadata(self, gateway, book_id, item_data, config, fields=None, manual=False):
        localized_sql = _optional_column_sql(gateway, 'books', 'b', 'localized_series')
        artist_sql = _optional_column_sql(gateway, 'books', 'b', 'cover_artist')
        cover_updated_sql = _optional_column_sql(gateway, 'books', 'b', 'cover_updated_at')
        volume_sql = _optional_column_sql(gateway, 'books', 'b', 'document_volume_index')
        target = gateway.fetch_one(
            'SELECT b.id, b.series_name, b.library_id, b.file_path, b.cover_image, b.metadata_locked, b.author, b.isbn, '
            'b.publisher, b.summary, b.link, b.genre, b.tags, b.release_date, b.title_alias, '
            + localized_sql + ' AS localized_series, ' + artist_sql + ' AS cover_artist '
            'FROM books b WHERE b.id = ? AND COALESCE(b.is_deleted, 0) = 0',
            (book_id,))
        if not target:
            return False, '도서를 찾을 수 없습니다.'
        library_kind_sql = _optional_column_sql(gateway, 'libraries', 'l', 'content_kind')
        library_row = gateway.fetch_one(
            'SELECT ' + library_kind_sql + ' AS content_kind FROM libraries l WHERE l.id = ?',
            (target['library_id'],)) or {}
        content_kind = library_row.get('content_kind') or 'unspecified'
        item_source = str(item_data.get('source') or '').strip().casefold()
        raw_metadata = dict(item_data.get('metadata') or item_data)
        remote_cover_by_volume = {}
        remote_cover_by_title = {}
        local_metadata = {}
        if manual and item_source in METADATA_SOURCES and item_source != 'series_db':
            # A remote result can identify the work, but Series.db is the
            # local authority for the original title and its stored links.
            # Combine those values before the selected provider is fetched so
            # they survive the normal field-priority and cleaning steps.
            local_metadata = self._manual_series_db_metadata(
                target.get('series_name') or '', content_kind)
            if local_metadata.get('localized_series'):
                raw_metadata['localized_series'] = local_metadata['localized_series']
            if local_metadata.get('link'):
                raw_metadata['link'] = _join_links(
                    local_metadata.get('link'), raw_metadata.get('link'), item_data.get('url'))
        metadata = _metadata_clean(raw_metadata)
        if item_source == 'yes24' and not metadata.get('summary'):
            summary = _yes24_public_summary(_yes24_candidate_item_id(item_data))
            if summary:
                metadata['summary'] = summary
        if item_source == 'series_db':
            # A manual Series.db result may still contain legacy top-level fields
            # from a cached browser response. Never use those to rename a series.
            metadata = {
                key: value for key, value in metadata.items()
                if key in {'localized_series', 'link', 'cover'}
            }
        if item_data.get('url') and item_data.get('source') in ('ridi', 'naver', 'kyobo'):
            fetched = _remote_fetch_metadata(item_data.get('url'), item_data.get('source'))
            if item_source == 'ridi':
                if isinstance(fetched.get('cover_by_volume'), dict):
                    remote_cover_by_volume.update(fetched['cover_by_volume'])
                if isinstance(fetched.get('cover_by_title'), dict):
                    remote_cover_by_title.update(fetched['cover_by_title'])
            identity = self._series_db_remote_identity(
                item_data, fetched, content_kind)
            if identity.get('localized_series'):
                fetched['localized_series'] = identity['localized_series']
            if identity.get('link'):
                fetched['link'] = _join_links(identity['link'], fetched.get('link'))
            if item_source == 'kyobo':
                metadata.update({key: value for key, value in fetched.items() if value})
            fetched.update(metadata)
            if identity.get('localized_series'):
                fetched['localized_series'] = identity['localized_series']
            if identity.get('link'):
                fetched['link'] = _join_links(identity['link'], fetched.get('link'))
            metadata = fetched
            metadata = _metadata_clean(metadata)
        metadata = _metadata_apply_conversions(metadata, config)
        metadata = _metadata_clean(metadata)
        genre_rules = _metadata_value_map(config.get('metadata_genre_map'))
        requested_fields = fields or item_data.get('fields')
        selected = set(_metadata_field_selection(config, requested_fields))
        if manual and not requested_fields:
            # Manual application is an explicit administrator action, so keep
            # the provider introduction available unless the caller supplied
            # an explicit field list.
            selected.add('summary')
        if manual and local_metadata:
            # Local identity fields are part of a manual match even when the
            # optional automatic-field list omitted them.
            selected.update({'localized_series', 'link'})
        overwrite = (
            _metadata_overwrite_enabled(config, content_kind)
            or (manual and _metadata_manual_overwrite_enabled(config))
        )
        cover_overwrite = _metadata_cover_overwrite_enabled(config, content_kind, overwrite)
        values = {}
        mapping = {
            'author': 'author', 'publisher': 'publisher', 'summary': 'summary', 'genre': 'genre',
            'tags': 'tags', 'release_date': 'release_date', 'isbn': 'isbn', 'link': 'link',
        }
        for source, column in mapping.items():
            value = str(metadata.get(source) or '').strip()
            if source not in selected or not value:
                continue
            if source == 'link':
                raw_existing = str(target.get(column) or '').strip()
                existing = _join_links(raw_existing)
                merged_link = _join_links(value) if overwrite else _replace_provider_links(existing, value)
                if merged_link and merged_link != raw_existing:
                    values[column] = merged_link
                continue
            existing_value = str(target.get(column) or '').strip()
            if source == 'author' and 'cover_artist' in selected:
                cleaned_author = (
                    _metadata_remove_terms(value, metadata.get('cover_artist'))
                    if overwrite else
                    _metadata_author_cleanup(
                        existing_value, value, metadata.get('cover_artist'))
                )
                if cleaned_author and cleaned_author != existing_value:
                    values[column] = cleaned_author
                    continue
            if source == 'genre' and existing_value:
                # A provider can add a parent category such as ``만화 e북``
                # without discarding a locally stored genre such as ``성인``.
                # Keep that additive behavior unless explicit automatic or
                # manual overwrite is enabled.
                replacement = _merge_converted_genres(
                    value, existing_value, genre_rules, overwrite)
                if replacement != existing_value:
                    values[column] = replacement
                continue
            if source == 'tags' and existing_value and metadata.get('genre'):
                # Older scans could save a provider category in Tags. Once
                # the same value is known as a Genre, remove that stale
                # duplicate and add the provider's actual keywords while
                # preserving unrelated local tags.
                cleaned_tags = _metadata_remove_terms(existing_value, metadata.get('genre'))
                if overwrite:
                    if value != existing_value:
                        values[column] = value
                else:
                    merged_tags = _join_terms([cleaned_tags, value])
                    if merged_tags != existing_value:
                        values[column] = merged_tags
                continue
            if overwrite or not existing_value or (
                column == 'summary' and _summary_should_refresh(existing_value, value)
            ):
                values[column] = value
        optional = {
            'localized_series': _optional_column_sql(gateway, 'books', 'b', 'localized_series') != 'NULL',
            'cover_artist': _optional_column_sql(gateway, 'books', 'b', 'cover_artist') != 'NULL',
        }
        if 'localized_series' in selected and optional['localized_series']:
            value = str(metadata.get('localized_series') or '').strip()
            if value and (overwrite or not str(target.get('localized_series') or '').strip()):
                values['localized_series'] = value
        if 'cover_artist' in selected and optional['cover_artist']:
            value = str(metadata.get('cover_artist') or '').strip()
            existing_artist = str(target.get('cover_artist') or '').strip()
            refreshed_artist = _metadata_artist_refresh(existing_artist, value)
            if value and (overwrite or not existing_artist or refreshed_artist):
                values['cover_artist'] = value

        if 'title' in selected and metadata.get('title'):
            raw_title_alias = str(target.get('title_alias') or '').strip()
            clean_title_alias = _metadata_title(raw_title_alias)
            # The provider title is a series title.  It must not replace the
            # per-file title/alias, because Comic Book Butler keeps the
            # filename-derived volume title (for example, ``01권 (한정판)``).
            # Clean an old description-suffixed alias when one is present.
            if raw_title_alias and clean_title_alias != raw_title_alias:
                values['title_alias'] = clean_title_alias
        if values:
            sets = ', '.join(f'{column} = ?' for column in values)
            params = list(values.values())
            gateway.execute(f'UPDATE books SET {sets} WHERE id = ?', (*params, book_id))

        # Series metadata is shared by all volumes, but locked rows are left intact.
        series_rows = gateway.fetch_all(
            'SELECT id, title, title_alias, file_path, cover_image, metadata_locked, link, ' + volume_sql + ' AS volume_index '
            'FROM books b WHERE series_name = ? AND library_id = ? '
            'AND COALESCE(is_deleted, 0) = 0 ORDER BY id', (target['series_name'], target['library_id']))
        series_link = ''
        if 'link' in selected:
            series_link = (
                _join_links(metadata.get('link'))
                if overwrite else _replace_provider_links(
                    _join_links(*(row.get('link') for row in series_rows)),
                    metadata.get('link'))
            )
            raw_target_link = str(target.get('link') or '').strip()
            if series_link and series_link != raw_target_link:
                values['link'] = series_link
        updated_ids = {book_id} if values else set()
        if series_rows and len(series_rows) > 1:
            # ``series_rows`` is ordered by database id, while the selected
            # target can be any volume (automatic collection usually starts
            # with the newest row).  Skip only that target so earlier volumes
            # receive the same metadata as later ones.
            for row in series_rows:
                if row.get('id') == target.get('id'):
                    continue
                if int(row.get('metadata_locked') or 0) == 1 and not overwrite:
                    continue
                if not values:
                    row_values = gateway.fetch_one(
                        'SELECT author, isbn, publisher, summary, link, genre, tags, release_date, title_alias, '
                        + localized_sql.replace('b.', '') + ' AS localized_series, '
                        + artist_sql.replace('b.', '') + ' AS cover_artist FROM books b WHERE b.id = ?',
                        (row['id'],)) or {}
                    row_changes = {
                        column: value for column, value in metadata.items()
                        if column in mapping and column in selected and value and
                        (column == 'link' or overwrite or not str(row_values.get(mapping[column]) or '').strip()
                         or (column == 'summary' and _summary_should_refresh(
                             row_values.get(mapping[column]), value)))
                    }
                    if 'link' in selected and metadata.get('link'):
                        raw_existing = str(row_values.get('link') or '').strip()
                        merged_link = series_link or _join_links(raw_existing, metadata.get('link'))
                        if merged_link and merged_link != raw_existing:
                            row_changes['link'] = merged_link
                    if 'genre' in selected and metadata.get('genre'):
                        raw_existing = str(row_values.get('genre') or '').strip()
                        replacement = _merge_converted_genres(
                            metadata['genre'], raw_existing, genre_rules, overwrite)
                        if replacement and replacement != raw_existing:
                            row_changes['genre'] = replacement
                    if 'tags' in selected and metadata.get('genre'):
                        raw_existing = str(row_values.get('tags') or '').strip()
                        cleaned_tags = _metadata_remove_terms(raw_existing, metadata.get('genre'))
                        if cleaned_tags != raw_existing:
                            row_changes['tags'] = cleaned_tags
                    if 'author' in selected and 'cover_artist' in selected:
                        cleaned_author = (
                            _metadata_remove_terms(metadata.get('author'), metadata.get('cover_artist'))
                            if overwrite else
                            _metadata_author_cleanup(
                                row_values.get('author'), metadata.get('author'), metadata.get('cover_artist'))
                        )
                        if cleaned_author and cleaned_author != str(row_values.get('author') or '').strip():
                            row_changes['author'] = cleaned_author
                    if 'title' in selected and metadata.get('title'):
                        raw_title_alias = str(row_values.get('title_alias') or '').strip()
                        clean_title_alias = _metadata_title(raw_title_alias)
                        if raw_title_alias and clean_title_alias != raw_title_alias:
                            row_changes['title_alias'] = clean_title_alias
                    if 'localized_series' in selected and optional['localized_series'] and metadata.get('localized_series') and (overwrite or not str(row_values.get('localized_series') or '').strip()):
                        row_changes['localized_series'] = metadata['localized_series']
                    if 'cover_artist' in selected and optional['cover_artist'] and metadata.get('cover_artist'):
                        existing_artist = str(row_values.get('cover_artist') or '').strip()
                        if overwrite or not existing_artist or _metadata_artist_refresh(existing_artist, metadata['cover_artist']):
                            row_changes['cover_artist'] = metadata['cover_artist']
                else:
                    row_values = gateway.fetch_one(
                        'SELECT author, isbn, publisher, summary, link, genre, tags, release_date, title_alias, '
                        + localized_sql.replace('b.', '') + ' AS localized_series, '
                        + artist_sql.replace('b.', '') + ' AS cover_artist FROM books b WHERE b.id = ?',
                        (row['id'],)) or {}
                    row_changes = {
                        column: value for column, value in values.items()
                        if column == 'link' or overwrite or not str(row_values.get(column) or '').strip()
                        or (column == 'summary' and _summary_should_refresh(
                            row_values.get(column), value))
                    }
                    if series_link and series_link != str(row_values.get('link') or '').strip():
                        row_changes['link'] = series_link
                    if 'genre' in selected and metadata.get('genre'):
                        raw_existing = str(row_values.get('genre') or '').strip()
                        replacement = _merge_converted_genres(
                            metadata['genre'], raw_existing, genre_rules, overwrite)
                        if replacement and replacement != raw_existing:
                            row_changes['genre'] = replacement
                    if 'tags' in selected and metadata.get('genre'):
                        raw_existing = str(row_values.get('tags') or '').strip()
                        cleaned_tags = _metadata_remove_terms(raw_existing, metadata.get('genre'))
                        if cleaned_tags != raw_existing:
                            row_changes['tags'] = cleaned_tags
                    if 'author' in selected and 'cover_artist' in selected:
                        cleaned_author = (
                            _metadata_remove_terms(metadata.get('author'), metadata.get('cover_artist'))
                            if overwrite else
                            _metadata_author_cleanup(
                                row_values.get('author'), metadata.get('author'), metadata.get('cover_artist'))
                        )
                        if cleaned_author and cleaned_author != str(row_values.get('author') or '').strip():
                            row_changes['author'] = cleaned_author
                    if 'title' in selected and metadata.get('title'):
                        raw_title_alias = str(row_values.get('title_alias') or '').strip()
                        clean_title_alias = _metadata_title(raw_title_alias)
                        if raw_title_alias and clean_title_alias != raw_title_alias:
                            row_changes['title_alias'] = clean_title_alias
                    if 'cover_artist' in selected and optional['cover_artist'] and metadata.get('cover_artist'):
                        existing_artist = str(row_values.get('cover_artist') or '').strip()
                        if overwrite or not existing_artist or _metadata_artist_refresh(existing_artist, metadata['cover_artist']):
                            row_changes['cover_artist'] = metadata['cover_artist']
                if row_changes:
                    row_sets = ', '.join(f'{column} = ?' for column in row_changes)
                    gateway.execute(f'UPDATE books SET {row_sets} WHERE id = ?', (*row_changes.values(), row['id']))
                    updated_ids.add(row['id'])

        date_key = _series_dates_key(target['library_id'], target['series_name'])
        if any(field in selected and metadata.get(field) for field in ('publication_start_date', 'publication_end_date')) or ('publication_status' in selected and metadata.get('publication_status')):
            try:
                saved_dates = _read_series_dates(gateway, date_key)
            except (ValueError, TypeError):
                saved_dates = {}
            for field, key in [('publication_start_date', 'start'), ('publication_end_date', 'end')]:
                if field in selected and metadata.get(field) and (overwrite or not saved_dates.get(key)):
                    saved_dates[key] = metadata[field]
            if 'publication_status' in selected and metadata.get('publication_status'):
                saved_dates['publication_status_label'] = metadata.get('publication_status_label', '')
            if 'publication_status' in selected and metadata.get('total_chapters'):
                saved_dates['total_chapters'] = metadata['total_chapters']
            if saved_dates.get('start') and saved_dates.get('end') and saved_dates['start'] > saved_dates['end']:
                return False, '연재종료일이 시작일보다 빠릅니다.'
            gateway.set_setting(date_key, json.dumps(saved_dates))

        for row in series_rows:
            if int(row.get('metadata_locked') or 0) == 1 and not overwrite:
                continue
            extra = {}
            if (item_data.get('genre_source') or item_source) == 'novelpia' and 'genre' in selected and 'tags' in selected:
                current = gateway.fetch_one('SELECT genre, tags FROM books WHERE id = ?', (row['id'],)) or {}
                terms = list(dict.fromkeys(re.split(r'[,;|\n]', _join_terms([current.get('genre'), current.get('tags'), metadata.get('genre'), metadata.get('tags')]))))
                terms = [term.strip() for term in terms if term.strip()]
                extra['genre'] = _join_terms([term for term in terms if term in NOVEL_GENRES])
                extra['tags'] = _join_terms([term for term in terms if term not in NOVEL_GENRES])
            for field in ('publication_status', 'books_lv'):
                if field not in selected or field not in metadata:
                    continue
                if _optional_column_sql(gateway, 'books', 'b', field) == 'NULL':
                    continue
                current = gateway.fetch_one(f'SELECT {field} FROM books WHERE id = ?', (row['id'],)) or {}
                existing = str(current.get(field) if current.get(field) is not None else '').strip()
                if overwrite or not existing or (field == 'publication_status' and existing in {'0', '1'}):
                    extra[field] = metadata[field]
            if any(field in selected and metadata.get(field) for field in ('publication_start_date', 'publication_end_date')):
                info = _comicinfo_metadata(row)
                volume = info.get('volume') or row.get('volume_index')
                count = info.get('count')
                dates = []
                if volume == 1 and 'publication_start_date' in selected and metadata.get('publication_start_date'):
                    dates.append(metadata['publication_start_date'])
                if count and volume == count and 'publication_end_date' in selected and metadata.get('publication_end_date'):
                    dates.append(metadata['publication_end_date'])
                # One release_date column cannot represent two distinct dates.
                if len(set(dates)) == 1:
                    current = gateway.fetch_one('SELECT release_date FROM books WHERE id = ?', (row['id'],)) or {}
                    if overwrite or not current.get('release_date'):
                        extra['release_date'] = dates[0]
            if extra:
                gateway.execute('UPDATE books SET ' + ', '.join(f'{key} = ?' for key in extra) + ' WHERE id = ?', (*extra.values(), row['id']))
                updated_ids.add(row['id'])

        if _metadata_cover_enabled(config, content_kind) and 'cover' in selected:
            try:
                from tools.scanner.cover import download_cover_from_url
                rows = series_rows or [target]
                webtoon = str(content_kind).casefold() == 'manhwa'
                cover_source = str(item_data.get('cover_source') or item_source).strip().casefold()
                if cover_source == 'merged':
                    cover_source = str(item_data.get('cover_source') or '').strip().casefold()
                cover_by_volume = item_data.get('cover_by_volume') or {}
                if not isinstance(cover_by_volume, dict):
                    cover_by_volume = {}
                cover_by_volume = {**remote_cover_by_volume, **cover_by_volume}
                cover_by_title = item_data.get('cover_by_title') or {}
                if not isinstance(cover_by_title, dict):
                    cover_by_title = {}
                cover_by_title = {**remote_cover_by_title, **cover_by_title}
                source_maps = item_data.get('cover_maps_by_source') or {}
                if isinstance(source_maps, dict) and isinstance(source_maps.get(cover_source), dict):
                    selected_maps = source_maps[cover_source]
                    source_volume_map = selected_maps.get('cover_by_volume') or {}
                    source_title_map = selected_maps.get('cover_by_title') or {}
                    if isinstance(source_volume_map, dict):
                        cover_by_volume = {**remote_cover_by_volume, **source_volume_map}
                    if isinstance(source_title_map, dict):
                        cover_by_title = {**remote_cover_by_title, **source_title_map}
                ridi_per_volume = _use_ridi_volume_covers(
                    cover_source, rows, cover_by_volume, cover_by_title)
                ridi_exact_single = _use_ridi_exact_single_cover(
                    cover_source, rows, metadata.get('cover'))
                eligible = [
                    row for row in rows
                    if _metadata_cover_eligible(
                        row, webtoon, cover_overwrite,
                        source=cover_source,
                        per_volume=(ridi_per_volume or ridi_exact_single))
                ]
                if webtoon and eligible:
                    query, _ = _metadata_book_folder_identity(target['series_name'], content_kind)
                    for candidate in self._series_db_search(query, content_kind, limit=80):
                        titles = candidate.get('_match_titles') or [candidate.get('title'), candidate.get('original_title')]
                        local_cover = (candidate.get('metadata') or candidate).get('cover')
                        if local_cover and any(_metadata_title_matches(query, title) for title in titles):
                            metadata['cover'] = local_cover
                            cover_source = 'series_db'
                            break

                # Series.db only has a representative image.  Webtoon rows share
                # one stored file so a long series does not duplicate the bytes.
                shared_cover = webtoon
                if shared_cover and eligible and metadata.get('cover'):
                    shared_key = f'rabbit_plugins:series-cover:{target["library_id"]}:{_series_title_key(target["series_name"])}'
                    cover_path = download_cover_from_url(
                        shared_key, metadata['cover'], force=True, library_id=target['library_id'])
                    if cover_path:
                        for row in eligible:
                            if cover_updated_sql != 'NULL':
                                gateway.execute(
                                    'UPDATE books SET cover_image = ?, cover_updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                                    (cover_path, row['id']),
                                )
                            else:
                                gateway.execute('UPDATE books SET cover_image = ? WHERE id = ?', (cover_path, row['id']))
                            if cover_overwrite:
                                _remember_external_cover(
                                    gateway, row['id'], cover_path,
                                    target['library_id'], content_kind)
                elif cover_source in ('ridi', 'naver'):
                    downloaded_covers = {}
                    # Ridi/Naver may expose a cover per volume.  Prefer a volume
                    # map from the search results, then the source link stored on
                    # each book.  An unmatched volume keeps its current cover.
                    for row in eligible:
                        cover_url = _row_mapped_cover(
                            row, cover_by_volume, cover_by_title)
                        if not cover_url and not ridi_per_volume:
                            link = _source_link(row.get('link'), cover_source)
                            if link:
                                cover_url = _remote_fetch_metadata(link, cover_source).get('cover', '')
                        if (not cover_url and not ridi_per_volume
                                and row.get('id') == target.get('id')):
                            cover_url = metadata.get('cover', '')
                        if not cover_url:
                            continue
                        cover_path = downloaded_covers.get(cover_url)
                        if not cover_path:
                            cover_key = f'rabbit_plugins:url-cover:{cover_url}'
                            cover_path = download_cover_from_url(
                                cover_key, cover_url, force=cover_overwrite,
                                library_id=target['library_id'])
                            if cover_path:
                                downloaded_covers[cover_url] = cover_path
                        if cover_path:
                            if cover_updated_sql != 'NULL':
                                gateway.execute(
                                    'UPDATE books SET cover_image = ?, cover_updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                                    (cover_path, row['id']),
                                )
                            else:
                                gateway.execute('UPDATE books SET cover_image = ? WHERE id = ?', (cover_path, row['id']))
                            if cover_overwrite:
                                _remember_external_cover(
                                    gateway, row['id'], cover_path,
                                    target['library_id'], content_kind)
                elif metadata.get('cover'):
                    for row in eligible:
                        cover_key = f'rabbit_plugins:url-cover:{metadata["cover"]}'
                        cover_path = download_cover_from_url(
                            cover_key, metadata['cover'],
                            force=cover_overwrite, library_id=target['library_id'])
                        if cover_path:
                            if cover_updated_sql != 'NULL':
                                gateway.execute(
                                    'UPDATE books SET cover_image = ?, cover_updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                                    (cover_path, row['id']),
                                )
                            else:
                                gateway.execute('UPDATE books SET cover_image = ? WHERE id = ?', (cover_path, row['id']))
                            if cover_overwrite:
                                _remember_external_cover(
                                    gateway, row['id'], cover_path,
                                    target['library_id'], content_kind)
            except Exception as error:
                print(f'[RabbitPlugins-Metadata] 표지 저장 실패: {error}')
        if manual:
            gateway.set_setting(_metadata_hold_key(target['library_id'], target['series_name']), '')
        return True, f'메타데이터 {len(updated_ids)}권에 적용했습니다.'

    def _refresh_newly_completed_series(self, gateway, db_type, payload, config, source_order,
                                        do_refresh=True):
        """Refresh only mutable completion fields when a new final file arrives.

        The core scan hook supplies a count and the first ten titles, not book
        IDs. Newly inserted books have the highest IDs in their library, so
        inspect exactly that many rows and require an older ongoing sibling.
        """
        try:
            new_count = int(payload.get('new_books_count') or 0)
            library_id = int(payload.get('library_id') or 0)
        except (TypeError, ValueError):
            return set(), 0
        if new_count < 1 or library_id < 1:
            return set(), 0
        new_rows = gateway.fetch_all(
            'SELECT id, series_name, title, file_path FROM books '
            'WHERE library_id = ? AND COALESCE(is_deleted, 0) = 0 '
            'ORDER BY id DESC LIMIT ?', (library_id, new_count))
        final_series = {
            str(row.get('series_name') or '').strip() for row in new_rows
            if re.search(r'\(\s*완결\s*\)|（\s*완결\s*）',
                         os.path.basename(str(row.get('file_path') or '').replace('\\', '/')),
                         re.IGNORECASE)
            and str(row.get('series_name') or '').strip()
        }
        if not final_series:
            return set(), 0

        library = gateway.fetch_one('SELECT content_kind FROM libraries WHERE id = ?',
                                    (library_id,)) or {}
        content_kind = str(library.get('content_kind') or 'manga')
        new_ids = {row['id'] for row in new_rows}
        handled = set()
        changed = 0
        for series in sorted(final_series):
            books = gateway.fetch_all(
                'SELECT id, title, file_path, publication_status, tags, metadata_locked '
                'FROM books WHERE library_id = ? AND series_name = ? '
                'AND COALESCE(is_deleted, 0) = 0 ORDER BY id',
                (library_id, series))
            marker_ids = [row['id'] for row in new_rows if row.get('series_name') == series]
            already_handled = any((db_type, library_id, marker_id) in _COMPLETION_SCAN_MARKERS
                                  for marker_id in marker_ids)
            ongoing_sibling = any(
                row['id'] not in new_ids
                and str(row.get('publication_status') or '').strip() == '0'
                for row in books)
            if not ongoing_sibling and not already_handled:
                continue
            handled.add(series)
            if not do_refresh or already_handled:
                continue
            if any(row['id'] not in new_ids
                   and str(row.get('publication_status') or '').strip() == '0'
                   and int(row.get('metadata_locked') or 0) == 1
                   for row in books):
                print(f'[RabbitPlugins-Metadata] 완결 갱신 보류, 기존 연재 도서 잠금: {series!r}')
                continue
            for marker_id in marker_ids:
                _COMPLETION_SCAN_MARKERS.append((db_type, library_id, marker_id))
            search_title, author_hint = _metadata_book_folder_identity(series, content_kind)
            book_type = _metadata_book_type(
                content_kind, books[0].get('title'),
                ' '.join(os.path.basename(str(row.get('file_path') or '').replace('\\', '/'))
                         for row in books))
            candidates = self._search_metadata(
                search_title, config, db_type, content_kind, book_type,
                author_hint=author_hint, refresh=True)
            selected = self._select_auto_candidates(
                candidates, search_title, content_kind, book_type, source_order,
                author_hint=author_hint)
            if not selected:
                continue
            metadata = _metadata_clean(
                (self._merge_metadata_candidates(selected, content_kind) or {}).get('metadata') or {})
            if metadata.get('publication_status') != '2':
                print(f'[RabbitPlugins-Metadata] 완결 파일 감지, 제공처 완결 미확인: {series!r}')
                continue
            end_date = metadata.get('publication_end_date') or ''
            incoming_tags = metadata.get('tags') or ''
            for row in books:
                if int(row.get('metadata_locked') or 0) == 1:
                    continue
                updates = {}
                if str(row.get('publication_status') or '').strip() != '2':
                    updates['publication_status'] = '2'
                if incoming_tags:
                    tags = _join_terms([row.get('tags'), incoming_tags])
                    if tags != str(row.get('tags') or '').strip():
                        updates['tags'] = tags
                if updates:
                    gateway.execute(
                        'UPDATE books SET ' + ', '.join(f'{field} = ?' for field in updates)
                        + ' WHERE id = ?', (*updates.values(), row['id']))
                    changed += 1
            if end_date:
                date_key = _series_dates_key(library_id, series)
                saved = _read_series_dates(gateway, date_key)
                if saved.get('end') != end_date:
                    saved['end'] = end_date
                    gateway.set_setting(date_key, json.dumps(saved, ensure_ascii=False))
                    changed += 1
            print(f'[RabbitPlugins-Metadata] 완결 갱신: series={series!r} '
                  f'end_date={end_date or "제공처 값 없음"} '
                  f'tags={bool(incoming_tags)}')
        return handled, changed

    def _auto_collect(self, db_type, payload):
        config = self.get_plugin_config(db_type, {}) or {}
        gateway = self.get_db_gateway(db_type)
        library_id = payload.get('library_id')
        source_order = _metadata_source_order(config)
        selected_fields = _metadata_field_selection(config, automatic=True)
        completion_series, completion_updated = self._refresh_newly_completed_series(
            gateway, db_type, payload, config, source_order,
            do_refresh=payload.get('_rabbit_allow_overwrite', True))
        localized_sql = _optional_column_sql(gateway, 'books', 'b', 'localized_series')
        artist_sql = _optional_column_sql(gateway, 'books', 'b', 'cover_artist')
        localized_condition = (
            f'COALESCE({localized_sql}, "") = "" OR '
            if localized_sql != 'NULL' else ''
        )
        artist_condition = (
            f'COALESCE({artist_sql}, "") = "" OR '
            if artist_sql != 'NULL' else ''
        )
        missing_condition = (
            'COALESCE(author, "") = "" OR COALESCE(summary, "") = "" OR '
            'COALESCE(isbn, "") = "" OR COALESCE(genre, "") = "" OR '
            'COALESCE(cover_image, "") = "" OR '
            + artist_condition + localized_condition +
            "INSTR(COALESCE(summary, ''), '작품소개') > 0 OR "
            "SUBSTR(RTRIM(COALESCE(summary, '')), -3) = '...' OR "
            "INSTR(COALESCE(title_alias, ''), '작품소개') > 0 OR "
            "(INSTR(COALESCE(link, ''), 'ridibooks.com/books/') > 0 "
            "AND INSTR(COALESCE(link, ''), CHAR(63)) > 0)"
        )
        overwrite_kinds = (
            _metadata_overwrite_kinds(config)
            if payload.get('_rabbit_allow_overwrite', True) else set()
        )
        target_conditions = [
            f'(COALESCE(b.metadata_locked, 0) = 0 AND ({missing_condition}))'
        ]
        query_params = [library_id] if library_id else []
        if overwrite_kinds is None:
            target_conditions.append('1 = 1')
        elif overwrite_kinds:
            placeholders = ','.join('?' for _ in sorted(overwrite_kinds))
            target_conditions.append(
                f'COALESCE(NULLIF(l.content_kind, ""), "unspecified") IN ({placeholders})')
            query_params.extend(sorted(overwrite_kinds))
        query = (
            'SELECT b.id, b.series_name, b.title, b.title_alias, b.file_path, b.library_id FROM books b '
            'LEFT JOIN libraries l ON l.id = b.library_id '
            'WHERE COALESCE(b.is_deleted, 0) = 0 '
            + ('AND b.library_id = ? ' if library_id else '') +
            'AND (' + ' OR '.join(target_conditions) + ') '
            'ORDER BY b.id DESC LIMIT 80'
        )
        rows = gateway.fetch_all(query, tuple(query_params))
        print(f'[RabbitPlugins-Metadata] runtime=metadata-v19-overwrite-internal pid={os.getpid()} 자동 수집 대상={len(rows)}권 '
              f'db={db_type} library_id={library_id or "all"} '
              f'sources={",".join(source_order)} fields={",".join(selected_fields)}')
        series_files = {}
        for item in rows:
            item_series = str(item.get('series_name') or item.get('title') or '').strip()
            item_key = (item_series.casefold(), item.get('library_id'))
            if item_series:
                series_files.setdefault(item_key, []).append(str(item.get('file_path') or ''))
        seen = set()
        processed = 0
        matched = 0
        updated = 0
        for row in rows:
            series = str(row.get('series_name') or row.get('title') or '').strip()
            key = (series.casefold(), row.get('library_id'))
            if not series or key in seen or series in completion_series:
                continue
            seen.add(key)
            processed += 1
            content_kind = 'manga'
            try:
                library = gateway.fetch_one('SELECT content_kind FROM libraries WHERE id = ?', (row['library_id'],))
                content_kind = str((library or {}).get('content_kind') or 'manga')
            except Exception:
                pass
            book_type = _metadata_book_type(
                content_kind,
                row.get('title'),
                ' '.join(
                    os.path.basename(str(path).replace('\\', '/'))
                    for path in series_files.get(key, [])
                ),
            )
            search_title, author_hint = _metadata_book_folder_identity(series, content_kind)
            ebook_code = next(
                (
                    code for code in (
                        _metadata_ebook_code(path)
                        for path in series_files.get(key, [])
                    ) if code
                ),
                _metadata_ebook_code(series),
            )
            candidates = self._search_metadata(
                search_title, config, db_type, content_kind, book_type,
                ebook_code=ebook_code, author_hint=author_hint,
                yes24_edition=_yes24_local_edition(series_files.get(key, ())))
            diagnostics = []
            selected_candidates = self._select_auto_candidates(
                candidates,
                search_title,
                content_kind,
                book_type,
                _metadata_source_order(config),
                author_hint=author_hint,
                diagnostics=diagnostics,
            )
            hold_key = _metadata_hold_key(row['library_id'], series)
            if diagnostics:
                hold = {**diagnostics[0], 'checked_at': datetime.now().isoformat(timespec='seconds')}
                gateway.set_setting(hold_key, json.dumps(hold, ensure_ascii=False))
            elif _read_metadata_hold(gateway, row['library_id'], series):
                gateway.set_setting(hold_key, '')
            if selected_candidates:
                matched += 1
                selected = self._merge_metadata_candidates(selected_candidates, content_kind)
                applied, message = self._apply_metadata(
                    gateway, row['id'], selected, config, fields=selected_fields)
                if applied:
                    updated += 1
                selected_sources = ','.join(
                    str(item.get('source') or '').strip() for item in selected_candidates)
                print(f'[RabbitPlugins-Metadata] series={series!r} query={search_title!r} '
                      f'author_hint={author_hint!r} ebook_code={ebook_code or "없음"} '
                      f'sources={selected_sources} '
                      f'applied={applied} message={message}')
            else:
                search_query = _metadata_search_query(search_title, config)
                print(f'[RabbitPlugins-Metadata] series={series!r} 결과 없음 '
                      f'query={search_query!r} author_hint={author_hint!r} '
                      f'ebook_code={ebook_code or "없음"} '
                      f'content_kind={content_kind!r} '
                      f'book_type={book_type!r}')
        return {
            'rows': len(rows), 'processed': processed,
            'matched': matched, 'updated': updated + completion_updated,
        }

    def _queue_auto_collect(self, db_type, payload):
        config = self.get_plugin_config(db_type, {}) or {}
        if not _config_bool(config.get('metadata_auto_enabled'), False):
            print(f'[RabbitPlugins-Metadata] 자동 수집 건너뜀 db={db_type}: '
                  '설정이 꺼져 있습니다.')
            return {'success': True, 'skipped': True, 'message': '자동 메타데이터 수집이 꺼져 있습니다.'}
        if db_type not in ('general', 'adult'):
            print(f'[RabbitPlugins-Metadata] 자동 수집 건너뜀 db={db_type}: '
                  '일반/성인 서재가 아닙니다.')
            return {'success': True, 'skipped': True, 'message': '도서 서재만 자동 수집합니다.'}
        app = None
        if has_request_context():
            try:
                from flask import current_app
                app = current_app._get_current_object()
            except Exception:
                app = None

        # The scanner already calls plugin hooks from its event worker.  The
        # previous implementation created a second daemon thread here, so a
        # container restart or worker teardown could silently discard the
        # actual collection after the hook had reported success.  Run the
        # bounded operation in this hook instead and serialize overlapping
        # new-book/completion events with one process lock.
        try:
            with _AUTO_COLLECT_LOCK:
                if app is not None:
                    with app.app_context():
                        stats = self._auto_collect(db_type, dict(payload or {}))
                else:
                    stats = self._auto_collect(db_type, dict(payload or {}))
            return {
                'success': True,
                'completed': True,
                'stats': stats,
                'message': '메타데이터 자동 수집을 완료했습니다.',
            }
        except Exception as error:
            print(f'[RabbitPlugins-Metadata] 자동 수집 실패 db={db_type}: '
                  f'{type(error).__name__}: {error}')
            return {'success': False, 'error': str(error), 'message': '메타데이터 자동 수집에 실패했습니다.'}

    def on_scan_new_books_detected(self, db_type, payload):
        event = dict(payload or {})
        # The completion hook performs the potentially broad overwrite pass.
        # This first hook only fills genuinely missing metadata, preventing
        # the same complete Kavita/ComicInfo series from being crawled twice
        # for one scan.
        event['_rabbit_allow_overwrite'] = False
        return self._queue_auto_collect(db_type, event)

    def on_scan_completed(self, db_type, payload):
        # Always run the completion pass, including scans that reported new
        # files.  The core emits the new-book and completion events on separate
        # workers; if the first event is delayed or fails, skipping here would
        # leave the newly scanned rows without any automatic collection. This
        # completion pass also applies explicit existing-value overwrite rules.
        event = dict(payload or {})
        event['_rabbit_allow_overwrite'] = True
        return self._queue_auto_collect(db_type, event)

    def run_context_menu_action(self, db_type, action_id, context):
        if action_id == 'optimize_series_db':
            if not has_request_context() or session.get('role') != 'admin':
                return {'success': False, 'error': '관리자만 Series.db를 최적화할 수 있습니다.'}
            if db_type != 'general':
                return {'success': False, 'error': '일반 도서 설정에서 실행해 주세요.'}
            result = self._optimize_series_db()
            result['database_path'] = str(self._series_db_path())
            return result
        if action_id == 'metadata_search':
            if not has_request_context() or session.get('role') != 'admin':
                return {'success': False, 'error': '관리자만 메타데이터를 검색할 수 있습니다.'}
            query = str(context.get('query') or context.get('series_name') or '').strip()
            config = self.get_plugin_config(db_type or 'general', {}) or {}
            sources = _config_list(context.get('sources'), METADATA_SOURCES)
            if 'sources' in context:
                config = dict(config)
                config['metadata_sources'] = sources
            content_kind = str(context.get('content_kind') or '').strip() or None
            book_type = str(context.get('book_type') or '').strip()
            ebook_code = _metadata_ebook_code(query)
            folder_author = ''
            if context.get('book_id'):
                try:
                    gateway = self.get_db_gateway(db_type or 'general')
                    target = gateway.fetch_one(
                        'SELECT b.title, b.series_name, b.file_path, b.library_id FROM books b '
                        'WHERE b.id = ?', (int(context.get('book_id')),)) or {}
                    if not content_kind and target.get('library_id'):
                        kind_sql = _optional_column_sql(gateway, 'libraries', 'l', 'content_kind')
                        library = gateway.fetch_one(
                            'SELECT ' + kind_sql + ' AS content_kind FROM libraries l WHERE l.id = ?',
                            (target['library_id'],)) or {}
                        content_kind = str(library.get('content_kind') or '').strip() or None
                    if not book_type:
                        book_type = _metadata_book_type(
                            content_kind, target.get('title'), target.get('file_path'))
                    ebook_code = ebook_code or _metadata_ebook_code(target.get('file_path'))
                    folder_title, hint = _metadata_book_folder_identity(target.get('series_name'), content_kind)
                    if _metadata_title_matches(query, folder_title, allow_partial=True):
                        folder_author = hint
                except (TypeError, ValueError):
                    pass
            query, author_hint = _metadata_book_folder_identity(query, content_kind)
            author_hint = author_hint or folder_author
            search_query = _metadata_search_query(query, config)
            print(f'[RabbitPlugins-Metadata] 수동 검색 query={search_query!r} '
                  f'content_kind={content_kind!r} book_type={book_type!r} '
                  f'ebook_code={ebook_code or "없음"} sources={",".join(sources)}')
            results = self._search_metadata(
                search_query, config, db_type, content_kind, book_type,
                manual=True, ebook_code=ebook_code, author_hint=author_hint)
            return {'success': True, 'results': results, 'query': search_query}
        if action_id == 'metadata_apply':
            if not has_request_context() or session.get('role') != 'admin':
                return {'success': False, 'error': '관리자만 메타데이터를 저장할 수 있습니다.'}
            try:
                book_id = int(context.get('book_id'))
            except (TypeError, ValueError):
                return {'success': False, 'error': '적용할 도서를 선택하지 않았습니다.'}
            config = self.get_plugin_config(db_type or 'general', {}) or {}
            return_value = self._apply_metadata(
                self.get_db_gateway(db_type or 'general'), book_id,
                context.get('metadata') or context.get('item_data') or {}, config,
                context.get('fields'), manual=True)
            return {'success': return_value[0], 'message': return_value[1]}
        if action_id != 'save_detail_metadata':
            return {'success': False, 'error': '지원하지 않는 작업입니다.'}
        if not has_request_context() or session.get('role') != 'admin':
            return {'success': False, 'error': '관리자만 그림작가와 연재일을 수정할 수 있습니다.'}
        if db_type not in ('general', 'adult'):
            return {'success': False, 'error': '지원하지 않는 서재입니다.'}

        series_name = str(context.get('series_name') or '').strip()
        artist = str(context.get('cover_artist') or '').strip()
        release_date = str(context.get('release_date') or '').strip()
        start_date = str(context.get('publication_start_date') or '').strip()
        end_date = str(context.get('publication_end_date') or '').strip()
        chapter_text = str(context.get('manual_chapter_count') or '').strip()
        if chapter_text and (not chapter_text.isdigit() or not 1 <= int(chapter_text) <= 1000000):
            return {'success': False, 'error': '회차 수는 1부터 1000000 사이의 정수로 입력해 주세요.'}
        if start_date and end_date and start_date > end_date:
            return {'success': False, 'error': '연재종료일이 시작일보다 빠릅니다.'}
        if not series_name or len(artist) > 500:
            return {'success': False, 'error': '시리즈명 또는 그림작가 값을 확인해 주세요.'}
        for value in (start_date, end_date, release_date):
            if value:
                try:
                    date.fromisoformat(value)
                except ValueError:
                    return {'success': False, 'error': '날짜는 YYYY-MM-DD 형식으로 입력해 주세요.'}

        gateway = self.get_db_gateway(db_type)
        books = gateway.fetch_all(
            'SELECT id, library_id, file_path, file_format, file_mtime, file_size FROM books '
            'WHERE series_name = ? AND COALESCE(is_deleted, 0) = 0 ORDER BY id',
            (series_name,))
        if not books:
            return {'success': False, 'error': '수정할 시리즈를 찾을 수 없습니다.'}
        if 'release_date' in context:
            gateway.execute('UPDATE books SET release_date = ? WHERE series_name = ? AND COALESCE(is_deleted, 0) = 0', (release_date or None, series_name))
        volumes = [(book, _comicinfo_metadata(book)) for book in books]
        count = next((info['count'] for _, info in volumes if info['count']), 0)
        start_books = [book for book, info in volumes if info['volume'] == 1]
        end_books = [book for book, info in volumes if count and info['volume'] == count]
        # 코어 공용 편집 API가 그림작가와 권별 연재일을 받지 않아 플러그인 액션으로 함께 저장한다.
        cover_artist_supported = _optional_column_sql(gateway, 'books', 'b', 'cover_artist') != 'NULL'
        artist_changed = any(str(info.get('artist') or '').strip() != artist for _, info in volumes)
        if cover_artist_supported:
            gateway.execute(
                'UPDATE books SET cover_artist = ? WHERE series_name = ? AND COALESCE(is_deleted, 0) = 0',
                (artist, series_name))
        for library_id in {book['library_id'] for book in books}:
            key = _series_dates_key(library_id, series_name)
            stored = _read_series_dates(gateway, key)
            if 'publication_start_date' in context:
                stored['start'] = start_date
            if 'publication_end_date' in context:
                stored['end'] = end_date
            if 'manual_chapter_count' in context:
                if chapter_text:
                    stored['manual_chapter_count'] = int(chapter_text)
                else:
                    stored.pop('manual_chapter_count', None)
            gateway.set_setting(key, json.dumps(stored))
        return {
            'success': True,
            'cover_artist_saved': cover_artist_supported or not artist_changed,
            'warnings': ['현재 DB에 cover_artist 컬럼이 없어 그림작가 변경은 저장하지 못했습니다.']
            if not cover_artist_supported and artist_changed else [],
        }

    def _home_recently_added(self, db_type, limit=20):
        if not has_request_context() or not session.get('user_id'):
            return {'success': False, 'error': '로그인이 필요합니다.'}
        if session.get('is_default_password') == 1:
            return {'success': False, 'error': '비밀번호를 먼저 변경해 주세요.'}
        if str(db_type or '').strip().lower() != 'general':
            return {'success': False, 'error': '일반 도서 홈 위젯입니다.'}

        try:
            limit = min(20, max(1, int(limit)))
        except (TypeError, ValueError):
            limit = 20

        config = self.get_plugin_config('general', {})
        sections = config.get('home_library_sections', []) if isinstance(config, dict) else []
        if isinstance(sections, str):
            try:
                sections = json.loads(sections)
            except (TypeError, ValueError):
                sections = []
        if not isinstance(sections, list):
            sections = []

        from services.category_service import CategoryService
        libraries = CategoryService.get_libraries(
            'general', user_id=session['user_id'], role=session.get('role'))
        available = {str(library['id']): library for library in libraries}
        selected = []
        seen = set()
        for section in sections:
            if not isinstance(section, dict):
                continue
            library_id = str(section.get('library_id') or '').strip()
            try:
                item_limit = min(limit, 20, max(1, int(section.get('limit', 5))))
            except (TypeError, ValueError):
                item_limit = min(limit, 5)
            if library_id in available and library_id not in seen:
                selected.append((library_id, item_limit))
                seen.add(library_id)

        if not selected:
            return {'success': True, 'items': [{
                'item_type': 'metric', 'metric': '설정 안내',
                'value': '표시할 라이브러리를 선택해 주세요.',
                'description': 'Rabbit Plugins 설정에서 라이브러리와 작품 수를 추가해 주세요.',
            }]}

        from services.book_service import get_cover_image_with_t

        gateway = self.get_db_gateway('general')
        core, error = _load_core_dependencies()
        if error:
            return {'success': False, 'error': error}
        check_rating = core.get('check_book_rating_permission')
        rating_service = core.get('ContentRatingService')
        try:
            max_rating = int(session.get('content_rating_max', 18))
        except (TypeError, ValueError):
            max_rating = 0
        def visible(row):
            if check_rating:
                return bool(check_rating('general', row['id']))
            level = (rating_service.compute_effective_level(row.get('books_lv'), row.get('genre'), row.get('tags'))
                     if rating_service else _fallback_effective_level(row))
            return level <= max_rating
        items = []
        query = (
            'SELECT id, library_id, title, title_alias, series_name, series_alias, author, publisher, '
            'cover_image, cover_updated_at, file_format, total_pages, created_at, books_lv, genre, tags FROM books '
            'WHERE library_id = ? AND COALESCE(is_deleted, 0) = 0 '
            'ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?'
        )
        for library_id, item_limit in selected:
            library = available[library_id]
            by_series = {}
            for offset in range(0, 1000, 100):
                rows = gateway.fetch_all(query, (library_id, 100, offset))
                for row in rows:
                    if not visible(row):
                        continue
                    series_name = str(row.get('series_name') or '').strip()
                    key = series_name or f"__single__:{row['id']}"
                    by_series.setdefault(key, row)
                    if len(by_series) >= item_limit:
                        break
                if len(by_series) >= item_limit or len(rows) < 100:
                    break

            recent = list(by_series.values())[:item_limit]
            items.append({
                'item_type': 'metric',
                'metric': library['name'],
                'value': f'{len(recent)}개',
                'description': '신규 도서' if recent else '표시할 작품이 없습니다.',
                'library_id': library_id,
                'limit': item_limit,
            })
            for row in recent:
                cover = get_cover_image_with_t(row.get('cover_image'), row.get('cover_updated_at'))
                series_name = str(row.get('series_name') or '').strip()
                items.append({
                    'id': row.get('id'),
                    'library_id': row.get('library_id'),
                    'title': row.get('title') or '제목 없음',
                    'title_alias': row.get('title_alias') or '',
                    'series_name': series_name or '기타 단행본',
                    'series_alias': row.get('series_alias') or '',
                    'author': row.get('author') or '',
                    'publisher': row.get('publisher') or '',
                    'pubDate': str(row.get('created_at') or '')[:10],
                    'cover': cover,
                    'file_format': row.get('file_format') or '',
                    'total_pages': row.get('total_pages') or 0,
                })

        return {'success': True, 'items': items}

    def get_dashboard_data(self, db_type, limit=12):
        # 홈 위젯 요청에는 book_id가 없고, 상세페이지의 파일/추천 요청에는 항상 들어온다.
        if has_request_context() and 'book_id' not in request.args:
            return self._home_recently_added(db_type, limit)

        # 공용 데이터 라우트에 login_required가 없으므로 여기서 반드시 검사한다.
        if not has_request_context() or not session.get('user_id'):
            return {'success': False, 'error': '로그인이 필요합니다.'}
        if session.get('is_default_password') == 1:
            return {'success': False, 'error': '비밀번호를 먼저 변경해 주세요.'}
        db_type = str(db_type or 'general').strip().lower()
        admin = session.get('role') == 'admin'
        if db_type not in ('general', 'adult', 'audiobook', 'video') or (
            db_type == 'adult' and not admin and session.get('has_adult_access') != 1
        ):
            return {'success': False, 'error': '접근할 수 없는 서재입니다.'}
        try:
            book_id = int(request.args.get('book_id', ''))
            limit = min(24, max(1, int(limit)))
        except (ValueError, TypeError):
            return {'success': False, 'error': '올바른 도서를 선택해 주세요.'}
        mode = request.args.get('mode', 'files')
        if book_id < 1 or mode not in ('files', 'similar', 'discovery'):
            return {'success': False, 'error': '올바르지 않은 요청입니다.'}

        core, compatibility_error = _load_core_dependencies()
        if compatibility_error:
            return {'success': False, 'error': compatibility_error}
        check_adult_permission = core['check_adult_permission']
        check_download_permission = core['check_download_permission']
        check_book_rating_permission = core.get('check_book_rating_permission')
        ContentRatingService = core.get('ContentRatingService')
        if not check_adult_permission(db_type):
            return {'success': False, 'error': '접근할 수 없는 서재입니다.'}
        max_rating = session.get('content_rating_max', 18)
        def visible(row):
            if admin:
                return True
            level = (ContentRatingService.compute_effective_level(
                row.get('books_lv'), row.get('genre'), row.get('tags'))
                if ContentRatingService else _fallback_effective_level(row))
            return level <= int(max_rating)

        gateway = self.get_db_gateway(db_type)
        libraries = [] if admin else sorted(str(row['library_id']) for row in gateway.fetch_all(
            'SELECT library_id FROM user_category_permissions WHERE user_id = ? AND has_access = 1',
            (session['user_id'],)))
        permission = '' if admin else ' AND b.library_id IN (' + ','.join('?' for _ in libraries) + ')'
        if not admin and not libraries:
            return {'success': False, 'error': '접근 가능한 서재가 없습니다.'}
        if db_type in ('audiobook', 'video'):
            media_mode = 'similar' if mode == 'discovery' else mode
            result = self._media_data(gateway, db_type, book_id, media_mode, limit, admin, permission, libraries)
            if mode == 'discovery' and result.get('success'):
                return {'success': True, 'relations': {}, 'recommendations': result.get('items', [])}
            return result
        localized_series_sql = _optional_column_sql(gateway, 'books', 'b', 'localized_series')
        content_kind_sql = _optional_column_sql(gateway, 'libraries', 'l', 'content_kind')
        target = gateway.fetch_one(
            'SELECT b.id, b.series_name, b.series_alias, ' + localized_series_sql + ' AS localized_series, '
            'b.library_id, ' + content_kind_sql + ' AS content_kind, b.author, b.genre, b.tags, b.books_lv, '
            'b.file_path, b.file_format, b.file_mtime, b.file_size FROM books b '
            'LEFT JOIN libraries l ON l.id = b.library_id '
            'WHERE b.id = ? AND COALESCE(b.is_deleted, 0) = 0' + permission,
            (book_id, *libraries))
        if not target or not visible(target):
            return {'success': False, 'error': '도서를 찾을 수 없거나 접근 권한이 없습니다.'}
        target['localized_series'] = _localized_series_value(target)

        if mode == 'files':
            config = self.get_plugin_config('general', {})
            support_summary_html = config.get('support_summary_html', True)
            support_summary_html = (
                support_summary_html is True
                or str(support_summary_html).casefold() in ('1', 'true', 'yes', 'on')
            )
            exclude_tags = str(config.get('exclude_tags') or '').strip()
            exclude_genres = str(config.get('exclude_genres') or '').strip()
            library = gateway.fetch_one('SELECT name FROM libraries WHERE id = ?', (target['library_id'],))
            cover_artist_sql = _optional_column_sql(gateway, 'books', 'b', 'cover_artist')
            comicinfo_book = gateway.fetch_one(
                'SELECT b.file_path, b.file_format, ' + cover_artist_sql + ' AS cover_artist, '
                'b.file_mtime, b.file_size, b.release_date FROM books b '
                'WHERE b.id = ? AND COALESCE(b.is_deleted, 0) = 0' + permission,
                (book_id, *libraries))
            comicinfo = _comicinfo_metadata(comicinfo_book or {})
            if comicinfo_book and comicinfo_book.get('release_date'):
                comicinfo['date'] = comicinfo_book['release_date']
            files = gateway.fetch_all(
                'SELECT b.id, b.file_path, b.file_format, ' + cover_artist_sql + ' AS cover_artist, '
                + _optional_column_sql(gateway, 'books', 'b', 'document_volume_index') + ' AS document_volume_index, '
                + _optional_column_sql(gateway, 'books', 'b', 'document_volume_count') + ' AS document_volume_count, '
                'b.file_size, b.file_mtime, b.created_at, '
                'b.books_lv, b.genre, b.tags, b.release_date, b.publication_status, b.total_pages, '
                'COALESCE(p.pages_read, 0) AS pages_read, COALESCE(p.is_completed, 0) AS is_completed, p.last_read_at '
                'FROM books b LEFT JOIN user_progress p ON p.book_id = b.id AND p.user_id = ? '
                'WHERE b.series_name = ? AND b.library_id = ? AND COALESCE(b.is_deleted, 0) = 0 ORDER BY b.id',
                (session['user_id'], target['series_name'], target['library_id']))
            files = [row for row in files if visible(row)]
            volume_metadata = []
            for row in files:
                info = dict(comicinfo) if int(row['id']) == book_id else _comicinfo_metadata(row)
                if row.get('document_volume_index') is not None:
                    info['volume'] = row['document_volume_index']
                if row.get('document_volume_count') is not None:
                    info['count'] = row['document_volume_count']
                info['date'] = row.get('release_date') or info['date']
                volume_metadata.append(info)
            comicinfo['summary'] = comicinfo.get('summary') or next(
                (item['summary'] for item in volume_metadata if item.get('summary')), '')
            localized_series = target.get('localized_series') or next(
                (item['localized_series'] for item in volume_metadata if item.get('localized_series')), '')
            if not support_summary_html:
                comicinfo['summary'] = ''
            count_values = []
            volume_values = set()
            number_values = set()
            for item in volume_metadata:
                try:
                    item_count = int(item.get('count') or 0)
                except (TypeError, ValueError, OverflowError):
                    item_count = 0
                if item_count > 0:
                    count_values.append(item_count)
                try:
                    item_volume = float(item.get('volume') or 0)
                except (TypeError, ValueError, OverflowError):
                    item_volume = 0
                if item_volume > 0:
                    volume_values.add(item_volume)
                try:
                    item_number = float(item.get('number') or 0)
                except (TypeError, ValueError, OverflowError):
                    item_number = 0
                if item_number > 0:
                    number_values.add(item_number)
            count = max(count_values, default=0)
            comicinfo['count'] = count or None
            comicinfo['final_volume_found'] = bool(count and float(count) in volume_values)
            comicinfo['available_volume_count'] = len(volume_values)
            comicinfo['final_number_found'] = bool(count and float(count) in number_values)
            comicinfo['available_number_count'] = len(number_values)
            publication_status = next(
                (str(row.get('publication_status') or '').strip()
                 for row in files if str(row.get('publication_status') or '').strip()),
                '')
            if not publication_status:
                publication_status = next(
                    (str(item.get('publication_status') or '').strip()
                     for item in volume_metadata if str(item.get('publication_status') or '').strip()),
                    '')
            comicinfo['publication_status_label'] = {
                '0': '연재', '1': '휴재', '2': '완결',
            }.get(publication_status, '')
            visible_files = []
            for row, info in zip(files, volume_metadata):
                item = {key: row[key] for key in (
                    'id', 'file_path', 'file_format', 'file_size', 'file_mtime', 'created_at', 'books_lv', 'genre', 'tags',
                    'release_date', 'total_pages', 'pages_read', 'is_completed', 'last_read_at'
                )}
                # These values come from each file's embedded metadata.  The
                # detail renderer uses them to distinguish chapters from
                # volumes and to group chapters inside a volume.
                item['volume'] = info.get('volume')
                item['count'] = info.get('count')
                item['number'] = info.get('number')
                visible_files.append(item)
            publication_dates = {
                'start': next((item['date'] for item in volume_metadata
                               if item['date'] and (item['volume'] == 1 or (not volume_values and item.get('number') == 1))), ''),
                'end': next((item['date'] for item in volume_metadata
                             if item['date'] and count and
                             (item['volume'] == count or (not volume_values and item.get('number') == count))), ''),
            }
            try:
                stored_dates = _read_series_dates(gateway, _series_dates_key(target['library_id'], target['series_name']))
            except (ValueError, TypeError):
                stored_dates = {}
            if publication_status == '1' and stored_dates.get('publication_status_label') == '연재중단':
                comicinfo['publication_status_label'] = '연재중단'
            for key in ('start', 'end'):
                if key in stored_dates:
                    publication_dates[key] = _metadata_date(stored_dates.get(key))
            # 기존 저장 API의 WHERE series_name 범위와 일치시킨다(삭제 표시 행도 포함).
            scope = gateway.fetch_one(
                'SELECT COUNT(*) AS books, COUNT(DISTINCT library_id) AS libraries FROM books WHERE series_name = ?',
                (target['series_name'],)) if admin else None
            return {
                'success': True, 'files': visible_files, 'comicinfo': comicinfo,
                'localized_series': localized_series, 'publication_dates': publication_dates,
                'publication_coverage': _chapter_file_coverage(files, stored_dates.get('total_chapters')),
                'manual_chapter_count': stored_dates.get('manual_chapter_count'),
                'support_summary_html': support_summary_html, 'exclude_tags': exclude_tags,
                'exclude_genres': exclude_genres, 'metadata_sources': _metadata_source_order(config),
                'metadata_auto_enabled': _config_bool(config.get('metadata_auto_enabled'), False),
                'metadata_hold': _read_metadata_hold(
                    gateway, target['library_id'], target['series_name']) if admin else {},
                'metadata_fields': _metadata_field_selection(config, automatic=True),
                'can_edit': admin, 'edit_scope': scope,
                'library_name': library['name'] if library else '',
                'library_id': target['library_id'],
                'content_kind': target.get('content_kind') or 'unspecified',
                'can_download': check_download_permission(),
            }

        if mode == 'discovery':
            return self._discovery_data(
                gateway, target, permission, libraries, visible, check_book_rating_permission, db_type, limit)
        return self._similar_data(
            gateway, target, permission, libraries, visible, check_book_rating_permission, db_type, limit)

    def _series_db_path(self):
        configured = str(self.get_plugin_config('general', {}).get('series_db_path') or '').strip()
        if configured:
            path = Path(configured).expanduser()
        else:
            try:
                from flask import current_app
                root_path = Path(current_app.root_path)
            except RuntimeError:
                root_path = Path('/app')
            path = root_path / 'db' / 'series.full.sqlite'
        if not path.is_absolute():
            path = Path(current_app.root_path) / path
        return path.resolve()

    def _optimize_series_db(self):
        path = self._series_db_path()
        if not path.is_file():
            return {'success': False, 'error': f'Series.db 파일을 찾을 수 없습니다: {path}'}

        required_columns = {
            'id', 'title', 'native_title', 'secondary_titles_ko', 'source_manga_updates_id',
            'type', 'links', 'state', 'status', 'titles', 'artists', 'authors', 'final_volume',
            'content_rating', 'cover_raw_url', 'source_manga_updates_response_series_id',
            'source_manga_updates_response_recommendations', 'relationships_v2',
            'relationships_other', 'relationships_sequel', 'relationships_spin_off',
            'relationships_main_story', 'relationships_alternative', 'relationships_adaptation',
            'relationships_prequel', 'relationships_side_story',
        }
        optimized_objects = {
            ('table', 'series_title_fts'), ('table', 'series_creator_fts'),
            ('view', 'series_recommendation_items'), ('view', 'series_relationship_items'),
            ('trigger', 'series_fts_after_insert'), ('trigger', 'series_fts_after_delete'),
            ('trigger', 'series_fts_after_update'), ('index', 'idx_series_manga_updates_id'),
            ('index', 'idx_series_type_status'),
        }
        uri = path.as_uri() + '?mode=ro'

        def inspect_database():
            db = sqlite3.connect(uri, uri=True, timeout=30)
            try:
                columns = {row[1] for row in db.execute('PRAGMA table_info(Series)')}
                objects = set(db.execute(
                    "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
                row = db.execute('SELECT COUNT(*) FROM Series').fetchone()
                if 'state' not in columns:
                    missing = (required_columns - {'state'}) - columns
                    if not missing and not (optimized_objects - objects):
                        return 'optimized', '', row[0]
                    details = ', '.join(sorted(missing)) if missing else 'FTS·뷰·인덱스 구성'
                    return 'unsupported', f'state 컬럼이 없거나 {details}가 부족해 변경하지 않았습니다.', row[0]
                missing = required_columns - columns
                if missing:
                    return 'unsupported', '필수 컬럼이 없어 변경하지 않았습니다: ' + ', '.join(sorted(missing)), row[0]
                return 'ready', '', row[0]
            except sqlite3.Error as exc:
                return 'error', str(exc), 0
            finally:
                db.close()

        def status_result(status):
            if status[0] == 'optimized':
                return {'success': True, 'already_optimized': True,
                        'message': '이미 최적화된 DB입니다. 파일을 변경하지 않았습니다.'}
            if status[0] == 'unsupported':
                return {'success': False, 'error': status[1]}
            if status[0] == 'error':
                return {'success': False, 'error': 'Series.db 구조를 확인하지 못했습니다: ' + status[1]}
            return None

        status = inspect_database()
        result = status_result(status)
        if result:
            return result

        lock_path = path.with_name(path.name + '.rabbit-plugins-optimize.lock')
        try:
            with open(lock_path, 'a+b') as lock:
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return {'success': False, 'error': 'Series.db 최적화가 이미 실행 중입니다.'}

                status = inspect_database()
                result = status_result(status)
                if result:
                    return result

                script_path = Path(__file__).with_name('series_db_optimize.sql')
                if not script_path.is_file():
                    return {'success': False, 'error': '최적화 SQL 파일이 플러그인에 없습니다.'}

                try:
                    test_db = sqlite3.connect(':memory:')
                    try:
                        test_db.execute("CREATE VIRTUAL TABLE fts_test USING fts5(value, tokenize='trigram')")
                    finally:
                        test_db.close()
                except sqlite3.Error as exc:
                    return {'success': False, 'error': f'현재 SQLite가 FTS5 trigram을 지원하지 않습니다: {exc}'}

                original_size = path.stat().st_size
                required_free = original_size * 3 + 64 * 1024 * 1024
                if shutil.disk_usage(path.parent).free < required_free:
                    return {'success': False, 'error': '백업과 VACUUM에 필요한 여유 공간이 부족합니다.'}

                backup_path = path.with_name(
                    f'{path.stem}.backup-{datetime.now().strftime("%Y%m%d-%H%M%S-%f")}{path.suffix}')
                source_db = backup_db = None
                backup_ready = False
                try:
                    source_db = sqlite3.connect(uri, uri=True, timeout=60)
                    backup_db = sqlite3.connect(str(backup_path), timeout=60)
                    source_db.backup(backup_db, pages=10000, sleep=0.1)
                    backup_check = backup_db.execute('PRAGMA quick_check').fetchone()[0]
                    backup_db.close()
                    backup_db = None
                    source_db.close()
                    source_db = None
                    if backup_check != 'ok':
                        backup_path.unlink(missing_ok=True)
                        return {'success': False, 'error': '자동 백업 검증에 실패해 최적화를 중단했습니다.'}
                    if path.stat().st_size != original_size:
                        return {'success': False, 'error': '백업 중 원본 DB가 바뀌어 최적화를 중단했습니다.',
                                'backup_path': str(backup_path)}
                    backup_ready = True

                    db = sqlite3.connect(str(path), timeout=60)
                    try:
                        db.execute('PRAGMA busy_timeout=60000')
                        db.executescript(script_path.read_text(encoding='utf-8'))
                    finally:
                        db.close()

                    status = inspect_database()
                    if status[0] != 'optimized':
                        raise RuntimeError(status[1] or '최적화된 DB 구조를 확인하지 못했습니다.')
                    verify_db = sqlite3.connect(uri, uri=True, timeout=60)
                    try:
                        integrity = verify_db.execute('PRAGMA integrity_check').fetchone()[0]
                    finally:
                        verify_db.close()
                    if integrity != 'ok':
                        raise RuntimeError('SQLite integrity_check 결과가 ok가 아닙니다: ' + integrity)

                    return {
                        'success': True,
                        'message': f"Series.db 최적화와 검증을 완료했습니다. {status[2]:,}개 작품이 남았습니다.",
                        'backup_path': str(backup_path),
                    }
                except Exception as exc:
                    if source_db:
                        source_db.close()
                    if backup_db:
                        backup_db.close()
                    if not backup_ready:
                        backup_path.unlink(missing_ok=True)
                        return {'success': False,
                                'error': f'자동 백업 중 오류가 발생해 원본 DB를 변경하지 않았습니다: {exc}'}
                    try:
                        saved_db = sqlite3.connect(str(backup_path), timeout=60)
                        restored_db = sqlite3.connect(str(path), timeout=60)
                        try:
                            saved_db.backup(restored_db, pages=10000, sleep=0.1)
                        finally:
                            restored_db.close()
                            saved_db.close()
                    except Exception as restore_exc:
                        return {'success': False,
                                'error': f'최적화에 실패했고 자동 복구도 실패했습니다: {restore_exc}. 백업: {backup_path}'}
                    return {'success': False,
                            'error': f'최적화에 실패해 백업으로 복구했습니다: {exc}. 백업: {backup_path}',
                            'backup_path': str(backup_path)}
        except OSError as exc:
            return {'success': False, 'error': f'Series.db 최적화를 시작하지 못했습니다: {exc}'}

    def _series_index(self):
        """Build a small searchable sidecar without writing to the 4.5 GB source DB."""
        from flask import current_app

        source = self._series_db_path()
        if not source.is_file():
            return None
        stat = source.stat()
        source_path = str(source)
        index = Path(current_app.root_path) / 'cache' / 'rabbit_plugins_series_index.sqlite'
        temporary = index.with_name(index.name + '.tmp')
        lock_path = index.with_name(index.name + '.lock')

        def is_current():
            try:
                with sqlite3.connect(index.as_uri() + '?mode=ro', uri=True) as db:
                    columns = {row[1] for row in db.execute('PRAGMA table_info(series_lookup)')}
                    title_columns = {row[1] for row in db.execute('PRAGMA table_info(series_title_lookup)')}
                    if not {'series_type', 'native_title'} <= columns or not {'series_id', 'title_key'} <= title_columns:
                        return False
                    row = db.execute('SELECT source_path, source_size, source_mtime_ns FROM index_meta').fetchone()
                return row == (source_path, stat.st_size, stat.st_mtime_ns)
            except (OSError, sqlite3.Error):
                return False

        try:
            index.parent.mkdir(parents=True, exist_ok=True)
            with open(lock_path, 'a+b') as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                if is_current():
                    return index
                temporary.unlink(missing_ok=True)
                source_db = sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=30)
                source_db.row_factory = sqlite3.Row
                index_db = sqlite3.connect(temporary, timeout=30)
                index_db.execute('PRAGMA journal_mode=OFF')
                index_db.execute('PRAGMA synchronous=OFF')
                index_db.execute(
                    'CREATE TABLE series_lookup '
                    '(id INTEGER PRIMARY KEY, series_type TEXT, native_title TEXT, native_key TEXT, ko_titles TEXT, manga_updates_id TEXT)')
                index_db.execute('CREATE TABLE series_title_lookup (series_id INTEGER, title_key TEXT)')
                index_db.execute('CREATE TABLE index_meta (source_path TEXT, source_size INTEGER, source_mtime_ns INTEGER)')
                cursor = source_db.execute(
                    'SELECT id, type AS series_type, native_title, titles, secondary_titles_ko, source_manga_updates_id FROM series')
                while True:
                    batch = cursor.fetchmany(3000)
                    if not batch:
                        break
                    values, title_values = [], []
                    for row in batch:
                        native_title = str(row['native_title'] or '').strip()
                        native_key = _title_key(native_title)
                        ko_titles = _korean_titles(row['titles'], row['secondary_titles_ko'])
                        manga_updates_id = str(row['source_manga_updates_id'] or '').strip().casefold()
                        if native_key or ko_titles or manga_updates_id:
                            values.append((row['id'], row['series_type'], native_title, native_key,
                                           json.dumps(ko_titles, ensure_ascii=False), manga_updates_id))
                            title_values.extend((row['id'], _series_title_key(title)) for title in ko_titles
                                                if _series_title_key(title))
                    if values:
                        index_db.executemany('INSERT INTO series_lookup VALUES (?, ?, ?, ?, ?, ?)', values)
                    if title_values:
                        index_db.executemany('INSERT INTO series_title_lookup VALUES (?, ?)', title_values)
                after = source.stat()
                if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                    source_db.close()
                    index_db.close()
                    temporary.unlink(missing_ok=True)
                    return None
                index_db.execute('CREATE INDEX idx_series_lookup_native ON series_lookup(native_key)')
                index_db.execute('CREATE INDEX idx_series_lookup_mu ON series_lookup(manga_updates_id)')
                index_db.execute('CREATE INDEX idx_series_title_lookup_key ON series_title_lookup(title_key)')
                index_db.execute('INSERT INTO index_meta VALUES (?, ?, ?)', (source_path, stat.st_size, stat.st_mtime_ns))
                index_db.commit()
                source_db.close()
                index_db.close()
                os.replace(temporary, index)
            return index
        except (OSError, sqlite3.Error):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return None

    def _source_series_rows(self, series_ids):
        ids = list(dict.fromkeys(int(value) for value in series_ids if str(value).isdigit()))
        if not ids:
            return {}
        fields = list(RELATION_FIELDS.values()) + ['source_manga_updates_response_recommendations']
        query = 'SELECT id, ' + ', '.join(fields) + ' FROM series WHERE id IN (' + ','.join('?' for _ in ids) + ')'
        path = self._series_db_path()
        try:
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=20) as db:
                db.row_factory = sqlite3.Row
                return {row['id']: dict(row) for row in db.execute(query, ids)}
        except (OSError, sqlite3.Error):
            return {}

    def _discovery_data(self, gateway, target, permission, libraries, visible,
                        check_rating, db_type, limit):
        index_path = self._series_index()
        if not index_path:
            similar = self._similar_data(
                gateway, target, permission, libraries, visible, check_rating, db_type, limit,
                use_cache=False)
            return {'success': True, 'relations': {}, 'recommendations': similar['items']}

        local_titles = [target['series_name'], target['series_alias']]
        local_title_keys = {_series_title_key(value) for value in local_titles if _series_title_key(value)}
        try:
            with sqlite3.connect(index_path.as_uri() + '?mode=ro', uri=True) as db:
                db.row_factory = sqlite3.Row
                source_matches = {}
                localized_key = _title_key(target.get('localized_series'))
                if localized_key:
                    rows = db.execute(
                        'SELECT id, series_type, native_title, native_key, ko_titles, manga_updates_id '
                        'FROM series_lookup WHERE native_key = ?', (localized_key,))
                    source_matches.update((row['id'], dict(row)) for row in rows)
                if local_title_keys:
                    placeholders = ','.join('?' for _ in local_title_keys)
                    rows = db.execute(
                        'SELECT DISTINCT s.id, s.series_type, s.native_title, s.native_key, s.ko_titles, s.manga_updates_id '
                        'FROM series_title_lookup t JOIN series_lookup s ON s.id = t.series_id '
                        'WHERE t.title_key IN (' + placeholders + ')', tuple(local_title_keys))
                    source_matches.update((row['id'], dict(row)) for row in rows)
                source_matches = [row for row in source_matches.values() if
                    _series_type_matches_library(row['series_type'], target.get('content_kind')) and (
                        (localized_key and row['native_key'] == localized_key) or any(
                            _series_title_key(title) in local_title_keys
                            for title in json.loads(row['ko_titles'] or '[]')))]
        except (OSError, sqlite3.Error, ValueError, TypeError):
            source_matches = []
        if not source_matches:
            similar = self._similar_data(
                gateway, target, permission, libraries, visible, check_rating, db_type, limit,
                use_cache=False)
            return {'success': True, 'relations': {}, 'recommendations': similar['items']}

        source_ids = [row['id'] for row in source_matches]
        source_rows = self._source_series_rows(source_ids)
        if not source_rows:
            similar = self._similar_data(
                gateway, target, permission, libraries, visible, check_rating, db_type, limit,
                use_cache=False)
            return {'success': True, 'relations': {}, 'recommendations': similar['items']}

        relation_ids = {kind: [] for kind in RELATION_FIELDS}
        recommendations = []
        for row in source_rows.values():
            for kind, field in RELATION_FIELDS.items():
                for value in _relation_ids(row[field]):
                    if value not in relation_ids[kind]:
                        relation_ids[kind].append(value)
            try:
                values = json.loads(row['source_manga_updates_response_recommendations'] or '[]')
            except (TypeError, ValueError):
                values = []
            if isinstance(values, list):
                for value in values:
                    if isinstance(value, dict) and _recommendation_slug(value) and all(
                        _recommendation_slug(old) != _recommendation_slug(value) for old in recommendations):
                        recommendations.append(value)

        if relation_ids['prequel']:
            for row in self._source_series_rows(relation_ids['prequel']).values():
                for field in (RELATION_FIELDS['main_story'], RELATION_FIELDS['prequel']):
                    for value in _relation_ids(row[field]):
                        if value not in source_ids and value not in relation_ids['prequel'] and value not in relation_ids['main_story']:
                            relation_ids['main_story'].append(value)

        all_ids = list(dict.fromkeys(value for values in relation_ids.values() for value in values))
        slugs = list(dict.fromkeys(_recommendation_slug(value) for value in recommendations))
        try:
            with sqlite3.connect(index_path.as_uri() + '?mode=ro', uri=True) as db:
                db.row_factory = sqlite3.Row
                by_id = {}
                if all_ids:
                    placeholders = ','.join('?' for _ in all_ids)
                    by_id = {row['id']: dict(row) for row in db.execute(
                        'SELECT id, series_type, native_title, native_key, ko_titles, manga_updates_id FROM series_lookup WHERE id IN (' + placeholders + ')',
                        all_ids)}
                by_slug = {slug: [dict(row) for row in db.execute(
                    'SELECT id, series_type, native_title, native_key, ko_titles, manga_updates_id FROM series_lookup WHERE manga_updates_id = ?',
                    (slug,))] for slug in slugs}
        except (OSError, sqlite3.Error):
            by_id, by_slug = {}, {}

        candidates = list(by_id.values()) + [row for rows in by_slug.values() for row in rows]
        korean_titles = list(dict.fromkeys(
            [str(title).strip() for title in local_titles if str(title or '').strip()] +
            [title for row in candidates for title in json.loads(row['ko_titles'] or '[]') if str(title).strip()]))
        native_titles = list(dict.fromkeys(
            str(row.get('native_title') or '').strip()
            for row in candidates if str(row.get('native_title') or '').strip()))
        local_books = []
        if korean_titles or native_titles:
            localized_series_sql = _optional_column_sql(gateway, 'books', 'b', 'localized_series')
            content_kind_sql = _optional_column_sql(gateway, 'libraries', 'l', 'content_kind')
            identity_conditions = []
            query_params = []
            if korean_titles:
                placeholders = ','.join('?' for _ in korean_titles)
                title_patterns = [
                    re.sub(r'([!%_])', r'!\1', str(title)) + '%[%]'
                    for title in korean_titles
                ]
                pattern_conditions = ' OR '.join(
                    "(b.series_name LIKE ? ESCAPE '!' OR b.series_alias LIKE ? ESCAPE '!')"
                    for _ in title_patterns
                )
                identity_conditions.append(
                    '(b.series_name IN (' + placeholders + ') OR b.series_alias IN ('
                    + placeholders + ') OR ' + pattern_conditions + ')')
                query_params.extend(korean_titles)
                query_params.extend(korean_titles)
                query_params.extend(pattern for pattern in title_patterns for _ in range(2))
            if native_titles and localized_series_sql != 'NULL':
                native_placeholders = ','.join('?' for _ in native_titles)
                identity_conditions.append(
                    localized_series_sql + ' IN (' + native_placeholders + ')')
                query_params.extend(native_titles)
            local_books = gateway.fetch_all(
                'SELECT b.id, b.library_id, ' + content_kind_sql + ' AS content_kind, b.series_name, b.series_alias, '
                + localized_series_sql + ' AS localized_series, b.cover_image, b.cover_updated_at, '
                'b.author, b.publisher, b.books_lv, b.genre, b.tags, '
                'b.file_path, b.file_format, b.file_mtime, b.file_size, '
                'fc.cover_image AS first_cover, fc.cover_updated_at AS first_cover_updated_at '
                'FROM books b LEFT JOIN libraries l ON l.id = b.library_id '
                'LEFT JOIN books fc ON fc.id = (SELECT fb.id FROM books fb '
                'WHERE fb.series_name = b.series_name AND fb.library_id = b.library_id '
                'AND COALESCE(fb.is_deleted, 0) = 0 AND fb.cover_image IS NOT NULL '
                "AND fb.cover_image <> '' ORDER BY fb.id ASC LIMIT 1) "
                'WHERE COALESCE(b.is_deleted, 0) = 0 AND '
                '(' + ' OR '.join(identity_conditions) + ')' +
                permission + ' ORDER BY b.id',
                (*query_params, *libraries))
        books_by_title = {}
        books_by_localized = {}
        target_series_key = _series_title_key(target['series_name'])
        for book in local_books:
            if not visible(book):
                continue
            # A relation can point to a distinct external edition with the
            # same native title. Never resolve that relation back to the
            # current locally owned series (including its other volumes).
            if _series_title_key(book['series_name']) == target_series_key:
                continue
            for name in (book['series_name'], book['series_alias']):
                key = _series_title_key(name)
                if key:
                    books_by_title.setdefault(key, []).append(book)
            localized_key = _title_key(_localized_series_value(book))
            if localized_key:
                books_by_localized.setdefault(localized_key, []).append(book)

        def local_item(series):
            if not series['native_key']:
                return None
            try:
                titles = json.loads(series['ko_titles'] or '[]')
            except (TypeError, ValueError):
                titles = []
            # A related work without a Korean title has no proven Korean
            # identity. Using the *current* work's title here made an
            # unowned NANA fanbook appear as the owned series "나나".
            matches = []
            for title in titles:
                for book in books_by_title.get(_series_title_key(title), []):
                    if not _series_type_matches_library(series.get('series_type'), book.get('content_kind')):
                        continue
                    book['localized_series'] = _localized_series_value(book)
                    if (not book['localized_series'] or
                        _title_key(book['localized_series']) == series['native_key']) and (
                        not check_rating or check_rating(db_type, book['id'])
                    ) and book not in matches:
                        matches.append(book)
            # Some Series.db relations have no Korean alias.  The local book
            # can still be linked by the original/native title saved in
            # ``localized_series`` during metadata matching.
            if series['native_key']:
                for book in books_by_localized.get(series['native_key'], []):
                    if (_series_type_matches_library(series.get('series_type'), book.get('content_kind'))
                        and (not check_rating or check_rating(db_type, book['id']))
                        and book not in matches):
                        matches.append(book)
            if not matches:
                return None
            matches.sort(key=lambda book: (
                str(book['library_id']) != str(target['library_id']),
                not bool(book['cover_image']), int(book['id'])))
            book = matches[0]
            same_library = [item for item in matches if str(item['library_id']) == str(book['library_id'])]
            from services.book_service import get_cover_image_with_t

            return {
                'book_id': book['id'], 'series_name': book['series_name'],
                'display_name': re.sub(r'\s*\[[^\[\]]+\]\s*$', '',
                                       book['series_alias'] or book['series_name']).strip(),
                'localized_series': book['localized_series'] or series.get('native_title', ''),
                'library_id': book['library_id'],
                'cover': get_cover_image_with_t(
                    book['first_cover'] or book['cover_image'], book.get('first_cover_updated_at')),
                'author': book['author'], 'publisher': book['publisher'],
                'book_count': len(same_library),
            }

        def work_key(item):
            return _series_title_key(item['series_name'])

        relations = {}
        relation_seen = set()
        for kind, ids in relation_ids.items():
            items, seen = [], set()
            for series_id in ids:
                series = by_id.get(series_id)
                if not series:
                    continue
                item = local_item(series)
                key = work_key(item) if item else None
                # Series.db stores reciprocal links in more than one relation
                # bucket (for example a sequel can also be listed as a
                # spin-off). Keep the first, most specific bucket only so the
                # detail page does not render the same work repeatedly.
                if item and key not in seen and key not in relation_seen:
                    items.append(item)
                    seen.add(key)
                    relation_seen.add(key)
                if len(items) >= limit:
                    break
            if items:
                relations[kind] = items

        related_keys = {work_key(item) for items in relations.values() for item in items}
        recommended, seen = [], set()
        for source in recommendations:
            for series in by_slug.get(_recommendation_slug(source), []):
                item = local_item(series)
                key = work_key(item) if item else None
                if item and key not in related_keys and key not in seen:
                    recommended.append(item)
                    seen.add(key)
                    break

        similar = self._similar_data(
            gateway, target, permission, libraries, visible, check_rating, db_type, limit,
            include_random_fallback=False, use_cache=False)['items']
        for item in similar:
            key = work_key(item)
            if key not in related_keys and key not in seen:
                recommended.append(item)
                seen.add(key)
                if len(recommended) >= limit:
                    break
        if not recommended:
            recommended = self._random_genre_tag_recommendations(
                gateway, target, permission, libraries, visible, check_rating, db_type, limit,
                excluded=related_keys)
        return {'success': True, 'relations': relations, 'recommendations': recommended[:limit]}

    def _matching_book_rows(self, gateway, target, permission, libraries, groups, random_order=False):
        condition, values = _term_filter(groups)
        if not condition:
            return []
        order = 'b.id DESC'
        if random_order:
            order = 'RAND()' if getattr(gateway, '_engine', '') in ('mariadb', 'mysql') else 'RANDOM()'
        localized_series_sql = _optional_column_sql(gateway, 'books', 'b', 'localized_series')
        return gateway.fetch_all(
            'SELECT b.id, b.series_name, b.series_alias, ' + localized_series_sql + ' AS localized_series, '
            'b.library_id, b.author, b.cover_updated_at, '
            'b.books_lv, b.genre, b.tags, b.cover_image, '
            'fc.cover_image AS first_cover, fc.cover_updated_at AS first_cover_updated_at '
            'FROM books b LEFT JOIN books fc ON fc.id = (SELECT fb.id FROM books fb '
            'WHERE fb.series_name = b.series_name AND fb.library_id = b.library_id '
            'AND COALESCE(fb.is_deleted, 0) = 0 AND fb.cover_image IS NOT NULL '
            "AND fb.cover_image <> '' ORDER BY fb.id ASC LIMIT 1) "
            'WHERE COALESCE(b.is_deleted, 0) = 0 '
            "AND b.series_name IS NOT NULL AND b.series_name <> '' AND b.series_name <> ?" + permission +
            ' AND ' + condition + ' ORDER BY ' + order + ' LIMIT 400',
            (target['series_name'], *libraries, *values))

    def _random_genre_tag_recommendations(self, gateway, target, permission, libraries,
                                          visible, check_rating, db_type, limit, excluded=()):
        groups = {field: tokens(target[field]) for field in ('genre', 'tags')}
        rows = self._matching_book_rows(
            gateway, target, permission, libraries, groups, random_order=True)
        from services.book_service import get_cover_image_with_t

        target_names = {_series_title_key(value) for value in (target['series_name'], target['series_alias'])
                        if _series_title_key(value)}
        excluded = set(excluded)
        found, found_by_both = {}, {}
        for row in rows:
            if not visible(row) or (check_rating and not check_rating(db_type, row['id'])):
                continue
            shared_genres = groups['genre'] & tokens(row['genre'])
            shared_tags = groups['tags'] & tokens(row['tags'])
            if _series_title_key(row['series_name']) in target_names or (not shared_genres and not shared_tags):
                continue
            item = {
                'book_id': row['id'], 'series_name': row['series_name'],
                'display_name': re.sub(r'\s*\[[^\[\]]+\]\s*$', '',
                                       row['series_alias'] or row['series_name']).strip(),
                'localized_series': row['localized_series'], 'library_id': row['library_id'],
                'author': row['author'], 'cover': get_cover_image_with_t(
                    row['first_cover'] or row['cover_image'], row.get('first_cover_updated_at')),
            }
            key = (_series_title_key(item['series_name']), item['library_id'])
            work = _series_title_key(item['series_name'])
            if work not in excluded and key not in found:
                found[key] = item
            if work not in excluded and groups['genre'] and groups['tags'] and shared_genres and shared_tags:
                found_by_both.setdefault(key, item)
        return list((found_by_both or found).values())[:limit]

    def _similar_data(self, gateway, target, permission, libraries, visible, check_rating, db_type, limit,
                      include_random_fallback=True, use_cache=True):
        signature = json.dumps([
            db_type, target, libraries, session.get('user_id'), session.get('content_rating_max', 18)
        ], sort_keys=True, ensure_ascii=False)
        cache_key = 'similar-v5:' + hashlib.sha256(signature.encode()).hexdigest()
        if use_cache:
            try:
                cached = self.cache_get(cache_key)
                if cached:
                    items = json.loads(cached)
                    items = [item for item in items if
                             not check_rating or check_rating(db_type, item['book_id'])][:limit]
                    if items or not include_random_fallback:
                        return {'success': True, 'items': items}
                    return {'success': True, 'items': self._random_genre_tag_recommendations(
                        gateway, target, permission, libraries, visible, check_rating, db_type, limit)}
            except (ValueError, TypeError):
                pass

        groups = {field: tokens(target[field]) for field in ('author', 'tags')}
        rows = self._matching_book_rows(gateway, target, permission, libraries, groups)
        ranked = {}
        from services.book_service import get_cover_image_with_t

        target_names = {_series_title_key(value) for value in (target['series_name'], target['series_alias'])
                        if _series_title_key(value)}
        for row in rows:
            if not visible(row):
                continue
            score = 6 * len(groups['author'] & tokens(row['author'])) + len(groups['tags'] & tokens(row['tags']))
            if not score or _series_title_key(row['series_name']) in target_names:
                continue
            key = (_series_title_key(row['series_name']), row['library_id'])
            item = {
                'book_id': row['id'], 'series_name': row['series_name'],
                'display_name': re.sub(r'\s*\[[^\[\]]+\]\s*$', '',
                                       row['series_alias'] or row['series_name']).strip(),
                'localized_series': row['localized_series'], 'library_id': row['library_id'],
                'author': row['author'], 'cover': get_cover_image_with_t(
                    row['first_cover'] or row['cover_image'], row.get('first_cover_updated_at')), 'score': score,
            }
            if key not in ranked or score > ranked[key]['score']:
                ranked[key] = item
        items = sorted(ranked.values(), key=lambda item: (-item['score'], item['series_name']))[:24]
        if use_cache:
            self.cache_set(cache_key, json.dumps(items, ensure_ascii=False), ttl=120)
        if check_rating:
            items = [item for item in items if check_rating(db_type, item['book_id'])]
        if not items and include_random_fallback:
            items = self._random_genre_tag_recommendations(
                gateway, target, permission, libraries, visible, check_rating, db_type, limit)
        return {'success': True, 'items': items[:limit]}

    def _media_data(self, gateway, db_type, book_id, mode, limit, admin, permission, libraries):
        # 테이블과 필드는 검증된 세션에 대한 고정 매핑만 사용한다.
        audio = db_type == 'audiobook'
        table, field = ('audiobooks', 'author') if audio else ('videos', 'genres')
        target = gateway.fetch_one(
            f'SELECT b.id, b.title, b.library_id, b.{field} AS terms FROM {table} b '
            'WHERE b.id = ? AND COALESCE(b.is_deleted, 0) = 0' + permission,
            (book_id, *libraries))
        if not target:
            return {'success': False, 'error': '미디어를 찾을 수 없거나 접근 권한이 없습니다.'}
        if mode == 'files':
            library = gateway.fetch_one('SELECT name FROM libraries WHERE id = ?', (target['library_id'],))
            scope = gateway.fetch_one(
                'SELECT COUNT(*) AS books, COUNT(DISTINCT library_id) AS libraries FROM audiobooks '
                'WHERE title = ? OR folder_name = ?', (target['title'], target['title'])) if audio and admin else None
            # 크기와 경로는 코어 상세 컨텍스트의 트랙/에피소드 데이터를 사용한다.
            return {'success': True, 'files': [], 'can_edit': audio and admin, 'edit_scope': scope, 'library_name': library['name'] if library else '', 'library_id': target['library_id']}
        terms = tokens(target['terms'])
        if not terms or not audio:
            return {'success': True, 'items': []}
        values = ['%' + term.replace('!', '!!').replace('%', '!%').replace('_', '!_') + '%'
                  for term in sorted(terms)[:12]]
        clauses = ' OR '.join([f"LOWER(b.{field}) LIKE ? ESCAPE '!'" for _ in values])
        # ponytail: 후보 400개 한도. 대형 미디어 서재의 누락이 문제면 토큰 인덱스로 확장한다.
        rows = gateway.fetch_all(
            f'SELECT b.id, b.title, b.library_id, b.{field} AS terms FROM {table} b '
            'WHERE b.id <> ? AND COALESCE(b.is_deleted, 0) = 0' + permission +
            ' AND (' + clauses + ') ORDER BY b.id DESC LIMIT 400', (book_id, *libraries, *values))
        items = []
        for row in rows:
            common = terms & tokens(row['terms'])
            if common:
                items.append({'book_id': row['id'], 'series_name': row['title'], 'library_id': row['library_id'],
                              'author': row['terms'] if audio else '',
                              'cover': f"/api/media/{table}/{row['id']}/cover", 'file_format': '',
                              'score': len(common)})
        items.sort(key=lambda item: (-item['score'], item['series_name']))
        return {'success': True, 'items': items[:limit]}
