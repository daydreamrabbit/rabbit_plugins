# 도서 상세 탭에 필요한 파일 정보, 관련작품, 추천항목을 제공합니다.
import os

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
import unicodedata
import zipfile
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree as ET

from flask import has_request_context, request, session
from plugins.metadata.base import BaseMetadataProvider

PLUGIN_VERSION = '1.0.11'
REQUIRED_CORE_COMMIT = '9ba7c93'
SERIES_TYPES_BY_LIBRARY = {
    'manga': {'manga', 'manhwa', 'manhua', 'oel'},
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
            'document_volume_index', 'document_volume_count',
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
    result['date'] = _metadata_publication_date(entries)
    return result


@lru_cache(maxsize=2048)
def _read_comicinfo(path, file_format, artist, _file_mtime, _file_size):
    result = {
        'artist': artist,
        'format': '', 'count': None, 'volume': None, 'date': '', 'summary': '', 'localized_series': '',
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
            'summary': _comicinfo_summary(summary),
            'localized_series': values.get('localizedseries', ''),
        })
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


class RabbitPluginsMetadataProvider(BaseMetadataProvider):
    id = 'rabbit_plugins'
    name = 'Rabbit Plugins · 상세페이지'
    version = PLUGIN_VERSION
    is_searchable = False
    config_schema = [
        {'key': 'support_summary_html', 'label': 'ComicInfo 소개 이미지 표시', 'type': 'checkbox', 'default': True},
        {'key': 'series_db_path', 'label': 'Series.db 경로', 'type': 'text', 'default': ''},
        {'key': 'exclude_tags', 'label': '제외할 태그', 'type': 'text', 'default': ''},
        {'key': 'exclude_genres', 'label': '제외할 장르', 'type': 'text', 'default': ''},
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
        'initial_data': True,
    }
    dashboard_widget = None
    category_tab = None

    def search(self, db_type, query):
        return []

    def apply(self, db_type, book_id, item_data):
        return False, '상세 화면의 메타정보 탭에서 수정해 주세요.'

    def run_context_menu_action(self, db_type, action_id, context):
        if action_id == 'optimize_series_db':
            if not has_request_context() or session.get('role') != 'admin':
                return {'success': False, 'error': '관리자만 Series.db를 최적화할 수 있습니다.'}
            if db_type != 'general':
                return {'success': False, 'error': '일반 도서 설정에서 실행해 주세요.'}
            result = self._optimize_series_db()
            result['database_path'] = str(self._series_db_path())
            return result
        if action_id != 'save_detail_metadata':
            return {'success': False, 'error': '지원하지 않는 작업입니다.'}
        if not has_request_context() or session.get('role') != 'admin':
            return {'success': False, 'error': '관리자만 그림작가와 연재일을 수정할 수 있습니다.'}
        if db_type not in ('general', 'adult'):
            return {'success': False, 'error': '지원하지 않는 서재입니다.'}

        series_name = str(context.get('series_name') or '').strip()
        artist = str(context.get('cover_artist') or '').strip()
        start_date = str(context.get('publication_start_date') or '').strip()
        end_date = str(context.get('publication_end_date') or '').strip()
        if not series_name or len(artist) > 500:
            return {'success': False, 'error': '시리즈명 또는 그림작가 값을 확인해 주세요.'}
        for value in (start_date, end_date):
            if value:
                try:
                    date.fromisoformat(value)
                except ValueError:
                    return {'success': False, 'error': '연재일은 YYYY-MM-DD 형식으로 입력해 주세요.'}

        gateway = self.get_db_gateway(db_type)
        books = gateway.fetch_all(
            'SELECT id, file_path, file_format, file_mtime, file_size FROM books '
            'WHERE series_name = ? AND COALESCE(is_deleted, 0) = 0 ORDER BY id',
            (series_name,))
        if not books:
            return {'success': False, 'error': '수정할 시리즈를 찾을 수 없습니다.'}
        volumes = [(book, _comicinfo_metadata(book)) for book in books]
        count = next((info['count'] for _, info in volumes if info['count']), 0)
        start_books = [book for book, info in volumes if info['volume'] == 1]
        end_books = [book for book, info in volumes if count and info['volume'] == count]
        if start_date and not start_books:
            return {'success': False, 'error': 'Volume 1 도서를 찾지 못해 연재시작일을 저장할 수 없습니다.'}
        if end_date and not end_books:
            return {'success': False, 'error': 'Count와 같은 Volume 도서를 찾지 못해 연재종료일을 저장할 수 없습니다.'}
        if start_date and end_date and start_date != end_date and (
            {book['id'] for book in start_books} & {book['id'] for book in end_books}
        ):
            return {'success': False, 'error': '시작권과 완결권이 같은 작품은 연재시작일과 종료일을 같게 입력해 주세요.'}

        # 코어 공용 편집 API가 그림작가와 권별 연재일을 받지 않아 플러그인 액션으로 함께 저장한다.
        cover_artist_supported = _optional_column_sql(gateway, 'books', 'b', 'cover_artist') != 'NULL'
        artist_changed = any(str(info.get('artist') or '').strip() != artist for _, info in volumes)
        if cover_artist_supported:
            gateway.execute(
                'UPDATE books SET cover_artist = ? WHERE series_name = ? AND COALESCE(is_deleted, 0) = 0',
                (artist, series_name))
        for book in start_books:
            if start_date:
                gateway.execute('UPDATE books SET release_date = ? WHERE id = ?', (start_date, book['id']))
        for book in end_books:
            if end_date:
                gateway.execute('UPDATE books SET release_date = ? WHERE id = ?', (end_date, book['id']))
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
        items = []
        query = (
            'SELECT id, library_id, title, title_alias, series_name, series_alias, author, publisher, '
            'cover_image, cover_updated_at, file_format, total_pages, created_at FROM books '
            'WHERE library_id = ? AND COALESCE(is_deleted, 0) = 0 '
            'ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?'
        )
        for library_id, item_limit in selected:
            library = available[library_id]
            by_series = {}
            for offset in range(0, 1000, 100):
                rows = gateway.fetch_all(query, (library_id, 100, offset))
                for row in rows:
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
                'b.books_lv, b.genre, b.tags, b.release_date, b.total_pages, '
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
            visible_files = [{key: row[key] for key in (
                'id', 'file_size', 'file_mtime', 'created_at', 'books_lv', 'genre', 'tags',
                'release_date', 'total_pages', 'pages_read', 'is_completed', 'last_read_at'
            )} for row in files]
            count = next((item['count'] for item in volume_metadata if item['count']), comicinfo.get('count'))
            publication_dates = {
                'start': next((item['date'] for item in volume_metadata
                               if item['volume'] == 1 and item['date']), ''),
                'end': next((item['date'] for item in volume_metadata
                             if count and item['volume'] == count and item['date']), ''),
            }
            # 기존 저장 API의 WHERE series_name 범위와 일치시킨다(삭제 표시 행도 포함).
            scope = gateway.fetch_one(
                'SELECT COUNT(*) AS books, COUNT(DISTINCT library_id) AS libraries FROM books WHERE series_name = ?',
                (target['series_name'],)) if admin else None
            return {'success': True, 'files': visible_files, 'comicinfo': comicinfo, 'localized_series': localized_series, 'publication_dates': publication_dates, 'support_summary_html': support_summary_html, 'exclude_tags': exclude_tags, 'exclude_genres': exclude_genres, 'can_edit': admin, 'edit_scope': scope, 'library_name': library['name'] if library else '', 'library_id': target['library_id'], 'can_download': check_download_permission()}

        if mode == 'discovery':
            return self._discovery_data(
                gateway, target, permission, libraries, visible, check_book_rating_permission, db_type, limit)
        return self._similar_data(
            gateway, target, permission, libraries, visible, check_book_rating_permission, db_type, limit)

    def _series_db_path(self):
        from flask import current_app

        configured = str(self.get_plugin_config('general', {}).get('series_db_path') or '').strip()
        path = Path(configured).expanduser() if configured else Path(current_app.root_path) / 'db' / 'series.full.sqlite'
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
        local_books = []
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
            localized_series_sql = _optional_column_sql(gateway, 'books', 'b', 'localized_series')
            content_kind_sql = _optional_column_sql(gateway, 'libraries', 'l', 'content_kind')
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
                '(b.series_name IN (' + placeholders + ') OR b.series_alias IN (' + placeholders + ')'
                ' OR ' + pattern_conditions + ')' +
                permission + ' ORDER BY b.id',
                (*korean_titles, *korean_titles,
                 *[pattern for pattern in title_patterns for _ in range(2)], *libraries))
        books_by_title = {}
        for book in local_books:
            if not visible(book):
                continue
            for name in (book['series_name'], book['series_alias']):
                key = _series_title_key(name)
                if key:
                    books_by_title.setdefault(key, []).append(book)

        def local_item(series):
            if not series['native_key']:
                return None
            try:
                titles = json.loads(series['ko_titles'] or '[]')
            except (TypeError, ValueError):
                titles = []
            if not titles:
                titles = local_titles
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
        for kind, ids in relation_ids.items():
            items, seen = [], set()
            for series_id in ids:
                series = by_id.get(series_id)
                if not series:
                    continue
                item = local_item(series)
                key = work_key(item) if item else None
                if item and key not in seen:
                    items.append(item)
                    seen.add(key)
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
