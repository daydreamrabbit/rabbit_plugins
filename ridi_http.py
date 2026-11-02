"""Shared, bounded Ridi request cache and rate-limit gate for plugin workers."""
import os
import re
import json
from urllib.parse import urlparse
import sqlite3
import tempfile
import time
import random
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests

# Administrative probes must not create a root-owned cache for the service.
_RUNTIME_USER = str(os.getuid()) if hasattr(os, 'getuid') else os.getlogin()
CACHE_PATH = Path(tempfile.gettempdir()) / f'rabbit-ridi-http-v1-{_RUNTIME_USER}.sqlite3'
CACHE_TTL = 3600
MIN_INTERVAL = 10.0
JITTER_MIN = 0.5
JITTER_MAX = 2.0


class RidiDeferred(requests.RequestException):
    """Do not interpret a deferred provider as an empty metadata result."""


def _retry_delay(value, failures, now):
    try:
        delay = float(value)
    except (TypeError, ValueError):
        try:
            delay = parsedate_to_datetime(value).timestamp() - now
        except (TypeError, ValueError, OverflowError):
            delay = min(900, 60 * 2 ** min(failures - 1, 4))
    return max(1, delay)


HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ko-KR,ko;q=0.9,en;q=0.8',
}


def _is_detail(url):
    return bool(re.fullmatch(r'/books/\d+/?', urlparse(url).path))


def has_product_payload(body):
    match = re.search(r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', body, re.S | re.I)
    if match:
        try:
            page = json.loads(match.group(1)).get('props', {}).get('pageProps', {})
            if page.get('adultBridge'):
                return False
        except (ValueError, AttributeError):
            pass
    return bool(re.search(r'var\s+bookDetail\s*=\s*\{|["\']@type["\']\s*:\s*["\']Book["\']|data-testid=["\']book-title["\']', body, re.I)
                or ('cell__BookDetailHomeHeader' in body and '"bookId"' in body))


def get(url, **kwargs):
    if cooling_down():
        raise RidiDeferred('리디 요청 제한 대기 중; 수집 보류')
    headers = dict(HEADERS)
    headers.update(kwargs.pop('headers', {}) or {})
    if 'RabbitPlugins' in headers.get('User-Agent', ''):
        headers['User-Agent'] = HEADERS['User-Agent']
    for attempt in range(2):
        current = dict(headers)
        if attempt:
            current.update({'Cache-Control': 'no-cache', 'Pragma': 'no-cache'})
        response = _get_once(url, headers=current, **kwargs)
        response.raise_for_status()
        if not _is_detail(url) or (response.url == url and has_product_payload(response.text)):
            return response
    raise requests.RequestException('리디 상세 응답에 상품 데이터가 없습니다. 기존 메타데이터 유지')


def _get_once(url, **kwargs):
    # One transaction serializes search/detail calls across processes, not
    # just threads. Waiters return promptly rather than piling up HTTP calls.
    db = sqlite3.connect(str(CACHE_PATH), timeout=1)
    try:
        db.execute('CREATE TABLE IF NOT EXISTS gate (id INTEGER PRIMARY KEY, next_at REAL, failures INTEGER)')
        db.execute('CREATE TABLE IF NOT EXISTS pages (url TEXT PRIMARY KEY, body TEXT, expires REAL)')
        db.execute('BEGIN IMMEDIATE')
        now = time.time()
        gate = db.execute('SELECT next_at, failures FROM gate WHERE id=1').fetchone()
        next_at, failures = gate or (0, 0)
        # Another worker can receive 429 after get() checked cooling_down().
        # Recheck under the transaction lock before returning even cached data.
        if failures and next_at > now:
            raise RidiDeferred('Ridi Retry-After 대기 중; 기존 메타데이터 유지')
        row = db.execute('SELECT body FROM pages WHERE url=? AND expires>?', (url, now)).fetchone()
        if row and _is_detail(url) and not has_product_payload(row[0]):
            db.execute('DELETE FROM pages WHERE url=?', (url,))
            row = None
        if row:
            response = requests.Response()
            response.status_code = 200
            response.url = url
            response.encoding = 'utf-8'
            response._content = row[0].encode('utf-8')
            response.headers['Content-Type'] = 'text/html; charset=utf-8'
            return response
        if next_at > now:
            if failures or next_at - now > MIN_INTERVAL + JITTER_MAX:
                raise RidiDeferred('Ridi Retry-After 대기 중; 기존 메타데이터 유지')
            time.sleep(next_at - now)
        try:
            response = requests.get(url, **kwargs)
        except requests.RequestException:
            db.execute('INSERT OR REPLACE INTO gate VALUES (1, ?, ?)', (time.time() + 15, failures + 1))
            db.commit()
            raise
        now = time.time()
        status = getattr(response, 'status_code', None)
        if status in (429, 503):
            failures += 1
            delay = _retry_delay(response.headers.get('Retry-After'), failures, now)
            if status == 429:
                delay = max(3600, delay)
            db.execute('INSERT OR REPLACE INTO gate VALUES (1, ?, ?)', (now + delay, failures))
            db.commit()
            response.raise_for_status()
        db.execute('INSERT OR REPLACE INTO gate VALUES (1, ?, 0)', (now + MIN_INTERVAL + random.uniform(JITTER_MIN, JITTER_MAX),))
        # Cache only successful HTML, never challenge/error pages or redirects
        # to login. Keeping at most 64 pages bounds disk growth.
        if (status == 200 and response.url == url
                and 'html' in response.headers.get('Content-Type', '').lower()
                and (not _is_detail(url) or has_product_payload(response.text))
                and len(response.content) <= 4_000_000
                and ('bookDetail' in response.text or '__NEXT_DATA__' in response.text
                     or 'application/ld+json' in response.text)):
            db.execute('INSERT OR REPLACE INTO pages VALUES (?, ?, ?)', (url, response.text, now + CACHE_TTL))
            db.execute('DELETE FROM pages WHERE expires <= ?', (now,))
            db.execute('DELETE FROM pages WHERE url NOT IN (SELECT url FROM pages ORDER BY expires DESC LIMIT 64)')
        db.commit()
        return response
    except sqlite3.OperationalError as error:
        if 'locked' in str(error).lower() or 'busy' in str(error).lower():
            raise RidiDeferred('Ridi 요청 처리 중; 잠시 후 다시 조회') from error
        raise requests.RequestException(f'리디 로컬 캐시 저장 오류: {error}') from error
    finally:
        db.close()


def cooling_down():
    if not CACHE_PATH.exists():
        return False
    try:
        with sqlite3.connect(str(CACHE_PATH), timeout=1) as db:
            row = db.execute('SELECT next_at, failures FROM gate WHERE id=1').fetchone()
            return bool(row and row[1] and row[0] > time.time())
    except sqlite3.OperationalError:
        return True


def invalidate_series_cache(titles, links):
    """Evict only matching response pages; never reset provider rate limits."""
    if not CACHE_PATH.exists():
        return
    from urllib.parse import parse_qs
    urls = set(re.findall(r'https?://[^\s,]+', '\n'.join(links)))
    names = {str(title).strip().casefold() for title in titles}
    with sqlite3.connect(str(CACHE_PATH), timeout=5) as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='pages'").fetchone()
        if not exists:
            return
        for (url,) in db.execute('SELECT url FROM pages').fetchall():
            query = parse_qs(urlparse(url).query).get('q', [''])[0].strip().casefold()
            if url in urls or (query and query in names):
                db.execute('DELETE FROM pages WHERE url=?', (url,))
