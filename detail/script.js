// Rabbit Plugins 상세 화면을 렌더링하고 BookOasis의 읽기·편집 API에 연결합니다.
(async function () {
  'use strict';

  const root = container.querySelector('.ds');
  if (!root) return;

  // Mouse drag supplements the browser's native touch/trackpad scrolling.
  // Delegate on the stable root so asynchronous shelf renders keep working.
  let shelfDrag = null;
  let suppressShelfClick = false;
  root.addEventListener('dragstart', event => {
    if (event.target.closest('.ds-shelf')) event.preventDefault();
  });
  root.addEventListener('pointerdown', event => {
    suppressShelfClick = false;
    if (event.pointerType !== 'mouse' || event.button !== 0) return;
    const shelf = event.target.closest('.ds-shelf');
    if (!shelf || shelf.scrollWidth <= shelf.clientWidth) return;
    shelfDrag = { shelf, id: event.pointerId, x: event.clientX, left: shelf.scrollLeft, moved: false };
  });
  root.addEventListener('pointermove', event => {
    if (!shelfDrag || event.pointerId !== shelfDrag.id) return;
    if (!(event.buttons & 1)) { endShelfDrag(); return; }
    const dx = event.clientX - shelfDrag.x;
    if (!shelfDrag.moved && Math.abs(dx) < 6) return;
    if (!shelfDrag.moved) {
      shelfDrag.moved = true;
      shelfDrag.shelf.setPointerCapture(event.pointerId);
      shelfDrag.shelf.classList.add('is-dragging');
    }
    event.preventDefault();
    shelfDrag.shelf.scrollLeft = shelfDrag.left - dx;
  });
  function endShelfDrag() {
    if (!shelfDrag) return;
    const { shelf, id, moved } = shelfDrag;
    shelfDrag = null;
    suppressShelfClick = moved;
    shelf.classList.remove('is-dragging');
    if (shelf.hasPointerCapture(id)) shelf.releasePointerCapture(id);
  }
  ['pointerup', 'pointercancel', 'lostpointercapture'].forEach(name => root.addEventListener(name, endShelfDrag));
  root.addEventListener('pointerout', event => {
    if (shelfDrag && !shelfDrag.moved && !shelfDrag.shelf.contains(event.relatedTarget)) endShelfDrag();
  });
  root.addEventListener('click', event => {
    if (!suppressShelfClick || !event.target.closest('.ds-shelf') || event.detail === 0) return;
    suppressShelfClick = false;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);

  const $ = (selector) => root.querySelector(selector);
  const all = (selector) => [...root.querySelectorAll(selector)];
  const meta = { ...(context.meta || {}) };
  const books = (context.books || []).filter((book) => Number.isInteger(Number(book.id)) && Number(book.id) > 0);
  const libraryTypes = { general: '일반도서', adult: '성인도서', audiobook: '오디오북', video: '영상강좌' };
  const relationLabels = {
    main_story: '본편', original: '원작', prequel: '전작', sequel: '후속작',
    spin_off: '스핀오프', side_story: '외전', alternative: '다른 판본', adaptation: '각색',
    parody: '패러디', contains: '수록작', compilation: '모음집', cameo: '카메오',
    series: '시리즈', remake: '리메이크', crossover: '크로스오버', summary: '요약판',
    reboot: '리부트', expansion: '확장작', character_focus: '인물 중심작',
    uncollected: '미수록작', other: '기타',
  };
  const editableRelationTypes = new Set([
    'main_story', 'original', 'prequel', 'sequel', 'spin_off', 'side_story',
    'alternative', 'adaptation', 'other',
  ]);
  let type = context.type || 'general';
  let media = type === 'audiobook' || type === 'video';
  let unit = type === 'audiobook' ? '트랙' : type === 'video' ? '편' : '권';
  let libraryName = '';
  let libraryId = context.libraryId || null;
  let canEdit = false;
  let editScope = null;
  let dirty = false;
  let saving = false;
  let summaryHtmlEnabled = false;
  let excludedGenres = new Set();
  let excludedTags = new Set();
  let detailDataReady = false;
  let detailDataFailed = false;
  let contentKind = '';
  let metadataSources = [];
  let metadataAutoEnabled = false;
  let metadataHold = {};
  let metadataFields = [];
  let manualEmptyFields = new Set();
  let metadataRefreshTimer = null;
  let metadataRefreshAttempts = 0;
  let metadataRefreshInFlight = false;
  let metadataRefreshSequence = 0;
  let detailDataSequence = 0;
  // 상세 번들이 처음 그려지는 순간에도 다운로드 링크를 만들 수 있도록
  // 허용을 기본값으로 두고, files 응답에서 명시적으로 거부된 경우만 막습니다.
  let canDownload = true;
  let metadataSearchLoading = false;
  let metadataSearchResults = [];
  // Cover files keep the same path when they are replaced.  Change this
  // revision after a metadata/cover update so the browser requests the new
  // bytes instead of displaying a cached image.
  let coverRevision = Date.now();
  let discoveryLoaded = false;
  let discoveryAttempted = false;
  let loadingDiscovery = false;
  let discoveryRefreshQueued = false;
  let discoveryLoadedKey = '';
  let discoveryRenderedSignature = '';
  let discoveryStatusLabel = '';
  let loadingCollections = false;
  let noticeTimer;
  let fields = [
    ['series_alias', '표시 제목'],
    ['author', '작가'],
    ['cover_artist', '그림작가'],
    ['translator', '번역가'],
    ['publisher', '출판사'],
    ['publication_start_date', '연재시작일'],
    ['publication_end_date', '연재종료일'],
    ['manual_chapter_count', '회차 수'],
    ['books_lv', '연령등급'],
    ['isbn', 'ISBN / Web ID'],
    ['genre', '장르'],
    ['tags', '태그'],
    ['link', '관련 링크'],
  ];

  // The bundle is evaluated before the state module import resolves. Use the
  // type supplied by the core to render the first useful frame synchronously;
  // this prevents the detail container from appearing as an empty page.
  if (['general', 'adult', 'audiobook', 'video'].includes(context.type)) {
    type = context.type;
    media = type === 'audiobook' || type === 'video';
    unit = type === 'audiobook' ? '트랙' : type === 'video' ? '편' : '권';
  }

  const title = (book) => {
    const raw = [book.title_alias, book.metadata_title, book.title]
      .map((value) => String(value || '').trim()).find(Boolean) || '제목 없음';
    // Metadata providers often prefix the display title with a publisher or
    // imprint tag such as "[미즈]". Keep the metadata title, but hide only
    // leading bracket tags in the volume and chapter cards.
    const withoutDescription = raw.split(/\s*작품\s*소개\s*[:：]/u, 1)[0].trim();
    return withoutDescription.replace(/^(?:(?:\[[^\]]+\]|\{[^}]+\})\s*)+/u, '').trim() || raw;
  };
  const fileDisplayLabel = (book, index = 0, options = {}) => {
    // 권별 Title을 그대로 표시합니다. 권·화 그룹 이름과 정렬 번호는
    // 별도로 파일명에서 구하므로 부제에 숫자가 없어도 순서를 유지합니다.
    if (options.unit !== 'volume' && [book?.title_alias, book?.metadata_title]
      .some((value) => String(value || '').trim())) return title(book);
    const candidates = [book?.file_path, book?.title_alias, book?.title]
      .map((value) => String(value || '').replace(/\\/g, '/').split('/').pop() || '')
      .filter(Boolean);
    const sourceSuffix = /\s*\((?:리디|교보|예스(?:24)?|네이버|카카오|마나부|스캔|웹툰|레진|익헨|마블|일러스트|펌|확인)\)\s*(?:#\d+)?$/iu;
    const technicalSuffix = /\s*\[(?:\d{3,5}\s*[x×](?:\s*\d{2,5})?|\d{3,5}\s*[pP])\]\s*/gu;
    const volumeOnlyPattern = /((?:제\s*)?\d+(?:\.\d+)?\s*권(?:\s*\([^)]*\))*)/iu;
    const volumePattern = /((?:제\s*)?\d+(?:\.\d+)?\s*(?:권|화|회|편)(?:\s*\([^)]*\))*)/iu;
    for (const candidate of candidates) {
      const base = candidate.replace(/\.(?:cbz|zip|cbr|rar|epub|pdf|txt|html?|mobi|azw3?|mp3|m4b|mp4)$/iu, '').replace(sourceSuffix, '').replace(technicalSuffix, ' ').trim();
      if (options.unit === 'volume') {
        const volumeMatch = base.match(volumeOnlyPattern);
        if (volumeMatch?.[1]) return volumeMatch[1].replace(/\s+/g, ' ').trim();
      }
      const match = base.match(volumePattern);
      if (match?.[1]) return match[1].replace(/\s+/g, ' ').trim();
    }
    const chapter = chapterNumber(book);
    if (chapter > 0 && chapterOnlyMode()) return numberText(chapter) + '화';
    return title(book) || String(index + 1).padStart(2, '0');
  };
  const num = (value) => Number.isFinite(Number(value)) ? Math.max(0, Number(value)) : 0;
  const completed = (book) => Number(media
    ? (book.is_track_completed ?? book.is_episode_completed ?? book.is_completed)
    : book.is_completed) === 1;
  const progress = (book) => completed(book)
    ? 100
    : media
      ? Math.min(100, num(book.track_progress_pct ?? book.episode_progress_pct))
      : num(book.total_pages)
        ? Math.min(100, Math.round(num(book.pages_read) / num(book.total_pages) * 100))
        : 0;
  const reading = (book) => !completed(book) && (media ? progress(book) > 0 : num(book.pages_read) > 0);
  const continueBook = () => (
    (media && books.find((book) => Number(book.id) === Number(meta.current_track_id ?? meta.current_episode_id) && !completed(book)))
    || books.filter(reading).sort((a, b) => String(b.last_read_at || '').localeCompare(String(a.last_read_at || '')))[0]
    || books.find((book) => !completed(book))
    || books[0]
  );

  function metadataNumber(book, key) {
    const value = Number(book?.[key]);
    return Number.isFinite(value) && value > 0 ? value : 0;
  }

  function volumeNumber(book) {
    return metadataNumber(book, 'volume') || metadataNumber(book, 'document_volume_index');
  }

  function seriesVolumeSortNumber(book) {
    // Use the filename-derived label first: it preserves decimal volumes such
    // as 4.5 even when a scanned database field contains a rounded integer.
    const label = fileDisplayLabel(book, 0, { unit: 'volume' });
    const match = String(label || '').match(/(?:제\s*)?(\d+(?:\.\d+)?)\s*권/iu);
    if (match) return Number(match[1]) || 0;
    return volumeNumber(book);
  }

  function compareSeriesVolumeBooks(a, b) {
    const aVolume = seriesVolumeSortNumber(a);
    const bVolume = seriesVolumeSortNumber(b);
    if (aVolume > 0 && bVolume > 0 && aVolume !== bVolume) return aVolume - bVolume;
    if (aVolume > 0 && !bVolume) return -1;
    if (!aVolume && bVolume > 0) return 1;
    return 0;
  }

  function seriesCardNumber(book, index) {
    if (chapterOnlyMode() && chapterNumber(book) > 0) {
      return numberText(chapterNumber(book)) + '화';
    }
    const volume = media ? 0 : seriesVolumeSortNumber(book);
    return String(volume > 0 ? numberText(volume) : index + 1).padStart(2, '0');
  }

  function chapterNumber(book) {
    return metadataNumber(book, 'number') || metadataNumber(book, 'chapter_number');
  }

  function numberText(value) {
    return Number.isInteger(Number(value)) ? String(Number(value)) : String(value);
  }

  function hasChapterMetadata() {
    return books.some((book) => chapterNumber(book) > 0);
  }

  function hasVolumeMetadata() {
    return books.some((book) => volumeNumber(book) > 0);
  }

  function chapterOnlyMode() {
    return !media && hasChapterMetadata() && !hasVolumeMetadata();
  }

  function groupedVolumeMode() {
    return !media && hasChapterMetadata() && hasVolumeMetadata();
  }

  function volumeGroups() {
    const groups = new Map();
    books.forEach((book, index) => {
      const volume = seriesVolumeSortNumber(book);
      const key = volume > 0 ? String(volume) : 'book:' + String(book.id || index);
      if (!groups.has(key)) groups.set(key, { volume, books: [] });
      groups.get(key).books.push(book);
    });
    return [...groups.values()].sort((a, b) => {
      if (!a.volume) return 1;
      if (!b.volume) return -1;
      return a.volume - b.volume;
    });
  }

  function uniqueChapterNumbers() {
    return [...new Set(books.map(chapterNumber).filter((value) => value > 0))].sort((a, b) => a - b);
  }

  function chapterCoverage() {
    const groups = groupedVolumeMode() ? volumeGroups() : [{ books }];
    let present = 0;
    let expected = 0;
    let missing = false;
    for (const group of groups) {
      const numbers = [...new Set(group.books.map(chapterNumber).filter((value) => value > 0))]
        .sort((a, b) => a - b);
      if (!numbers.length) continue;
      const max = numbers[numbers.length - 1];
      present += numbers.length;
      if (!Number.isInteger(max)) continue;
      expected += max;
      if (numbers[0] !== 1 || numbers.length < max) missing = true;
    }
    return { present, expected, missing };
  }

  function chapterTargetCount() {
    const count = Number(meta.comicinfo_count) || 0;
    if (chapterOnlyMode()) return count;
    if (!groupedVolumeMode() || !count) return 0;
    const maxVolume = Math.max(...books.map(volumeNumber).filter((value) => value > 0), 0);
    // ComicInfo Count normally means total volumes when it equals the largest
    // Volume. In that case it cannot prove that all chapters in the volume
    // exist. A different Count is treated as an explicit chapter total.
    return count === maxVolume ? 0 : count;
  }

  function split(value) {
    return [...new Set(String(value || '').split(/[,;|\n]/).map((part) => part.trim()).filter((part) => part && part !== '-'))];
  }

  function distinctGenreTags(genre, tags) {
    const genres = split(genre);
    const genreKeys = new Set(genres.map((term) => term.toLocaleLowerCase()));
    const cleanTags = split(tags).filter((term) => !genreKeys.has(term.toLocaleLowerCase()));
    return { genre: genres.join(', '), tags: cleanTags.join(', ') };
  }

  {
    const cleanTerms = distinctGenreTags(meta.genre, meta.tags);
    meta.genre = cleanTerms.genre;
    meta.tags = cleanTerms.tags;
  }

  function excludedTerms(value) {
    return new Set(String(value || '').split(',').map((term) => term.trim().toLocaleLowerCase()).filter(Boolean));
  }

  function triggerDownload(downloadUrl) {
    notify('다운로드를 시작했습니다.');
    const link = document.createElement('a');
    link.href = downloadUrl;
    link.setAttribute('download', '');
    link.hidden = true;
    document.body.append(link);
    requestAnimationFrame(() => {
      link.click();
      setTimeout(() => link.remove(), 0);
    });
  }

  function fileFormatBadge(value, book = null, options = {}) {
    const format = String(value || '').trim().toUpperCase();
    if (!format) return null;
    const kind = ['CBZ', 'EPUB', 'PDF', 'ZIP'].includes(format) ? format.toLowerCase() : 'other';
    // 원본 파일이 없는 IMGDIR을 제외한 모든 도서 포맷은 코어 다운로드 API로 전달한다.
    // 실제 접근 가능 여부는 서버의 사용자 다운로드/서재/연령 권한 검사가 최종 판정한다.
    const downloadable = Boolean(book?.id)
      && canDownload
      && format !== 'IMGDIR';
    const asAnchor = downloadable && options.anchor === true;
    const badge = node(asAnchor ? 'a' : 'span', 'ds-file-format ds-file-format-' + kind
      + (downloadable ? ' ds-file-format-download' : ''), format);
    if (downloadable) {
      const downloadUrl = '/api/media/books/' + encodeURIComponent(book.id)
        + '/download?type=' + encodeURIComponent(type);
      badge.setAttribute('data-role', 'detail-download-link');
      badge.setAttribute('aria-label', format + ' 파일 다운로드');
      badge.title = format + ' 파일 다운로드';
      if (asAnchor) {
        badge.href = downloadUrl;
        badge.setAttribute('download', '');
        return badge;
      }
      badge.setAttribute('role', 'button');
      badge.tabIndex = 0;
      const startDownload = (event) => {
        event.preventDefault();
        event.stopPropagation();
        triggerDownload(downloadUrl);
      };
      badge.addEventListener('click', startDownload);
      badge.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') startDownload(event);
      });
    }
    return badge;
  }

  function formatDate(value) {
    if (!value) return '—';
    const text = String(value).trim();
    if (/^\d{4}-\d{2}-\d{2}(?:$|[T ])/.test(text)) return text.slice(0, 10);
    const date = new Date(text);
    return Number.isNaN(date.getTime()) ? '—' : date.toISOString().slice(0, 10);
  }

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text != null) element.textContent = String(text);
    return element;
  }

  function icon(name) {
    const element = node('i', 'fa-solid fa-' + name);
    element.setAttribute('aria-hidden', 'true');
    return element;
  }

  function canListenBook(book) {
    return !media && ['general', 'adult'].includes(String(type).toLowerCase())
      && typeof window.canListen === 'function'
      && window.canListen(book?.file_format);
  }

  function listenToBook(book) {
    if (!book?.id || !canListenBook(book)) return;
    if (typeof window.openListen !== 'function') {
      return notify('음성 듣기 기능을 연결하지 못했습니다. 페이지를 새로고침해 주세요.', true);
    }
    window.openListen(Number(book.id), type);
  }

  function listenButton(book, className, label) {
    if (!canListenBook(book)) return null;
    const button = node('button', className);
    button.type = 'button';
    button.title = label;
    button.setAttribute('aria-label', label);
    button.append(icon('headphones'));
    button.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      listenToBook(book);
    });
    return button;
  }

  function safeUrl(value, cover = false) {
    let raw = String(value || '').trim();
    if (!raw) return '';
    if (cover && !/^(https?:|\/\/)/i.test(raw) && !/^\/api\//i.test(raw)
        && (!raw.startsWith('/') || /^\/covers\//i.test(raw))) {
      const queryIndex = raw.indexOf('?');
      const suffix = queryIndex >= 0 ? raw.slice(queryIndex) : '';
      let path = queryIndex >= 0 ? raw.slice(0, queryIndex) : raw;
      path = path.replace(/^[\/\\]+/, '').replace(/^covers[\/\\]/i, '');
      raw = '/covers/' + path.split(/[\/\\]/).map((part) => encodeURIComponent(part)).join('/') + suffix;
    }
    try {
      const url = new URL(raw, location.origin);
      if (cover && url.origin === location.origin && /^\/covers\//i.test(url.pathname)) {
        url.searchParams.set('rabbit_cover', String(coverRevision));
      }
      return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
    } catch {
      return '';
    }
  }

  // The grid remains mounted underneath the detail view. Tell the core that
  // its cached series rows are stale whenever this view changes metadata or a
  // cover. The core defers the network request while detail is visible, then
  // reloads the same loaded page range (and restores its scroll position) as
  // soon as the user returns to the list.
  function invalidateSeriesList() {
    if (typeof window.invalidateBookListAfterScan === 'function') {
      window.invalidateBookListAfterScan();
    }
  }

  const siteNames = Object.freeze({
    'ridibooks.com': '리디북스',
    'ridi.com': '리디',
    'yes24.com': 'YES24',
    'aladin.co.kr': '알라딘',
    'kyobobook.co.kr': '교보문고',
    'booklive.jp': 'BookLive',
    'amazon.co.jp': 'Amazon',
    'amazon.com': 'Amazon',
    'kakao.com': '카카오',
    'naver.com': '네이버',
  });

  const siteFavicons = Object.freeze({
    // Some providers block /favicon.ico or do not publish it at the search
    // host.  Google's favicon endpoint keeps the source icon consistent.
    'ridibooks.com': 'https://www.google.com/s2/favicons?domain=ridibooks.com&sz=32',
    'ridi.com': 'https://www.google.com/s2/favicons?domain=ridibooks.com&sz=32',
    'series.naver.com': 'https://www.google.com/s2/favicons?domain=series.naver.com&sz=32',
    'naver.com': 'https://www.google.com/s2/favicons?domain=series.naver.com&sz=32',
    'kyobobook.co.kr': 'https://www.google.com/s2/favicons?domain=kyobobook.co.kr&sz=32',
    'yes24.com': 'https://www.google.com/s2/favicons?domain=yes24.com&sz=32',
  });

  function siteInfo(link) {
    const url = new URL(link);
    const host = url.hostname.replace(/^www\./i, '').toLocaleLowerCase();
    const domain = [...Object.keys(siteNames), 'mangabaka.org', 'munpia.com', 'novelpia.com']
      .find((name) => host === name || host.endsWith('.' + name));
    const service = ['series.naver.com', 'comic.naver.com', 'page.kakao.com', 'webtoon.kakao.com']
      .find((name) => host === name || host.endsWith('.' + name));
    const faviconDomain = Object.keys(siteFavicons).find((name) => host === name || host.endsWith('.' + name));
    return {
      key: service || (domain === 'ridi.com' ? 'ridibooks.com'
        : ['naver.com', 'kakao.com'].includes(domain) ? host : domain || host),
      label: siteNames[domain] || host,
      favicon: siteFavicons[faviconDomain]
        || 'https://www.google.com/s2/favicons?domain=' + encodeURIComponent(host) + '&sz=32',
    };
  }

  function seriesSiteLinks(bookRows, seriesLinks) {
    const selected = new Map();
    const add = (value, book = {}) => {
      const groups = new Map();
      for (const raw of String(value || '').split(/[,;|\n]+/)) {
        const link = safeUrl(raw.trim());
        if (!link) continue;
        const key = siteInfo(link).key;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(link);
      }
      const filename = String(book.file_path || '').replace(/\\/g, '/').split('/').pop() || '';
      const codes = new Set((filename.match(/[ES]\d{6,}/gi) || [])
        .map((code) => code.toUpperCase()));
      for (const [key, links] of groups) {
        if (selected.has(key)) continue;
        const exact = links.find((link) => (link.match(/[ES]\d{6,}/gi) || [])
          .some((code) => codes.has(code.toUpperCase())));
        selected.set(key, exact || links[0]);
      }
    };
    // Display order in the carousel can be reversed. Shortcuts always start
    // with volume 1 (or the standalone file), independently of that order.
    const ordered = [...bookRows].sort((a, b) => compareSeriesVolumeBooks(a, b)
      || chapterNumber(a) - chapterNumber(b));
    for (const book of ordered) add(book.link, book);
    add(seriesLinks);
    return [...selected.values()];
  }

  function siteFavicon(link, label) {
    let info;
    try {
      info = siteInfo(link);
    } catch {
      return icon('link');
    }
    const image = node('img', 'ds-link-favicon');
    image.src = info.favicon;
    image.alt = '';
    image.title = label || info.label;
    image.loading = 'lazy';
    image.decoding = 'async';
    image.referrerPolicy = 'no-referrer';
    image.addEventListener('error', () => {
      const fallback = icon('link');
      fallback.classList.add('ds-link-favicon-fallback');
      image.replaceWith(fallback);
    }, { once: true });
    return image;
  }

  function renderSummary(value) {
    const target = $('[data-summary]');
    const source = String(value || '')
      .replace(/^\s*<(h[1-6]|p|div|strong|b)\b[^>]*>\s*작품\s*소개\s*[:：]?\s*<\/\1>\s*/i, '')
      .replace(/^\s*(?:#{1,6}\s*)?작품\s*소개(?:\s*[:：]\s*|\s+|$)/, '');
    const parsed = new DOMParser().parseFromString(source, 'text/html');
    parsed.body.querySelectorAll('script, style, iframe, object, embed, svg').forEach((element) => element.remove());
    target.replaceChildren();

    if (!summaryHtmlEnabled) {
      target.textContent = parsed.body.textContent
        .replace(/<img\b[^>]*\/?\s*>/gi, '')
        .trim() || '등록된 책 소개가 없습니다.';
      return;
    }

    const append = (element) => {
      if (element.nodeType === Node.TEXT_NODE) {
        target.append(document.createTextNode(element.textContent || ''));
      } else if (element.nodeType === Node.ELEMENT_NODE) {
        if (element.tagName === 'IMG') {
          const source = element.getAttribute('src') || '';
          const url = safeUrl(source.startsWith('//') ? 'https:' + source : source);
          if (!url) return;
          const image = document.createElement('img');
          image.className = 'ds-summary-image';
          image.src = url;
          image.alt = element.getAttribute('alt') || '';
          image.loading = 'eager';
          image.decoding = 'async';
          image.referrerPolicy = 'no-referrer';
          image.addEventListener('load', fitSummary, { once: true });
          image.addEventListener('error', fitSummary, { once: true });
          target.append(image);
          return;
        }
        if (element.tagName === 'BR') target.append(document.createElement('br'));
        else [...element.childNodes].forEach(append);
      }
    };
    [...parsed.body.childNodes].forEach(append);
    if (!target.hasChildNodes()) target.textContent = '등록된 책 소개가 없습니다.';
  }

  function parseRating(value) {
    if (value == null) return null;
    const text = String(value).trim().toLocaleLowerCase();
    if (!text || ['-', 'none', 'null'].includes(text)) return null;
    const compact = text.replace(/[\s_-]+/g, '');
    const numeric = Number(text);
    if (Number.isFinite(numeric)) {
      // BookOasis uses 19 and 20 for the distinct ComicInfo/Kavita levels
      // "성인망가" (R18+) and "포르노". Preserve those levels when the raw
      // books_lv string is not present in a plugin context.
      if (numeric >= 20) return { level: 20, rank: 4, label: '포르노', specific: true };
      if (numeric >= 19) return { level: 19, rank: 3, label: '성인망가', specific: true };
      if (numeric >= 18) return { level: 18, rank: 2, label: '18세 이용가', specific: false };
      if (numeric >= 15) return { level: 15, rank: 1, label: '15세 이용가', specific: false };
      if (numeric >= 0) return { level: 0, rank: 0, label: '전체 이용가', specific: false };
      return null;
    }
    if (['porn', 'porno', 'pornography', 'pornographic', '포르노', '포르노그래피'].includes(compact)) {
      return { level: 20, rank: 4, label: '포르노', specific: true };
    }
    if (['adultonly18+', 'adultsonly18+', 'adultonly18', 'adultsonly18'].includes(compact)) {
      return { level: 20, rank: 4, label: '포르노', specific: true };
    }
    if (['adultonly', 'adultsonly'].includes(compact)) {
      return { level: 18, rank: 2, label: '18세 이용가', specific: true };
    }
    if (['성인망가', 'r18', 'r18+', 'x18+', 'xrated'].includes(compact) || /(?:r18|x-rated|청소년관람불가|성인망가)/i.test(text)) {
      return { level: 19, rank: 3, label: '성인망가', specific: true };
    }
    if (compact === 'm') return { level: 18, rank: 2, label: '18세 이용가', specific: true };
    if (['ma15', 'ma15+', 'm15', 'm15+'].includes(compact) || /15세\s*이상|청소년/i.test(text)) {
      return { level: 15, rank: 1, label: '15세 이용가', specific: true };
    }
    if (['everyone', 'general', 'all', 'allages', '전체', '전체이용가', '일반', '전연령', '0세'].includes(compact) || /전\s*연령/i.test(text)) {
      return { level: 0, rank: 0, label: '전체 이용가', specific: true };
    }
    if (['18+', '18세', '18세이상', 'adult', '성인', '19금'].includes(compact) || /(?:^|\D)18(?:\+|세)(?:\D|$)|x-rated|성인/i.test(text)) {
      return { level: 18, rank: 2, label: '18세 이용가', specific: true };
    }
    if (/(?:^|\D)15(?:\+|세)(?:\D|$)/i.test(text)) return { level: 15, rank: 1, label: '15세 이용가', specific: true };
    return null;
  }

  function getContentRating(extraRows = []) {
    const candidates = [];
    const add = (source) => {
      if (!source || typeof source !== 'object') return;
      for (const [key, labelKey] of [
        ['content_rating_level', 'content_rating_label'],
        ['age_rating_level', 'age_rating_label'],
        ['age_rating', 'age_rating_label'],
        ['books_lv', 'content_rating_label'],
      ]) {
        const parsed = parseRating(source[key]);
        if (!parsed) continue;
        const rawAgeValue = ['books_lv', 'age_rating'].includes(key) && typeof source[key] === 'string';
        candidates.push({
          ...parsed,
          label: parsed.specific ? parsed.label : source[labelKey] || parsed.label,
          specific: parsed.specific || rawAgeValue,
          raw: key === 'books_lv' ? source[key] : '',
        });
      }
    };
    [meta, ...books, ...extraRows].forEach(add);
    if (!candidates.length) return null;
    return candidates.reduce((best, candidate) => (
      !best || candidate.rank > best.rank || (candidate.rank === best.rank && candidate.specific && !best.specific)
        ? candidate
        : best
    ), null);
  }

  function contentRatingBadge(rating) {
    const label = String(rating?.label || '').trim();
    const compact = label.toLocaleLowerCase().replace(/[\s_-]+/g, '');
    if (/포르노|porn|adultonly18/.test(compact)) return { kind: 'porn', label: '포르노' };
    if (/성인망가|r18|x18|xrated|청소년관람불가/.test(compact)) return { kind: 'manga', label: '성인망가' };
    if (/15세|15\+|ma15|m15|청소년/.test(compact) || Number(rating?.level) === 15) {
      return { kind: 'teen', label: '15세 이용가' };
    }
    if (/18세|18\+|adult|성인|19금/.test(compact) || Number(rating?.level) >= 18) {
      return { kind: 'adult', label: '18세 이용가' };
    }
    return { kind: 'general', label: '전체 이용가' };
  }

  function editContentRatingValue() {
    const rating = getContentRating();
    if (!rating) return String(meta.books_lv ?? '').trim();
    const badge = contentRatingBadge(rating);
    return { general: 'everyone', teen: 'ma15+', adult: 'm', manga: 'r18', porn: 'adult only 18+' }[badge.kind] || '';
  }

  function applyContentRating(rows = []) {
    const rating = getContentRating(rows);
    if (!rating) return;
    const current = getContentRating();
    if (!current || rating.rank > current.rank || (rating.rank === current.rank && rating.specific && !current.specific)) {
      meta.content_rating_level = rating.level;
      meta.content_rating_label = rating.label;
      if (rating.raw) meta.books_lv = rating.raw;
    }
  }

  function hasStoredSummary(value) {
    const text = String(value || '').trim();
    return Boolean(text && text !== '-' && text !== '등록된 설명이 없습니다.');
  }

  function renderAppearance() {
    const coverSrc = safeUrl(meta.cover_image || books[0]?.cover_image, true);
    const bannerSrc = safeUrl(meta.banner_image, true);
    const banner = $('[data-banner]');
    const bannerImage = banner.querySelector('img');
    const colorscapeImage = $('[data-colorscape-image]');
    root.dataset.colorscape = String(Boolean(coverSrc));
    if (colorscapeImage.dataset.url !== coverSrc) {
      colorscapeImage.dataset.url = coverSrc;
      if (!coverSrc) {
        colorscapeImage.removeAttribute('src');
      } else {
        colorscapeImage.onerror = () => {
          if (colorscapeImage.dataset.url !== coverSrc) return;
          colorscapeImage.dataset.url = '';
          colorscapeImage.removeAttribute('src');
          root.dataset.colorscape = 'false';
        };
        colorscapeImage.src = coverSrc;
      }
    }
    root.dataset.hasBanner = String(Boolean(bannerSrc));
    document.getElementById('book-detail-view')?.classList.toggle('rabbit-plugins-has-banner', Boolean(bannerSrc));

    if (!bannerSrc) {
      banner.hidden = true;
      bannerImage.removeAttribute('src');
      bannerImage.dataset.url = '';
    } else {
      banner.hidden = false;
      if (bannerImage.dataset.url !== bannerSrc) {
        bannerImage.dataset.url = bannerSrc;
        bannerImage.onerror = () => {
          if (bannerImage.dataset.url !== bannerSrc) return;
          banner.hidden = true;
          root.dataset.hasBanner = 'false';
          document.getElementById('book-detail-view')?.classList.remove('rabbit-plugins-has-banner');
        };
        bannerImage.src = bannerSrc;
      }
    }
  }

  function notify(message, error = false) {
    const notice = $('[data-notice]');
    notice.textContent = message;
    notice.dataset.error = String(error);
    notice.hidden = false;
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { notice.hidden = true; }, error ? 7000 : 4000);
  }

  async function request(url, options = {}) {
    let lastError = null;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        const response = await fetch(url, {
          credentials: 'same-origin',
          cache: 'no-store',
          signal: AbortSignal.timeout(60000),
          ...options,
        });
        const body = await response.text();
        let data;
        try {
          data = JSON.parse(body);
        } catch {
          throw new Error('서버 응답을 읽지 못했습니다. 로그인 상태를 확인해 주세요. (HTTP ' + response.status + ')');
        }
        if (!response.ok || !data.success) throw new Error(data.error || data.message || '요청 실패 (' + response.status + ')');
        return data;
      } catch (error) {
        lastError = error;
        if (attempt === 0 && ['GET', 'HEAD'].includes(String(options.method || 'GET').toUpperCase()) && /HTTP (?:502|503|504)|요청 실패 \((?:502|503|504)\)/.test(error.message || '')) {
          await new Promise((resolve) => setTimeout(resolve, 350));
          continue;
        }
        break;
      }
    }
    throw lastError || new Error('요청에 실패했습니다.');
  }

  const metadataValue = (field) => {
    const key = {
      cover: 'cover_image',
      release_date: 'publication_dates',
    }[field] || field;
    if (key === 'publication_dates') {
      return meta.publication_dates?.start || meta.publication_dates?.end || '';
    }
    if (key === 'cover_image') return meta.cover_image || books.find((book) => book.cover_image)?.cover_image || '';
    return meta[key] || '';
  };

  function metadataNeedsRefresh() {
    if (!metadataAutoEnabled || !metadataFields.length) return false;
    // `has_metadata` is the server's completion signal.  A selected field
    // such as localized_series can legitimately remain empty when the chosen
    // providers do not supply it.  Treating that optional value as an active
    // collection would keep polling an already completed detail forever.
    if (Number(meta.has_metadata) === 1) return false;
    // Provider series titles do not replace each file's embedded Title.
    return metadataFields.some((field) => {
      if (field === 'title') return false;
      const value = String(metadataValue(field) || '').trim();
      return !value || value === '-' || value === '등록된 설명이 없습니다.';
    });
  }

  function metadataSnapshot() {
    const keys = ['series_alias', 'localized_series', 'author', 'cover_artist', 'translator', 'publisher',
      'summary', 'genre', 'tags', 'isbn', 'uci', 'link', 'cover_image', 'banner_image',
      'publication_status', 'books_lv', 'release_date', 'metadata_locked', 'teams', 'locations', 'characters'];
    return JSON.stringify({
      meta: keys.map((key) => String(meta[key] || '')),
      dates: meta.publication_dates || {},
      books: books.map((book) => [book.id, book.cover_image || '', book.release_date || '',
        book.title_alias || '', book.metadata_title || '', book.title || '', book.link || '']),
    });
  }

  function stopMetadataRefresh() {
    if (metadataRefreshTimer) clearInterval(metadataRefreshTimer);
    metadataRefreshTimer = null;
    metadataRefreshInFlight = false;
  }

  async function refreshMetadataFromServer({ force = false } = {}) {
    const sequence = ++metadataRefreshSequence;
    const beforeDiscoveryKey = discoveryInputKey();
    const params = new URLSearchParams({
      type,
      series: context.seriesName,
      library_id: context.libraryId || libraryId || 'all',
      representative_book_id: books[0]?.id || meta.id || '',
    });
    const latest = await request('/api/media/detail?' + params);
    if (!root.isConnected || sequence !== metadataRefreshSequence || !latest.meta) return false;
    const before = metadataSnapshot();
    Object.assign(meta, latest.meta);
    if (Array.isArray(latest.books)) {
      books.splice(0, books.length, ...latest.books);
    }
    const beforeHold = JSON.stringify(metadataHold);
    await loadDetailData(false);
    if (!root.isConnected || sequence !== metadataRefreshSequence) return false;
    const changed = before !== metadataSnapshot() || beforeHold !== JSON.stringify(metadataHold);
    if (!changed && !force) return false;
    coverRevision += 1;
    renderMetadataRefresh();
    invalidateSeriesList();
    // 표지·설명처럼 추천 기준과 무관한 필드가 바뀔 때는 추천 API를
    // 다시 호출하지 않습니다. 작가·장르·태그·제목이 실제로 바뀐 경우에만
    // 새 후보를 조회하고, 기존 카드는 조회가 끝날 때까지 유지합니다.
    if (beforeDiscoveryKey !== discoveryInputKey()) loadDiscovery({ refresh: true });
    return true;
  }

  function startMetadataRefresh() {
    stopMetadataRefresh();
    if (!metadataNeedsRefresh()) return;
    metadataRefreshAttempts = 0;
    const poll = async () => {
      if (!root.isConnected || !metadataNeedsRefresh()) {
        stopMetadataRefresh();
        return;
      }
      if (metadataRefreshInFlight || metadataRefreshAttempts >= 30) {
        if (metadataRefreshAttempts >= 30) stopMetadataRefresh();
        return;
      }
      metadataRefreshAttempts += 1;
      metadataRefreshInFlight = true;
      try {
        await refreshMetadataFromServer();
      } catch {
        // The next poll retries while the automatic collector is still running.
      } finally {
        metadataRefreshInFlight = false;
        if (!root.isConnected || !metadataNeedsRefresh() || metadataRefreshAttempts >= 30) {
          stopMetadataRefresh();
        }
      }
    };
    metadataRefreshTimer = setInterval(poll, 2000);
    poll();
  }

  function apiUrl(mode) {
    const bookId = media ? meta.id : books[0]?.id;
    const params = new URLSearchParams({ type, book_id: String(bookId || ''), mode, limit: '18' });
    if (mode === 'discovery') params.set('graph', '1');
    return '/api/media/dashboard/widgets/' + encodeURIComponent(pluginId) + '/data?' + params;
  }

  // 추천/관련 작품은 작가·장르·태그·작품 식별 정보가 바뀔 때만 다시
  // 조회합니다. 자동 메타데이터 수집이 표지나 설명을 채우는 동안에는
  // 같은 추천 결과를 다시 그리지 않아 카드 이미지가 깜빡이지 않습니다.
  function discoveryInputKey() {
    return JSON.stringify({
      type,
      series: meta.series_name || context.seriesName || '',
      alias: meta.series_alias || '',
      localized: meta.localized_series || '',
      author: meta.author || '',
      genre: meta.genre || '',
      tags: meta.tags || '',
      contentKind: contentKind || '',
    });
  }

  function discoveryItemKey(item, label = '') {
    return [
      label,
      item?.book_id || item?.id || '',
      item?.series_name || '',
      item?.display_name || '',
      item?.localized_series || '',
      item?.cover || '',
      item?.library_id || '',
    ];
  }

  function discoveryPayloadSignature(data) {
    const relations = Object.entries(data?.relations || {}).map(([label, items]) => [
      label,
      (Array.isArray(items) ? items : []).map((item) => discoveryItemKey(item, label)),
    ]);
    const recommendations = (Array.isArray(data?.recommendations) ? data.recommendations : [])
      .map((item) => discoveryItemKey(item));
    return JSON.stringify({ relations, recommendations, graph: data?.relation_graph || {},
      publicationStatus: data?.publication_status_label || '' });
  }

  function renderDiscoveryPayload(data) {
    const previousStatus = String(meta.publication_status_label || '').trim();
    if (discoveryStatusLabel && previousStatus === discoveryStatusLabel) meta.publication_status_label = '';
    discoveryStatusLabel = '';
    const incomingStatus = String(data?.publication_status_label || '').trim();
    const storedStatus = String(meta.publication_status ?? '').trim();
    if ((!meta.publication_status_label || meta.publication_status_label === '알 수 없음')
        && !['0', '1', '2'].includes(storedStatus)
        && ['연재', '휴재', '연재중단', '완결', '연재 예정'].includes(incomingStatus)) {
      meta.publication_status_label = incomingStatus;
      discoveryStatusLabel = incomingStatus;
    }
    if (previousStatus !== String(meta.publication_status_label || '').trim()) renderInfo();
    const signature = discoveryPayloadSignature(data);
    if (signature === discoveryRenderedSignature) return false;
    renderRelations(data.relations || {}, data.relation_graph || {});
    renderRecommendations(data.recommendations || []);
    discoveryRenderedSignature = signature;
    return true;
  }

  function allowNavigation() {
    if (saving) return false;
    if (dirty && !window.confirm('저장하지 않은 변경사항을 두고 이동할까요?')) return false;
    dirty = false;
    return true;
  }

  function read(book, resume = false) {
    if (!book) return;
    if (media) {
      const player = type === 'audiobook' ? window.openAudioPlayer : window.openVideoPlayer;
      const parentId = Number(meta.id);
      if (!Number.isInteger(parentId) || parentId < 1 || typeof player !== 'function') {
        return notify('미디어 플레이어를 연결하지 못했습니다. 페이지를 새로고침해 주세요.', true);
      }
      if (!allowNavigation()) return;
      const currentId = Number(meta.current_track_id ?? meta.current_episode_id);
      const current = currentId === Number(book.id);
      player(parentId, Number(book.id), resume && current && !completed(book) ? num(meta.current_time) : 0);
      return;
    }
    if (typeof window.openReader !== 'function') return notify('리더를 연결하지 못했습니다. 페이지를 새로고침해 주세요.', true);
    if (!allowNavigation()) return;
    window.openReader(Number(book.id), book.file_format, fileDisplayLabel(book), num(book.pages_read), num(book.total_pages));
  }

  async function applyFilter(kind, value) {
    const filter = kind === 'genre' ? window.quickFilterByGenre : window.quickFilterByTag;
    if (typeof filter !== 'function') return notify('필터 화면을 연결할 수 없습니다. 새로고침해 주세요.', true);
    if (!allowNavigation()) return;
    try {
      await filter(value);
    } catch {
      notify('필터 화면을 열지 못했습니다. 다시 시도해 주세요.', true);
    }
  }

  async function searchAuthor(value, artist = false) {
    if (!allowNavigation()) return;
    const name = String(value || '').trim();
    if (!name) return;
    try {
      const { state } = await import('/static/js/state.js');
      if (!root.isConnected) return;
      const searchQuery = (artist ? '그림작가:' : '작가:') + name;
      state.searchQuery = searchQuery;
      const input = document.getElementById('library-search');
      if (input) input.value = searchQuery;
      if (typeof window.selectCategory === 'function') {
        await window.selectCategory('all', false, {
          preserveSearch: true,
          searchNavigation: true,
          searchQuery,
        });
        if (input) input.focus({ preventScroll: true });
      } else if (typeof window.filterBooks === 'function') {
        window.filterBooks();
      } else {
        throw new Error('검색 화면을 찾을 수 없습니다.');
      }
    } catch {
      notify('작가 검색을 열지 못했습니다.', true);
    }
  }

  async function loadRating() {
    const target = $('[data-stars]');
    const normalizeRating = (value) => Math.max(0, Math.min(5, Math.round(num(value) * 2) / 2));
    let api;
    const star = (filled) => {
      const element = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      element.setAttribute('class', 'fa-' + (filled ? 'solid' : 'regular') + ' ds-rating-star');
      element.setAttribute('viewBox', '0 0 24 24');
      const shape = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      shape.setAttribute('d', 'M12 2 15 8.5 22 9.3 17 14.2 18.3 21 12 17.6 5.7 21 7 14.2 2 9.3 9 8.5Z');
      element.append(shape);
      element.setAttribute('aria-hidden', 'true');
      return element;
    };
    let data = null;
    let submitting = false;
    const steps = Array.from({ length: 10 }, (_, index) => (index + 1) / 2);
    const paint = (value) => {
      const shown = normalizeRating(value);
      target.setAttribute('aria-label', '내 평점 ' + shown + '점 / 5점');
      target.querySelectorAll('[data-rating-step]').forEach((part) => {
        const rating = Number(part.dataset.ratingStep);
        const icon = part.firstElementChild;
        if (icon) {
          icon.classList.toggle('fa-solid', rating <= shown);
          icon.classList.toggle('fa-regular', rating > shown);
        }
        if (part.tagName === 'BUTTON') part.setAttribute('aria-pressed', String(rating === shown));
      });
    };
    const updateSummary = () => {
      let cancel = target.querySelector('.ds-rating-cancel');
      if (normalizeRating(data?.my_rating) > 0) {
        if (!cancel) {
          cancel = node('button', 'ds-rating-cancel');
          cancel.type = 'button';
          cancel.textContent = '평점 취소';
          cancel.addEventListener('click', event => {
            event.preventDefault(); event.stopPropagation(); save(0);
          });
          target.append(cancel);
        }
        cancel.disabled = submitting;
      } else cancel?.remove();
      let summary = target.querySelector('.ds-rating-summary');
      if (!num(data?.count)) {
        summary?.remove();
        return;
      }
      if (!summary) {
        summary = node('small', 'ds-rating-summary');
        target.append(summary);
      }
      summary.textContent = num(data.count) + '명 · 평균 ' + num(data.average).toFixed(1);
    };
    const build = (value, interactive) => {
      target.replaceChildren(...steps.map((rating, index) => {
        const part = node(interactive ? 'button' : 'span', 'ds-star-half ' + (index % 2 ? 'is-right' : 'is-left'));
        if (interactive) {
          part.type = 'button';
          part.disabled = submitting;
          part.setAttribute('aria-label', rating + '점 선택');
          part.setAttribute('aria-pressed', 'false');
          part.addEventListener('pointerenter', () => paint(rating));
          part.addEventListener('focus', () => paint(rating));
          part.addEventListener('click', (event) => {
            event.preventDefault();
            event.stopPropagation();
            save(rating);
          });
        } else {
          part.setAttribute('aria-hidden', 'true');
        }
        part.dataset.ratingStep = String(rating);
        part.append(star(rating <= value));
        return part;
      }));
      paint(value);
      if (interactive) {
        target.addEventListener('pointerleave', clearPreview);
        target.addEventListener('focusout', (event) => {
          if (!target.contains(event.relatedTarget)) clearPreview();
        });
      }
      updateSummary();
    };
    const clearPreview = () => {
      paint(data?.my_rating);
    };
    const save = async (rating) => {
      if (submitting) return;
      submitting = true;
      target.querySelectorAll('button[data-rating-step]').forEach((button) => { button.disabled = true; });
      paint(rating);
      try {
        const result = await api.submitRating(type, ratingContext, rating);
        if (!result.success) throw new Error(result.error || '별점 저장에 실패했습니다.');
        data = { ...data, ...result, my_rating: rating === 0 ? 0 : normalizeRating(result.my_rating ?? rating) };
        updateSummary();
        if (root.isConnected) notify(rating === 0 ? '평점을 취소했습니다.' : '별점을 저장했습니다.');
      } catch (error) {
        if (root.isConnected) notify(error.message || '별점 저장에 실패했습니다.', true);
      } finally {
        submitting = false;
        if (root.isConnected) {
          updateSummary();
          target.querySelectorAll('button[data-rating-step]').forEach((button) => { button.disabled = false; });
          paint(data?.my_rating);
        }
      }
    };

    const initialRating = normalizeRating(num(meta.score) / 20);
    build(initialRating, false);
    if (type !== 'general' && type !== 'adult') return;

    // core/api.js accepts the camelCase context keys.  Passing the old
    // snake_case names made the provider receive an empty series/book scope,
    // which was especially visible when the mobile stars were clicked.
    const ratingContext = {
      seriesName: meta.series_name || context.seriesName || '',
      libraryId: libraryId || context.libraryId || '',
      bookId: books[0]?.id || meta.id || '',
      author: meta.author || '',
      isbn: meta.isbn || '',
    };
    try {
      api = await import('/static/js/api.js');
      data = await api.fetchRatingWidget(type, ratingContext);
      if (!root.isConnected) return;
      if (!data?.success) {
        // 제공자가 잠시 응답하지 않아도 별표를 정적 텍스트로 남기지 않는다.
        // 사용자가 hover/click으로 상태를 확인할 수 있게 하고, 제출 시 서버가
        // 반환한 구체적인 오류를 알림으로 보여준다.
        console.warn('[Rabbit detail] 별점 조회가 실패했습니다:', data?.error || '알 수 없는 오류');
        data = { average: 0, count: 0, my_rating: initialRating };
      }
      build(normalizeRating(data.my_rating), true);
    } catch (error) {
      console.warn('[Rabbit detail] 별점 위젯을 불러오지 못했습니다:', error);
      if (root.isConnected) {
        data = { average: 0, count: 0, my_rating: initialRating };
        build(initialRating, true);
      }
    }
  }

  function fitSummary() {
    const paragraph = $('[data-summary]');
    const button = $('[data-action=summary]');
    if (!paragraph || !button) return;
    const expanded = button.getAttribute('aria-expanded') === 'true';
    const style = getComputedStyle(paragraph);
    const lineHeight = parseFloat(style.lineHeight) || 24;
    const lines = window.matchMedia('(max-width: 700px)').matches ? 6 : 8;
    paragraph.style.maxHeight = 'none';
    paragraph.style.overflow = 'visible';
    const firstImage = paragraph.querySelector('.ds-summary-image');
    let maxHeight = lineHeight * lines;
    if (firstImage?.naturalHeight) {
      const summaryTop = paragraph.getBoundingClientRect().top;
      const imageBottom = firstImage.getBoundingClientRect().bottom - summaryTop;
      const imageMargin = parseFloat(getComputedStyle(firstImage).marginBottom) || 0;
      maxHeight = imageBottom + imageMargin;
    }
    const overflow = paragraph.scrollHeight > maxHeight + 1;
    button.hidden = !overflow;
    if (overflow && !expanded) {
      paragraph.style.maxHeight = maxHeight + 'px';
      paragraph.style.overflow = 'hidden';
    }
  }

  function bindBookMenu(target, book, seriesScope = false) {
    if (media || !book) return;
    target.title = (seriesScope ? detailTitle() : title(book)) + ' · 우클릭으로 도서 메뉴';
    target.addEventListener('contextmenu', (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (dirty || saving) return notify('편집을 저장하거나 취소한 후 도서 메뉴를 열어 주세요.');
      if (typeof window.showBookContextMenu !== 'function') return notify('코어 도서 메뉴를 연결하지 못했습니다. 새로고침해 주세요.', true);
      window.showBookContextMenu(event.clientX, event.clientY, Number(book.id), seriesScope ? detailTitle() : title(book), !seriesScope, {
        seriesName: meta.series_name || context.seriesName,
        libraryId: book.library_id ?? libraryId ?? context.libraryId,
        markUnreadScope: seriesScope ? 'series' : 'book',
        fileFormat: String(book.file_format || '').toLowerCase(),
        // The core menu uses this flag to choose the symmetric read/unread
        // action.  Plugin-rendered cards do not carry the core card's
        // data-has-progress attribute, so pass the state explicitly.
        hasProgress: seriesScope
          ? books.some((item) => completed(item) || progress(item) > 0)
          : completed(book) || progress(book) > 0,
      });
    });
  }

  function image(src, alt = '') {
    const wrapper = node('div', 'ds-book-art');
    const url = safeUrl(src, true);
    if (!url) {
      wrapper.append(node('span', '', '표지 없음'));
      return wrapper;
    }
    const cover = document.createElement('img');
    cover.src = url;
    cover.alt = alt;
    cover.loading = 'lazy';
    cover.addEventListener('error', () => wrapper.replaceChildren(node('span', '', '표지 없음')), { once: true });
    wrapper.append(cover);
    return wrapper;
  }

  function bookCard(book, index, linked = false, relationLabel = '') {
    const card = node('article', 'ds-book' + (relationLabel ? ' ds-relation-book' : ''));
    const button = node('button', 'ds-book-open');
    const coverWrap = node('div', 'ds-book-cover-wrap');
    button.type = 'button';
    const label = linked ? book.display_name || book.series_name || title(book) : fileDisplayLabel(book, index);
    button.setAttribute('aria-label', label + (linked ? ' 상세 보기' : media ? ' 재생' : ' 읽기'));
    const art = image(linked ? book.cover : book.cover_image, label);
    if (relationLabel && book.series_type) {
      const kind = String(book.series_type).trim().toLowerCase();
      const typeLabel = kind === 'novel' ? '소설'
        : ['manga', 'manhwa', 'manhua', 'oel'].includes(kind) ? '만화' : '기타';
      art.append(node('span', 'ds-relation-type-badge', typeLabel));
    }

    if (!linked) {
      bindBookMenu(art, book);
      const number = seriesCardNumber(book, index);
      art.append(node('span', 'ds-book-number', number));
      const overlay = node('span', 'ds-book-overlay');
      overlay.setAttribute('aria-hidden', 'true');
      overlay.append(icon(media ? 'play' : 'book-open'));
      art.append(overlay);
    }

    // The cover is the only part of a series card that starts reading. Keep
    // the title, status, progress and file format outside the button so that
    // a downloadable format is a real independent link and cannot be
    // swallowed by the reader button.
    button.append(art);
    coverWrap.append(button);
    if (!linked) {
      const listen = listenButton(book, 'ds-book-listen', title(book) + ' 음성으로 듣기');
      if (listen) coverWrap.append(listen);
    }
    const titleNode = node('span', 'ds-book-title', label);
    let subtitle = null;
    let progressDisplay = null;
    if (linked) {
      button.append(titleNode);
      const originalTitle = String(book.localized_series || '').trim();
      if (originalTitle) button.append(node('span', 'ds-book-subtitle', originalTitle));
      button.addEventListener('click', () => {
        if (!allowNavigation()) return;
        if (typeof window.openBookDetail === 'function') {
          // openBookDetail accepts the target library directly and preserves
          // the current detail until the next detail is ready. Calling
          // selectCategory first briefly reveals the series grid.
          window.openBookDetail(null, book.series_name, book.library_id, book.book_id);
        } else {
          notify('상세페이지 연결을 사용할 수 없습니다.', true);
        }
      });
    } else {
      const state = completed(book)
        ? (media ? '재생 완료' : '완독')
        : reading(book)
          ? progress(book) + '% ' + (media ? '재생' : '읽음')
          : (media ? '미재생' : '미독');
      subtitle = node('span', 'ds-book-subtitle');
      const format = fileFormatBadge(book.file_format, book, { anchor: true });
      if (format) subtitle.append(format, document.createTextNode(' · '));
      subtitle.append(document.createTextNode(state));
      const percent = progress(book);
      progressDisplay = node('span', 'ds-book-progress');
      const bar = document.createElement('progress');
      bar.max = 100;
      bar.value = percent;
      bar.setAttribute('aria-label', label + ' 진행률');
      progressDisplay.append(bar, node('small', 'ds-book-progress-value', percent + '%'));
      button.addEventListener('click', (event) => {
        // 시리즈 카드는 표지 영역만 읽기 동작을 담당합니다. 제목·진행률·
        // 미독 상태를 눌렀을 때는 상세 화면을 다시 열거나 읽기를 시작하지
        // 않습니다. 다운로드 가능한 파일 형식 배지는 자체 동작을 가집니다.
        if (!event.target.closest('.ds-book-art')) return;
        read(book);
      });
    }

    if (relationLabel) card.append(node('span', 'ds-relation-label', relationLabel));
    if (linked) {
      card.append(button);
    } else {
      card.append(coverWrap, titleNode, subtitle, progressDisplay);
    }
    return card;
  }

  function volumeCard(group, index) {
    const representative = group.books[0];
    const card = node('article', 'ds-book ds-volume-card');
    const head = node('div', 'ds-volume-head');
    const toggle = node('button', 'ds-volume-toggle');
    toggle.type = 'button';
    const volumeLabel = fileDisplayLabel(representative, index, { unit: 'volume' })
      || (group.volume > 0 ? numberText(group.volume) + '권' : title(representative));
    toggle.setAttribute('aria-label', volumeLabel + ' 화 목록 열기');
    toggle.setAttribute('aria-expanded', 'false');
    const art = image(representative.cover_image, volumeLabel);
    bindBookMenu(art, representative);
    art.append(node('span', 'ds-book-number', volumeLabel));
    toggle.append(art, node('span', 'ds-book-title', volumeLabel));
    const done = group.books.filter(completed).length;
    const state = done === group.books.length ? '완독' : done ? done + '/' + group.books.length + '화 읽음' : '미독';
    const subtitle = node('span', 'ds-book-subtitle', group.books.length + '화 · ' + state);
    toggle.append(subtitle);
    const groupPercent = group.books.length
      ? Math.round(group.books.reduce((sum, book) => sum + progress(book), 0) / group.books.length)
      : 0;
    const progressDisplay = node('span', 'ds-book-progress');
    const bar = document.createElement('progress');
    bar.max = 100; bar.value = groupPercent;
    bar.setAttribute('aria-label', volumeLabel + ' 진행률');
    progressDisplay.append(bar, node('small', 'ds-book-progress-value', groupPercent + '%'));
    toggle.append(progressDisplay);

    const reader = node('button', 'ds-volume-reader');
    reader.type = 'button';
    reader.title = volumeLabel + ' 읽기';
    reader.setAttribute('aria-label', volumeLabel + ' 읽기');
    reader.append(icon('book-open'));
    reader.addEventListener('click', (event) => {
      event.stopPropagation();
      read(group.books.find((book) => !completed(book)) || representative, true);
    });
    const listenTarget = group.books.find((book) => !completed(book) && canListenBook(book))
      || group.books.find(canListenBook);
    const listen = listenButton(listenTarget, 'ds-volume-listen', volumeLabel + ' 음성으로 듣기');

    const chapters = node('div', 'ds-volume-chapters');
    chapters.hidden = true;
    const sorted = [...group.books].sort((a, b) => (chapterNumber(a) || 0) - (chapterNumber(b) || 0));
    sorted.forEach((book, chapterIndex) => {
      const chapter = node('button', 'ds-chapter-row');
      chapter.type = 'button';
      const number = chapterNumber(book) > 0 ? numberText(chapterNumber(book)) + '화' : (chapterIndex + 1) + '화';
      chapter.append(node('strong', 'ds-chapter-number', number), node('span', 'ds-chapter-title', fileDisplayLabel(book, chapterIndex)));
      const state = completed(book) ? '완독' : reading(book) ? progress(book) + '% 읽음' : '미독';
      chapter.append(node('small', 'ds-chapter-state', state));
      chapter.addEventListener('click', () => read(book));
      chapters.append(chapter);
    });
    toggle.addEventListener('click', () => {
      const expanded = !chapters.hidden;
      chapters.hidden = expanded;
      toggle.setAttribute('aria-expanded', String(!expanded));
    });
    head.append(toggle, reader);
    if (listen) head.append(listen);
    card.append(head, chapters);
    return card;
  }

  function renderTerms(sectionSelector, listSelector, kind, value) {
    const section = $(sectionSelector);
    const list = $(listSelector);
    const excluded = kind === 'genre' ? excludedGenres : excludedTags;
    const values = split(value).filter((term) => !excluded.has(term.toLocaleLowerCase()));
    section.hidden = values.length === 0;
    list.replaceChildren(...values.map((term) => {
      const button = node('button', 'ds-tag ds-tag-' + (kind === 'genre' ? 'genre' : 'tag'), term);
      button.type = 'button';
      button.setAttribute('aria-label', (kind === 'genre' ? '장르 ' : '태그 ') + term + ' 필터');
      button.addEventListener('click', () => applyFilter(kind, term));
      return button;
    }));
  }

  function isStandalone() {
    const volume = Number(meta.comicinfo_volume);
    return meta.is_standalone === true || (!hasChapterMetadata()
      && Number(meta.comicinfo_count) === 1
      && (volume === 1 || volume === 100000)
      && String(meta.comicinfo_format || '').trim().toLowerCase() === 'special');
  }

  function publicationDate() {
    if (manualEmptyFields.has('release_date')) return '';
    return meta.release_date || books.find((book) => book.release_date)?.release_date
      || meta.document_publication_date || meta.publication_dates?.start || '';
  }

  function detailIdentifierFact(metadata) {
    const value = (item) => String(item ?? '').trim();
    const usable = (item) => {
      const text = value(item);
      return text && !/^(?:-|—|–|n\/?a)$/i.test(text) ? text : '';
    };
    const isbn = usable(metadata?.isbn);
    const normalizedIsbn = isbn
      .replace(/^ISBN(?:-1[03])?\s*[:：]?\s*/i, '')
      .replace(/[\s\-‐‑‒–—]/g, '');
    if (isbn && !/^0{10,13}$/.test(normalizedIsbn)) {
      return ['ISBN / Web ID', isbn];
    }
    const uci = usable(metadata?.uci) || usable(metadata?.UCI);
    if (uci) return ['UCI', uci];
    return ['ISBN / Web ID', usable(metadata?.web_id) || '—'];
  }

  function completedVolumeCoverageStatus(explicitStatus, coverage) {
    if (explicitStatus !== '완결' || !coverage || coverage.known !== true) return '';
    const total = Number(coverage.total) || 0;
    const missing = Number(coverage.missing) || 0;
    if (total <= 0 || missing <= 0) return '';
    const present = Math.max(0, Math.min(total, Number(coverage.present) || 0));
    return '누락 (' + present + '/' + total + '권)';
  }

  function renderInfo() {
    if (!detailDataReady) {
      const facts = $('[data-facts]');
      facts.replaceChildren(node('dd', 'ds-detail-loading', detailDataFailed
        ? '상세정보를 불러오지 못했습니다.' : '상세정보를 불러오는 중입니다.'));
      $('[data-genres-block]').hidden = true;
      $('[data-tags-block]').hidden = true;
      $('[data-site-links]').hidden = true;
      if (detailDataFailed) {
        const retry = node('button', 'ds-text-button', '다시 시도');
        retry.type = 'button';
        retry.addEventListener('click', async () => {
          detailDataFailed = false;
          renderInfo();
          await loadDetailData();
        });
        facts.append(retry);
      }
      return;
    }
    const formats = [...new Set(books.map((book) => String(book.file_format || '').trim().toUpperCase()).filter(Boolean))];
    const count = Number(meta.comicinfo_count) || 0;
    const volume = Number(meta.comicinfo_volume) || 0;
    const comicFormat = String(meta.comicinfo_format || '').trim().toLocaleLowerCase();
    const ageRating = getContentRating();
    const onlyChapters = chapterOnlyMode();
    const hasVolumesAndChapters = groupedVolumeMode();
    const groups = volumeGroups();
    const chapterNumbers = uniqueChapterNumbers();
    const coverage = chapterCoverage();
    const availableChapterCount = coverage.present || books.length;
    const chapterTarget = chapterTargetCount();
    const availableVolumeCount = Number(meta.publication_available_volume_count) || 0;
    const totalVolumes = hasVolumesAndChapters ? groups.length : media ? (count > 0 ? count : books.length) : availableVolumeCount || books.length;
    const pureVolumes = !media && !onlyChapters && !hasVolumesAndChapters;
    const explicitStatus = String(meta.publication_status_label || '').trim();
    const hasExplicitStatus = explicitStatus && explicitStatus !== '알 수 없음';
    const countLabel = onlyChapters
      ? '총화수'
      : type === 'audiobook' ? '트랙 수' : type === 'video' ? '에피소드 수' : '보유 권수';
    const rows = [];
    if (books.length < 2) rows.push(['파일포맷', formats.join(' / ') || '—']);
    if (!onlyChapters && (media || !isStandalone())) rows.push([countLabel, String(totalVolumes)]);
    const chapters = Number(meta.manual_chapter_count || (['완결', '연재중단'].includes(explicitStatus) ? meta.publication_coverage?.total : 0) || 0);
    if (Number.isInteger(chapters) && chapters > 0) {
      rows.push(['회차 수', String(chapters)]);
    }
    const authors = split(meta.author);
    const artists = split(meta.artist || meta.cover_artist);
    if (authors.length && authors.length === artists.length && authors.every((name) => artists.includes(name))) {
      rows.push(['글·그림 작가', authors.join(', ')]);
    } else {
      if (authors.length) rows.push(['글작가', authors.join(', ')]);
      if (artists.length) rows.push(['그림작가', artists.join(', ')]);
    }
    if (meta.translator && meta.translator !== '-') rows.push(['번역가', meta.translator]);
    if (type === 'adult') {
      for (const [key, label] of [['teams', '그룹'], ['locations', '작품'], ['characters', '캐릭터']]) {
        const value = String(meta[key] || '').trim();
        if (value && value !== '-') rows.push([label, value]);
      }
    }
    if (meta.publisher && meta.publisher !== '-') rows.push(['출판사', meta.publisher]);
    if (!media) {
      const chapterFinalFound = chapterTarget > 0
        && chapterNumbers.some((number) => number === chapterTarget)
        && !coverage.missing;
      const remoteCoverage = meta.publication_coverage || {};
      const incomplete = explicitStatus === '완결' && remoteCoverage.known && remoteCoverage.missing > 0;
      const incompleteVolume = completedVolumeCoverageStatus(
        explicitStatus, meta.publication_volume_coverage);
      const standalone = isStandalone();
      const status = standalone ? '단편' : incomplete
        ? '누락 (' + remoteCoverage.present + '/' + remoteCoverage.total + '화)'
        : incompleteVolume
        ? incompleteVolume
        : hasExplicitStatus
        ? explicitStatus
        : !hasChapterMetadata() && count === 1 && volume === 1 && comicFormat === 'special'
          ? '단편'
          : onlyChapters || hasVolumesAndChapters
            ? chapterTarget > 0
              ? (chapterFinalFound ? '완결' : '누락 (' + availableChapterCount + '/' + chapterTarget + '화)')
              : coverage.missing
                ? '누락 (' + availableChapterCount + '/' + coverage.expected + '화)'
                : hasVolumesAndChapters && count > 0
                  ? (meta.publication_final_volume_found ? '완결' : '누락 (' + (availableVolumeCount || books.length) + '/' + count + '권)')
                  : '연재'
            : count > 0
              ? (meta.publication_final_volume_found
                ? '완결'
                : '누락 (' + (availableVolumeCount || books.length) + '/' + count + '권)')
            : volume > 0 ? '연재' : '알 수 없음';
      if (contentKind === 'book' || standalone) {
        if (standalone && contentKind !== 'book') rows.push(['연재상태', '단편']);
        const published = publicationDate();
        rows.push(['출간일', published ? String(published).slice(0, 10) : '—']);
      } else {
        const holdings = meta.holdings_coverage || {};
        const volumeStatus = holdings.holdings_status === 'missing' && holdings.holdings_label
          ? holdings.holdings_label : (hasExplicitStatus ? explicitStatus : '알 수 없음');
        rows.push(['연재상태', pureVolumes && !standalone ? volumeStatus : status]);
        rows.push(['연재시작일', meta.publication_dates?.start || '—']);
        rows.push(['연재종료일', meta.publication_dates?.end || '—']);
      }
      rows.push(['연령등급', ageRating?.label || '—']);
    }
    rows.push(detailIdentifierFact(meta));

    const facts = $('[data-facts]');
    facts.replaceChildren();
    for (const [label, value] of rows) {
      const row = document.createElement('div');
      const factClass = (label === '글작가' || label === '글·그림 작가')
        ? 'ds-fact-author'
        : label === '그림작가'
          ? 'ds-fact-artist'
          : label === '연령등급'
            ? 'ds-fact-age'
            : 'ds-fact-detail';
      row.className = factClass;
      row.dataset.factLabel = label;
      const term = node('dt', '', label);
      const detail = document.createElement('dd');
      if (label === '파일포맷' && formats.length) {
        formats.forEach((format, index) => {
          if (index) detail.append(document.createTextNode(' / '));
          detail.append(fileFormatBadge(format, books.length === 1 ? books[0] : null, { anchor: true }));
        });
      } else if (['글작가', '그림작가', '글·그림 작가'].includes(label) && value && value !== '—') {
        split(value).forEach((name, index) => {
          if (index) detail.append(document.createTextNode(', '));
          const person = node('button', 'ds-info-link', name);
          person.type = 'button';
          person.title = label + ' ' + name + ' 검색';
          person.setAttribute('aria-label', label + ' ' + name + ' 검색');
          person.addEventListener('click', () => searchAuthor(name, label === '그림작가'));
          detail.append(person);
        });
      } else if (label === '연령등급' && ageRating) {
        const badge = contentRatingBadge(ageRating);
        detail.append(node('span', 'ds-age-rating ds-age-rating-' + badge.kind, badge.label));
      } else {
        detail.textContent = value === '' || value == null ? '—' : String(value);
      }
      row.append(term, detail);
      facts.append(row);
    }

    renderTerms('[data-genres-block]', '[data-genres]', 'genre', meta.genre);
    renderTerms('[data-tags-block]', '[data-tags]', 'tag', meta.tags);

    const links = seriesSiteLinks(books, meta.link);
    const shortcuts = $('[data-site-links]');
    shortcuts.hidden = links.length === 0;
    shortcuts.replaceChildren();
    links.forEach((link, index) => {
      const info = siteInfo(link);
      const anchor = node('a', 'ds-site-link');
      anchor.href = link;
      anchor.target = '_blank';
      anchor.rel = 'noopener noreferrer';
      anchor.setAttribute('aria-label', info.label + ' 링크 ' + (index + 1) + ' 열기');
      anchor.append(siteFavicon(link, info.label));
      shortcuts.append(anchor);
    });
  }

  function renderMetadataSources() {
    const target = $('[data-metadata-source-options]');
    if (!target) return;
    const labels = { series_db: '데이터베이스', ridi: '리디', naver: '네이버시리즈', naver_webtoon: '네이버웹툰', kyobo: '교보문고', yes24: '예스24', kakao_webtoon: '카카오웹툰', kakaopage: '카카오페이지', munpia: '문피아', novelpia: '노벨피아' };
    const selected = metadataSources;
    target.replaceChildren(...Object.entries(labels).filter(([key]) => {
      const kind = ({'소설':'novel','라노벨':'novel','라이트노벨':'novel','웹툰':'manhwa'})[contentKind] || contentKind;
      return ['naver_webtoon','kakao_webtoon'].includes(key) ? kind === 'manhwa'
        : key === 'kakaopage' ? ['novel', 'manhwa'].includes(kind)
        : ['munpia','novelpia'].includes(key) ? kind === 'novel' : true;
    }).map(([key, label]) => {
      const field = node('label', 'ds-metadata-source-option');
      const input = document.createElement('input');
      input.type = 'checkbox'; input.value = key; input.checked = selected.includes(key);
      input.dataset.metadataSource = key;
      field.append(input, node('span', '', label));
      return field;
    }));
  }

  function metadataSourceSelection() {
    return all('[data-metadata-source-options] [data-metadata-source]:checked').map((input) => input.value);
  }

  function renderMetadataResults(results, showEmpty = true) {
    const target = $('[data-metadata-results]');
    if (!target) return;
    target.replaceChildren();
    if (!results.length) {
      if (showEmpty) {
        target.append(node('p', 'ds-metadata-empty', '검색 결과가 없습니다. 제목을 바꾸거나 다른 제공처를 선택해 보세요.'));
      }
      return;
    }
    results.forEach((item) => {
      const card = node('article', 'ds-metadata-result');
      const cover = node('div', 'ds-metadata-result-cover');
      const coverUrl = safeUrl(item.cover || item.metadata?.cover, true);
      if (coverUrl) {
        const image = document.createElement('img');
        image.src = coverUrl;
        image.alt = '';
        image.loading = 'lazy';
        image.decoding = 'async';
        image.referrerPolicy = 'no-referrer';
        image.addEventListener('error', () => {
          cover.replaceChildren(node('span', '', '표지 없음'));
        }, { once: true });
        cover.append(image);
      } else {
        cover.append(node('span', '', '표지 없음'));
      }
      const titleValue = item.title || item.metadata?.title || '제목 없음';
      const title = node('h3', '', titleValue);
      title.title = String(titleValue);
      const sourceLabel = item.source_label || item.source || '메타데이터';
      const variantLabel = item.variant_label || item.media_type_label || '';
      const source = node(
        'small', 'ds-metadata-result-source',
        variantLabel ? `${sourceLabel} > ${variantLabel}` : sourceLabel,
      );
      const original = item.original_title || item.metadata?.localized_series || '';
      const shownTitle = String(titleValue).trim().toLocaleLowerCase();
      const shownOriginal = String(original || '').trim().toLocaleLowerCase();
      const detailParts = [item.author, item.publisher].filter(Boolean);
      if (original && shownOriginal !== shownTitle) detailParts.push(original);
      const detail = node('p', '', detailParts.join(' · '));
      const apply = node('button', 'ds-button ds-primary', '이 메타데이터 적용');
      apply.type = 'button';
      apply.addEventListener('click', () => applyMetadataResult(item, apply));
      const linkValue = String(item.url || item.link || item.metadata?.link || '')
        .split(/[,;\n]+/u)[0].trim();
      const linkUrl = safeUrl(linkValue);
      const link = node('a', 'ds-metadata-result-link');
      if (linkUrl) {
        link.href = linkUrl;
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
        link.title = sourceLabel;
        link.setAttribute('aria-label', sourceLabel);
        link.append(siteFavicon(linkUrl, sourceLabel));
      } else {
        link.hidden = true;
      }
      card.append(source, cover, title, detail, apply, link);
      target.append(card);
    });
  }

  function openMetadataSearch() {
    if (!canEdit) return;
    // 모바일 상세정보 패널은 검색/편집 화면과 동시에 열리지 않도록 닫는다.
    // 패널이 fixed로 떠 있는 상태에서 아래 화면이 열리면 버튼과 검색폼을
    // 가리는 문제가 생길 수 있다.
    closeMobileInfo();
    const panel = $('[data-metadata-search]');
    panel.hidden = false;
    renderMetadataSources();
    const query = $('[data-metadata-query]');
    query.value = query.value || meta.series_name || context.seriesName || '';
    query.focus();
  }

  function closeMetadataSearch() { $('[data-metadata-search]').hidden = true; }

  async function searchMetadata(event) {
    event.preventDefault();
    if (metadataSearchLoading || !canEdit) return;
    const query = String($('[data-metadata-query]').value || '').trim();
    if (query.length < 2) return notify('두 글자 이상 입력해 주세요.', true);
    const status = $('[data-metadata-search-status]');
    metadataSearchLoading = true;
    status.textContent = '메타데이터를 검색하고 있습니다.';
    renderMetadataResults([], false);
    try {
      const result = await request('/api/media/context-menu/book/plugins/action', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plugin_id: 'rabbit_plugins', action_id: 'metadata_search', type,
          context: { query, sources: metadataSourceSelection(), content_kind: contentKind,
            series_name: meta.series_name || context.seriesName, book_id: books[0]?.id || '' } }),
      });
      if (typeof result.query === 'string' && result.query.trim()) {
        $('[data-metadata-query]').value = result.query.trim();
      }
      metadataSearchResults = Array.isArray(result.results) ? result.results : [];
      renderMetadataResults(metadataSearchResults);
      status.textContent = metadataSearchResults.length + '개 후보를 찾았습니다.';
    } catch (error) {
      status.textContent = error.message || '메타데이터 검색에 실패했습니다.';
      renderMetadataResults([], false);
    } finally { metadataSearchLoading = false; }
  }

  async function applyMetadataResult(item, button) {
    if (button.disabled) return;
    button.disabled = true; button.textContent = '적용 중...';
    try {
      const result = await request('/api/media/context-menu/book/plugins/action', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plugin_id: 'rabbit_plugins', action_id: 'metadata_apply', type,
          context: { book_id: books[0]?.id || meta.id,
            metadata: { ...(item.metadata || item), url: item.url || item.link || '', source: item.source || '' },
            fields: [], series_name: meta.series_name || context.seriesName } }),
      });
      await refreshMetadataFromServer({ force: true });
      invalidateSeriesList();
      closeMetadataSearch();
      notify(result.message || '메타데이터를 적용했습니다.');
    } catch (error) {
      notify(error.message || '메타데이터를 적용하지 못했습니다.', true);
      button.disabled = false; button.textContent = '이 메타데이터 적용';
    }
  }

  const seriesOrderStorageKey = 'rabbit_plugins:series-order:v1';
  let seriesDescending = false;
  try { seriesDescending = localStorage.getItem(seriesOrderStorageKey) === 'descending'; } catch (_) {}
  const seriesOrderButton = $('[data-series-order]');
  seriesOrderButton.addEventListener('click', () => {
    seriesDescending = !seriesDescending;
    try { localStorage.setItem(seriesOrderStorageKey, seriesDescending ? 'descending' : 'ascending'); } catch (_) {}
    renderSeries();
    $('[data-series-books]').scrollLeft = 0;
  });

  function renderSeries() {
    seriesOrderButton.replaceChildren();
    const orderIcon = node('i', 'fa-solid ' + (seriesDescending ? 'fa-arrow-up-wide-short' : 'fa-arrow-down-short-wide'));
    orderIcon.setAttribute('aria-hidden', 'true');
    seriesOrderButton.append(orderIcon);
    seriesOrderButton.setAttribute('aria-pressed', String(seriesDescending));
    const orderLabel = seriesDescending ? '역순 · 정순으로 변경' : '정순 · 역순으로 변경';
    seriesOrderButton.title = orderLabel;
    seriesOrderButton.setAttribute('aria-label', orderLabel);
    // Reverse rendered cards only; reading order and fallback volume labels
    // retain their original ascending indices.
    const orderedCards = cards => seriesDescending ? cards.reverse() : cards;
    const section = $('[data-series-section]');
    const list = $('[data-series-books]');
    section.hidden = books.length < 2;
    if (groupedVolumeMode()) {
      const groups = volumeGroups();
      $('[data-series-description]').textContent = groups.length + '권 · 표지를 누르면 화 목록을 확인하고 책 아이콘으로 읽습니다.';
      list.replaceChildren(...orderedCards(groups.map((group, index) => volumeCard(group, index))));
      return;
    }
    if (chapterOnlyMode()) {
      $('[data-series-description]').textContent = books.length + '화 · 표지를 누르면 읽기를 시작합니다.';
      list.replaceChildren(...orderedCards(books.map((book, index) => bookCard(book, index))));
      return;
    }
    const label = type === 'audiobook' ? '트랙' : type === 'video' ? '에피소드' : '권';
    $('[data-series-description]').textContent = books.length + label + ' · 표지를 누르면 ' + (media ? '재생' : '읽기') + '를 시작합니다.';
    const seriesBooks = media ? books : books
      .map((book, index) => ({ book, index }))
      .sort((a, b) => compareSeriesVolumeBooks(a.book, b.book) || a.index - b.index)
      .map(({ book }) => book);
    list.replaceChildren(...orderedCards(seriesBooks.map((book, index) => bookCard(book, index))));
  }

  async function relationAction(action, extra = {}) {
    return request('/api/media/context-menu/book/plugins/action', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plugin_id: 'rabbit_plugins', action_id: action, type,
        context: { book_id: books[0]?.id || meta.id, ...extra } }),
    });
  }

  $('[data-action=manage-relations]').onclick = async () => {
    if (!canEdit) return;
    const dialog = node('dialog', 'ds-relations-manager');
    dialog.setAttribute('aria-label', '관련작품 연결 관리');
    const heading = node('h2', '', '관련작품 연결 관리');
    const description = node('p', 'ds-relations-help', '양쪽 상세페이지에서 표시할 관계를 각각 선택하세요. 한쪽을 바꿔도 반대쪽 선택은 자동으로 변경되지 않습니다.');
    const close = node('button', 'ds-button', '닫기');
    close.onclick = () => dialog.close();
    dialog.addEventListener('close', () => dialog.remove());
    const status = node('p', 'ds-relations-status'); status.setAttribute('role', 'status');
    const existing = node('div', 'ds-relations-list');
    const form = node('form', 'ds-relations-search');
    const input = node('input', ''); input.type = 'search'; input.placeholder = '연결할 시리즈 제목'; input.setAttribute('aria-label', '연결할 시리즈 제목');
    const search = node('button', 'ds-button', '검색'); search.type = 'submit';
    const results = node('div', 'ds-relations-list');
    const connectedSection = node('section', 'ds-relations-group ds-relations-connected');
    const connectedHeading = node('h3', '', '연결된 작품');
    connectedSection.setAttribute('aria-label', '연결된 작품');
    connectedSection.append(connectedHeading, existing);
    const resultsSection = node('section', 'ds-relations-group ds-relations-unconnected');
    const resultsHeading = node('h3', '', '검색 결과 · 미연결 작품');
    resultsSection.setAttribute('aria-label', '검색 결과 · 미연결 작품');
    resultsSection.hidden = true;
    resultsSection.append(resultsHeading, results);
    function updateResultCount() {
      const count = results.querySelectorAll('.ds-relations-row').length;
      resultsHeading.textContent = '검색 결과 · 미연결 작품 (' + count + ')';
      if (!count) results.textContent = '연결할 수 있는 검색 결과가 없습니다.';
    }
    form.append(input, search);
    const header = node('div', 'ds-relations-heading');
    header.append(heading, close);
    const body = node('div', 'ds-relations-body');
    body.append(description, form, status, connectedSection, resultsSection);
    dialog.append(header, body);
    const viewport = window.visualViewport;
    const fitDialog = () => {
      dialog.style.setProperty('--ds-dialog-height', (viewport?.height || window.innerHeight) + 'px');
      dialog.style.setProperty('--ds-dialog-top', (viewport?.offsetTop || 0) + 'px');
    };
    fitDialog();
    viewport?.addEventListener('resize', fitDialog);
    viewport?.addEventListener('scroll', fitDialog);
    window.addEventListener('resize', fitDialog);
    dialog.addEventListener('close', () => {
      viewport?.removeEventListener('resize', fitDialog);
      viewport?.removeEventListener('scroll', fitDialog);
      window.removeEventListener('resize', fitDialog);
    }, { once: true });
    root.append(dialog); dialog.showModal(); input.focus({ preventScroll: true });
    async function loadExisting() {
      const data = await relationAction('related_list');
      connectedHeading.textContent = '연결된 작품 (' + (data.items || []).length + ')';
      existing.replaceChildren();
      for (const item of data.items || []) existing.append(row(item, true));
      if (!existing.childNodes.length) existing.textContent = '연결된 작품이 없습니다.';
    }
    function row(item, removing) {
      const line = node('div', 'ds-relations-row');
      line.dataset.connection = removing ? 'connected' : 'unconnected';
      const badge = node('span', 'ds-relation-state', removing ? (item.connection_origin === 'automatic' ? '자동 연결' : '직접 연결') : '미연결');
      const label = node('span', '', item.series_name + (item.library_name ? ' · ' + item.library_name : ''));
      label.className = 'ds-relations-title';
      const relation = node('select', 'ds-relation-type');
      relation.setAttribute('aria-label', item.series_name + '의 관계 (현재 작품 기준)');
      for (const [value, label] of Object.entries(relationLabels)) {
        if (!editableRelationTypes.has(value)) continue;
        const option = node('option', '', label); option.value = value; relation.append(option);
      }
      relation.value = editableRelationTypes.has(item.relation_type) ? item.relation_type : 'other';
      const reverse = relation.cloneNode(true);
      reverse.value = editableRelationTypes.has(item.reverse_relation_type) ? item.reverse_relation_type : 'other';
      reverse.setAttribute('aria-label', item.series_name + '에서 현재 작품을 표시할 관계');
      const forwardField = node('label', 'ds-relation-field');
      forwardField.append(node('span', '', '현재 작품 → ' + item.series_name), relation);
      const reverseField = node('label', 'ds-relation-field');
      reverseField.append(node('span', '', item.series_name + ' → 현재 작품'), reverse);
      const fields = node('div', 'ds-relation-directions');
      fields.append(forwardField, reverseField);
      const controls = node('div', 'ds-relation-controls');
      if (removing) {
        const save = node('button', 'ds-button', '관계 변경');
        save.onclick = async () => {
          save.disabled = true;
          try {
            const data = await relationAction('related_add', { target_book_id: item.book_id, relation_type: relation.value, reverse_relation_type: reverse.value });
            await loadExisting(); discoveryLoaded = false; await loadDiscovery({ refresh: true });
            status.textContent = '관계를 변경했습니다.'; notify(status.textContent);
          } catch (error) { status.textContent = error.message; }
          finally { save.disabled = false; }
        };
        controls.append(save);
      }
      const button = node('button', 'ds-button', removing ? '연결 해제' : '새로 연결');
      button.onclick = async () => {
        button.disabled = true;
        try {
          const data = await relationAction(removing ? 'related_remove' : 'related_add', { target_book_id: item.book_id, relation_type: relation.value, reverse_relation_type: reverse.value });
          if (!removing) { line.remove(); updateResultCount(); }
          await loadExisting();
          discoveryLoaded = false;
          await loadDiscovery({ refresh: true });
          status.textContent = data.message; notify(data.message);
        } catch (error) { status.textContent = error.message; }
        finally { button.disabled = false; }
      };
      controls.append(button);
      line.append(badge, label, fields, controls); return line;
    }
    form.onsubmit = async event => {
      event.preventDefault(); search.disabled = true; results.replaceChildren(); resultsSection.hidden = true; status.textContent = '검색 중…';
      try {
        const data = await relationAction('related_search', { query: input.value });
        for (const item of data.items || []) results.append(row(item, false));
        updateResultCount(); resultsSection.hidden = false;
        status.textContent = data.items?.length ? data.items.length + '개의 미연결 작품을 찾았습니다.' : '검색 결과가 없습니다.';
      } catch (error) { status.textContent = error.message; }
      finally { search.disabled = false; }
    };
    try { await loadExisting(); } catch (error) { status.textContent = error.message; }
  };

  // Labels stay in the DOM so theme fonts, text sizing and keyboard navigation
  // are shared with Oasis. force-graph handles only the layout and thin links.
  let relationGraph = null;
  let relationGraphSettings = null, relationGraphInput = null;
  const relationGraphMobile = window.matchMedia('(max-width: 700px), (hover: none) and (pointer: coarse)');
  function relationGraphVisible(settings,mobile) {
    return Boolean(settings && settings.enabled && (!mobile || settings.mobileEnabled));
  }
  function refreshRelationGraphDisplay() {
    if(root.isConnected && relationGraphInput)renderRelationGraph(relationGraphInput.works,relationGraphInput.graphData);
  }
  function relationGraphLayout(count, width) {
    const labelWidth = Math.min(112, Math.max(84, width / 2 - 16));
    const box = { width: labelWidth, height: 101 };
    if (count < 1 || width < 1) return { height: 0, labelWidth, positions: [] };
    const satellites = count - 1;
    if (!satellites) return { height: 150, labelWidth, positions: [[0, -12]] };
    if (satellites === 1 && width >= labelWidth * 2 + 44) {
      const distance = Math.min(100, width / 4);
      return { height: 178, labelWidth, positions: [[-distance, -22], [distance, -22]] };
    }
    if(satellites<=3) {
      if(width>=labelWidth*2+96) {
        const left=-Math.min(160,(width-labelWidth-32)/2),right=Math.min(160,width/2-labelWidth/2-64),pitch=148;
        return {height:satellites*pitch+40,labelWidth,positions:[[left,-24],
          ...Array.from({length:satellites},(_,i)=>[right,(i-(satellites-1)/2)*pitch-24])]};
      }
      const height=count*154+32;
      return {height,labelWidth,positions:Array.from({length:count},(_,i)=>[0,-height/2+48+i*154])};
    }
    // A circle remains compact on desktop. If labels cannot fit around it,
    // use complete rows, increasing the section height instead of cropping.
    if (width >= 520 && satellites <= 18) {
      const rx = Math.min(220 + satellites * 7, (width - labelWidth - 28) / 2);
      for (let ry = 105; ry <= 150; ry += 15) {
        const positions = [[0, -12], ...Array.from({ length: satellites }, (_, i) => {
          const angle = (i / satellites) * Math.PI * 2 - Math.PI / 2;
          return [Math.cos(angle) * rx, Math.sin(angle) * ry - 12];
        })];
        const overlaps = positions.some((a, i) => positions.slice(i + 1).some(b =>
          Math.abs(a[0] - b[0]) < box.width + 10 && Math.abs(a[1] - b[1]) < box.height + 10));
        if (!overlaps) return { height: ry * 2 + 146, labelWidth, positions };
      }
    }
    const columns = Math.max(1, Math.min(8, Math.floor((width - 16) / (labelWidth + 14))));
    const rows = Math.ceil(satellites / columns);
    const height = (rows + 1) * 122 + 22;
    const centerRow = Math.floor((rows + 1) / 2);
    const rowY = row => (row - rows / 2) * 122 - 18;
    const positions = [[0, rowY(centerRow)]];
    for (let i = 0; i < satellites; i++) {
      const row = Math.floor(i / columns), inRow = Math.min(columns, satellites - row * columns);
      const col = i % columns;
      const slot = row >= centerRow ? row + 1 : row;
      positions.push([(col - (inRow - 1) / 2) * (labelWidth + 14), rowY(slot)]);
    }
    return { height, labelWidth, positions };
  }

  function relationGraphRecommendationLayout(count,width) {
    const labelWidth=Math.min(112,Math.max(84,width-24));
    const columns=Math.max(1,Math.floor((width-24)/(labelWidth+24)));
    const rows=Math.ceil((count-1)/columns),centre=Math.floor(rows/2),pitch=156;
    const height=(rows+1)*pitch+24,y=row=>(row-rows/2)*pitch-20;
    const positions=[[0,y(centre)]];
    for(let i=0;i<count-1;i++) {
      const row=Math.floor(i/columns),size=Math.min(columns,count-1-row*columns);
      positions.push([(i%columns-(size-1)/2)*(labelWidth+24),y(row>=centre?row+1:row)]);
    }
    return {positions,height,labelWidth};
  }

  function relationGraphBranchLayout(nodes,width,edges,stacked=false) {
    const fallback=relationGraphLayout(nodes.length,width);
    if(nodes.length<2 || nodes.length>5)return fallback;
    const peers=nodes.filter(n=>!n.current),connected=new Set(edges.flatMap(e=>[e.source,e.target]));
    const before=peers.filter(n=>n.relationType==='prequel' && !connected.has(n.id));
    const after=peers.filter(n=>!before.includes(n));
    const previous=peers.filter(n=>n.relationType==='prequel'),next=peers.filter(n=>n.relationType==='sequel');
    const originals=peers.filter(n=>n.relationType==='original');
    if(originals.length===1 && peers.length===1)return {...fallback,positions:[fallback.positions[1],fallback.positions[0]]};
    if(originals.length===1 && originals.length+previous.length===peers.length) {
      const wide=width>=380&&!stacked,reach=Math.min(150,(width-fallback.labelWidth-24)/2);
      const positions=new Map([['current',[wide?reach/2:reach,60]],[originals[0].id,[wide?0:-reach,wide?-120:60]]]);
      previous.forEach((n,i)=>positions.set(n.id,wide?[-reach,60+(i-(previous.length-1)/2)*148]:[0,-90-(previous.length-i-1)*148]));
      const ys=[...positions.values()].map(p=>p[1]),top=Math.min(...ys),bottom=Math.max(...ys),centre=(top+bottom)/2;
      return {...fallback,height:bottom-top+220,positions:nodes.map(n=>{const p=positions.get(n.id);return[p[0],p[1]-centre];})};
    }
    if(peers.length===1 && previous.length===1) {
      return {...fallback,positions:[fallback.positions[1],fallback.positions[0]]};
    }
    if(previous.length===peers.length) {
      const reach=Math.min(135,(width-fallback.labelWidth-24)/2),pitch=148;
      const positions=new Map([['current',[reach,-24]],...previous.map((n,i)=>[n.id,[-reach,(i-(previous.length-1)/2)*pitch-24]])]);
      return {...fallback,height:previous.length*pitch+72,positions:nodes.map(n=>positions.get(n.id))};
    }
    const side=peers.filter(n=>['side_story','spin_off','main_story'].includes(n.relationType));
    if(previous.length===1 && next.length===1 && peers.length<=3 && side.length===peers.length-2) {
      const wide=width>=480,reach=Math.min(200,(width-fallback.labelWidth-24)/2);
      const row=wide?(side.length?80:-24):(side.length?120:70);
      const positions=new Map([['current',[0,row]],[previous[0].id,[-reach,wide?row:-20]],
        [next[0].id,[reach,wide?row:-20]]]);
      if(side.length)positions.set(side[0].id,[0,wide?-100:-120]);
      return {...fallback,height:side.length?(wide?380:430):(wide?170:280),positions:nodes.map(n=>positions.get(n.id))};
    }
    if(peers.length===3 && !before.length) {
      // Centre the directly connected hub, rather than ordering by query results.
      const neighbours=new Map(peers.map(n=>[n.id,new Set()]));
      for(const e of edges)if(neighbours.has(e.source) && neighbours.has(e.target)) {
        neighbours.get(e.source).add(e.target);neighbours.get(e.target).add(e.source);
      }
      const hubs=peers.filter(n=>neighbours.get(n.id).size===2);
      if(hubs.length===1) {
        const hub=hubs[0],leaves=peers.filter(n=>n!==hub),slots=[fallback.positions[1],fallback.positions[3]];
        const positions=new Map([['current',fallback.positions[0]],[hub.id,fallback.positions[2]],...leaves.map((n,i)=>[n.id,slots[i]])]);
        return {...fallback,positions:nodes.map(n=>positions.get(n.id))};
      }
    }
    if(width<380 || !before.length || !after.length)return fallback;
    if(width<640) {
      // Reserve the same outer cable gutter as the compact fallback layout.
      // Put the independent prequels above the current work in the left column.
      const left=-width/2+fallback.labelWidth/2+12,right=width/2-fallback.labelWidth/2-88,pitch=190;
      const positions=new Map([['current',[left,0]]]);
      before.forEach((n,i)=>positions.set(n.id,[left,-(before.length-i)*pitch]));
      after.forEach((n,i)=>positions.set(n.id,[right,(i-(after.length-1)/2)*pitch]));
      const ys=[...positions.values()].map(p=>p[1]),top=Math.min(...ys),bottom=Math.max(...ys),centre=(top+bottom)/2;
      return {...fallback,height:bottom-top+160,positions:nodes.map(n=>{const p=positions.get(n.id);return [p[0],p[1]-centre-24];})};
    }
    const reach=Math.min(220,(width-fallback.labelWidth-24)/2),pitch=164;
    const positions=new Map([['current',[0,-24]]]);
    for(const [branch,sign] of [[before,-1],[after,1]])branch.forEach((n,i)=>positions.set(n.id,[reach*sign,(i-(branch.length-1)/2)*pitch-24]));
    return {...fallback,height:Math.max(before.length,after.length)*pitch+70,positions:nodes.map(n=>positions.get(n.id))};
  }

  function relationGraphViewport(availableWidth,count,grouped=false) {
    const maximum=count>8?1.1:1.25;
    // Keep a wide grouped graph's columns when enlarging its contents.
    const columnLimit=grouped && availableWidth>=820?availableWidth/(Math.floor(availableWidth/410)*410):maximum;
    const scale=Math.max(1,Math.min(maximum,availableWidth/860,columnLimit));
    return {scale,width:availableWidth/scale};
  }

  function relationGraphFrameStats(samples) {
    if(!samples.length)return null;
    const sorted=[...samples].sort((a,b)=>a-b),total=samples.reduce((a,b)=>a+b,0);
    return {frames:samples.length,fps:+(samples.length*1000/total).toFixed(1),p95:+sorted[Math.floor((sorted.length-1)*.95)].toFixed(1),over33:samples.filter(n=>n>33.4).length};
  }

  function loadRelationGraphEngine() {
    if (typeof window.ForceGraph === 'function') return Promise.resolve(window.ForceGraph);
    if (window.rabbitRelationGraphEngine) return window.rabbitRelationGraphEngine;
    window.rabbitRelationGraphEngine = new Promise((resolve, reject) => {
      const script = document.createElement('script');
      const timer = setTimeout(() => fail(), 15000);
      function fail() {
        clearTimeout(timer); script.remove(); window.rabbitRelationGraphEngine = null;
        reject(new Error('관계도 엔진을 불러오지 못했습니다.'));
      }
      script.src = 'https://cdn.jsdelivr.net/npm/force-graph@1.52.0/dist/force-graph.min.js';
      script.async = true;
      script.onload = () => {
        clearTimeout(timer);
        if (typeof window.ForceGraph === 'function') resolve(window.ForceGraph);
        else fail();
      };
      script.onerror = fail;
      document.head.append(script);
    });
    return window.rabbitRelationGraphEngine;
  }

  function relationGraphTopology(works, graphData, currentTitle) {
    const identity = book => JSON.stringify([String(book.library_id), String(book.series_name)]);
    const lookup = new Map(works.map((work, i) => [identity(work.book), String(i)]));
    const extraEdges = (graphData.edges || []).flatMap(edge => {
      const source = lookup.get(JSON.stringify(edge.source_key)), target = lookup.get(JSON.stringify(edge.target_key));
      return source && target && source !== target ? [{ source, target, kind: edge.relation_type,
        label: relationLabels[edge.relation_type] || '관련작품' }] : [];
    });
    const parents = works.map((_, i) => i);
    function find(i) { while (parents[i] !== i) { parents[i] = parents[parents[i]]; i = parents[i]; } return i; }
    // Categories follow the focused work's registered relationship, not a
    // different (original) work's adaptation edges to those same books.
    const adapted=works.map((work,i)=>work.type==='adaptation'?i:-1).filter(i=>i>=0);
    for (const edge of extraEdges) if (['sequel', 'series'].includes(edge.kind)
      && adapted.includes(+edge.source)===adapted.includes(+edge.target)) parents[find(+edge.target)] = find(+edge.source);
    // This is a visual category of explicitly registered adaptations. It does
    // not create relationships or reading order between the adapted works.
    for(const id of adapted.slice(1))parents[find(id)]=find(adapted[0]);
    const components = new Map();
    works.forEach((_, i) => { const key = find(i); if (!components.has(key)) components.set(key, []); components.get(key).push(String(i)); });
    const groups = [];
    for (const members of components.values()) {
      if (members.length < 2 && !adapted.includes(+members[0])) continue;
      const indegree = new Map(members.map(id => [id, 0]));
      for (const edge of extraEdges) if (edge.kind === 'sequel' && indegree.has(edge.source) && indegree.has(edge.target)) indegree.set(edge.target, indegree.get(edge.target) + 1);
      const queue = members.filter(id => !indegree.get(id)), ordered = [];
      while (queue.length) {
        const id = queue.shift(); ordered.push(id);
        for (const edge of extraEdges) if (edge.kind === 'sequel' && edge.source === id && indegree.has(edge.target)) {
          indegree.set(edge.target, indegree.get(edge.target) - 1); if (!indegree.get(edge.target)) queue.push(edge.target);
        }
      }
      const acyclic = ordered.length === members.length;
      ordered.push(...members.filter(id => !ordered.includes(id)));
      const sequence = members.length>1 && acyclic && ordered.slice(1).every((id, i) => extraEdges.some(edge =>
        edge.kind === 'sequel' && edge.source === ordered[i] && edge.target === id));
      const names = ordered.map(id => String(works[+id].book.display_name || works[+id].book.series_name));
      let prefix = names[0]; while (prefix && !names.every(name => name.startsWith(prefix))) prefix = prefix.slice(0, -1);
      prefix = prefix.replace(/[:：\s·\-]+$/u, '').trim();
      const category=members.every(id=>adapted.includes(+id))?'adaptation':null;
      const label = category?'각색 작품':prefix.replace(currentTitle, '').trim() || '이어지는 작품';
      groups.push({ id: 'group-' + groups.length, members: ordered, label, prefix, sequence,category });
    }
    groups.sort((a, b) => b.members.length - a.members.length);
    const focusedFamily=works.some(work=>work.type==='original')
      ? works.flatMap((work,i)=>['prequel','sequel','side_story','spin_off','main_story','series'].includes(work.type)?[String(i)]:[])
      : [];
    if(focusedFamily.length>1) {
      const family=new Set(focusedFamily);
      for(let i=groups.length-1;i>=0;i--)if(groups[i].members.some(id=>family.has(id)))groups.splice(i,1);
      groups.push({id:'group-current-family',members:['current',...focusedFamily],label:'현재 작품과 관련작품',prefix:'',sequence:false,category:'focused-family'});
    }
    return { groups, extraEdges };
  }

  function relationGraphGroupedLayout(nodes, width, groups) {
    const ordinary = nodes.filter(n => !n.group && !n.groupId);
    const panels = [{ ordinary }, ...groups.map(group => ({ group }))];
    const columns = width >= 820 ? Math.min(panels.length, Math.floor(width / 410)) : 1;
    const gap = 20, panelWidth = (width - gap * (columns - 1)) / columns;
    for (const panel of panels) {
      if (panel.ordinary) {
        if(ordinary.some(n=>n.relationType==='original') && ordinary.every(n=>n.current||['original','prequel'].includes(n.relationType))) {
          const branch=relationGraphBranchLayout(ordinary,panelWidth,[],columns===1);
          panel.positions=branch.positions;panel.height=branch.height;continue;
        }
        if(ordinary.length>1 && ordinary.slice(1).every(n=>n.relationType==='prequel')) {
          const branch=relationGraphBranchLayout(ordinary,panelWidth,[]);
          panel.positions=branch.positions;panel.height=branch.height;
          continue;
        }
        const cols = Math.max(1, Math.min(4, ordinary.length, Math.floor((panelWidth - 16) / 126)));
        const rows = Math.ceil(ordinary.length / cols), center = Math.min(ordinary.length - 1, Math.floor(rows / 2) * cols + Math.floor(cols / 2));
        const slots = Array.from({ length: rows * cols }, (_, i) => [(i % cols - (cols - 1) / 2) * 126, (Math.floor(i / cols) - (rows - 1) / 2) * 122 - 18]);
        const rootSlot = slots.splice(center, 1)[0];
        panel.positions = [rootSlot, ...slots].slice(0, ordinary.length); panel.height = rows * 122 + 42;
      } else {
        if(panel.group.category==='focused-family') {
          const members=panel.group.members.map(id=>nodes.find(n=>n.id===id));
          const peers=members.filter(n=>!n.current).sort((a,b)=>(a.relationType==='prequel'?0:1)-(b.relationType==='prequel'?0:1));
          const reach=Math.min(185,(panelWidth-128)/2),positions=new Map();
          let radiusY=135;
          for(;radiusY<1100;radiusY+=16) {
            positions.set('current',[0,0]);
            peers.forEach((n,i)=>{const angle=Math.PI+i*Math.PI*2/peers.length;positions.set(n.id,[Math.cos(angle)*reach,Math.sin(angle)*radiusY]);});
            const points=[...positions.values()];
            if(!points.some((a,i)=>points.slice(i+1).some(b=>Math.abs(a[0]-b[0])<90&&Math.abs(a[1]-b[1])<118)))break;
          }
          // Initial layout only: the focus remains an ordinary movable particle.
          panel.positions=members.map(n=>positions.get(n.id));panel.height=radiusY*2+240;continue;
        }
        const count = panel.group.members.length,rx = Math.max(40, Math.min(105+count*7,(panelWidth - 108) / 2));
        if(count===1){panel.positions=[[0,-18]];panel.height=220;continue;}
        if (count === 2) {
          const vertical = panelWidth < 390;
          const distance = Math.min(110,(panelWidth - 144) / 2);
          panel.positions = vertical ? [[0, -55], [0, 80]] : [[-distance, -18], [distance, -18]];
          panel.height = vertical ? 350 : 220;
          continue;
        }
        let positions = [], ry = 96;
        for (; ry < 550; ry += 12) {
          positions = Array.from({ length: count }, (_, i) => {
            const angle = i / count * Math.PI * 2 - Math.PI / 2;
            return [Math.cos(angle) * rx, Math.sin(angle) * ry - 18];
          });
          if (!positions.some((a, i) => positions.slice(i + 1).some(b => Math.abs(a[0]-b[0]) < 88 && Math.abs(a[1]-b[1]) < 108))) break;
        }
        if (ry >= 550) {
          const fallback = relationGraphLayout(count, panelWidth);
          positions = fallback.positions; ry = fallback.height / 2 - 56;
        }
        panel.positions = positions; panel.height = ry * 2 + 192;
      }
    }
    for(const panel of panels)if(panel.group && panel.group.members.some(id=>nodes.find(n=>n.id===id)?.extraRelations?.length)) {
      panel.height+=96;
      panel.positions=panel.positions.map(([x,y])=>[x,y+48]);
    }
    const columnHeights = Array(columns).fill(0);
    for (const panel of panels) {
      panel.column = columnHeights.indexOf(Math.min(...columnHeights));
      panel.top = columnHeights[panel.column];
      columnHeights[panel.column] += panel.height + gap;
    }
    const height = Math.max(...columnHeights) - gap + 16;
    const positions = new Map(), groupBoxes = new Map();
    panels.forEach((panel, i) => {
      const cx = (panel.column - (columns - 1) / 2) * (panelWidth + gap);
      const cy = -height / 2 + 8 + panel.top + panel.height / 2;
      const ids = panel.ordinary ? ordinary.map(n => n.id) : panel.group.members;
      ids.forEach((id, j) => positions.set(id, [cx + panel.positions[j][0], cy + panel.positions[j][1]]));
      if (panel.group) {
        positions.set(panel.group.id, [cx, cy - 18]);
        groupBoxes.set(panel.group.id, { width: Math.max(120, panelWidth - 8), height: panel.height - 12 });
      }
    });
    return { height, labelWidth: 112, positions: nodes.map(n => positions.get(n.id)), groupBoxes };
  }

  function relationGraphLinks(nodes, topology) {
    const byId = new Map(nodes.map(n => [n.id, n]));
    const links = nodes.filter(n => !n.current && !n.group && !n.groupId)
      .map(n => ({ source: 'current', target: n.id, label: '', spoke: true }));
    for (const group of topology.groups) {
      if(group.members.includes('current')) {
        for(const id of group.members.filter(id=>id!=='current'))links.push({source:'current',target:id,label:'',spoke:true,groupId:group.id});
      } else links.push({ source: 'current', target: group.id, label: '', spoke: true });
    }
    const pairs = new Map();
    const labelTargets=new Set(),targetSlots=new Map();
    for (const edge of topology.extraEdges) {
      // In an adaptation's focused family, show its direct relationships.
      // Cross-family original/adaptation details remain in the source data.
      const family=topology.groups.find(group=>group.category==='focused-family');
      if(family && family.members.includes(edge.source)!==family.members.includes(edge.target))continue;
      const key = [edge.source, edge.target].sort().join(':');
      if (!pairs.has(key)) pairs.set(key, []);
      pairs.get(key).push(edge);
    }
    for (const edges of pairs.values()) {
      const a = byId.get(edges[0].source), b = byId.get(edges[0].target);
      const groupId = a?.groupId && a.groupId === b?.groupId ? a.groupId : null;
      const distinct = edges.filter((edge, i) => edges.findIndex(other => other.source === edge.source && other.target === edge.target && other.kind === edge.kind) === i);
      const detailedSide=!groupId && distinct.find(edge=>['side_story','adaptation'].includes(edge.kind) && byId.get(edge.target)?.kind!==edge.label);
      const explicitMain=detailedSide && distinct.find(edge=>edge.kind==='main_story' && edge.source===detailedSide.target && edge.target===detailedSide.source);
      const explicitOriginal=detailedSide && detailedSide.kind==='adaptation' && distinct.find(edge=>edge.kind==='original' && edge.source===detailedSide.target && edge.target===detailedSide.source);
      distinct.forEach((edge, i) => {
        const key=edge.target+':'+edge.kind,target=byId.get(edge.target);
        const labelRoles=edge===detailedSide?[
          {role:explicitMain?'본편':explicitOriginal || detailedSide.kind==='adaptation' && byId.get(edge.source)?.kind==='원작'?'원작':'기준',title:byId.get(edge.source)?.shortLabel||byId.get(edge.source)?.label||''},
          {role:edge.kind==='adaptation'?'각색':'외전',title:target?.shortLabel||target?.label||''}
        ]:null;
        const showLabel=edge!==explicitMain && edge!==explicitOriginal && (Boolean(labelRoles) || target?.kind!==edge.label && !labelTargets.has(key));
        const targetSlot=targetSlots.get(edge.target)||0;
        if(showLabel){labelTargets.add(key);targetSlots.set(edge.target,targetSlot+1);}
        links.push({ ...edge,groupId,lane:0,pairKey:[edge.source,edge.target].sort().join(':'),pairIndex:i,pairSize:distinct.length,showLabel,targetSlot,labelRoles });
      });
    }
    return links;
  }

  function relationGraphNodeRelations(n,links,nodes=null) {
    return [...new Set(links.filter(link=>link.target===n.id && link.kind && link.label && link.label!==n.kind
      && (!nodes || n.groupId && nodes.find(peer=>peer.id===link.source)?.groupId===n.groupId)).map(link=>link.label))];
  }

  function relationGraphPeerDescriptions(n,links,nodes) {
    return [...new Set(links.filter(link=>link.target===n.id && link.kind && link.label && link.label!==n.kind).map(link=>{
      const source=nodes.find(peer=>peer.id===link.source);
      return source?source.label+' 기준: '+link.label:'';
    }).filter(Boolean))];
  }

  function relationGraphRecommendationSample(items,random=Math.random) {
    const seen=new Set(),books=(Array.isArray(items)?items:[]).filter(book=>{
      const key=String(book.library_id)+':'+String(book.series_name||book.book_id);
      if(seen.has(key))return false;seen.add(key);return true;
    });
    const shuffle=items=>{
      const values=[...items];
      for(let i=values.length-1;i>0;i--){const j=Math.floor(random()*(i+1));[values[i],values[j]]=[values[j],values[i]];}
      return values;
    };
    const mangaUpdates=books.filter(book=>book.recommendation_source==='manga_updates');
    const similar=books.filter(book=>book.recommendation_source==='similarity');
    const byScore=new Map();
    for(const book of similar){const score=Number(book.score)||0;if(!byScore.has(score))byScore.set(score,[]);byScore.get(score).push(book);}
    const rankedSimilar=[...byScore.keys()].sort((a,b)=>b-a).flatMap(score=>shuffle(byScore.get(score)));
    const fallback=books.filter(book=>book.recommendation_source==='genre_tag_fallback');
    const other=books.filter(book=>!['manga_updates','similarity','genre_tag_fallback'].includes(book.recommendation_source));
    return [...shuffle(mangaUpdates),...rankedSimilar,...shuffle(fallback),...shuffle(other)].slice(0,10);
  }

  function relationGraphRecommendationTone(book,index=0) {
    const identity=String(book?.recommendation_source||'recommendation')+':'+
      String(book?.series_name||book?.display_name||book?.book_id||index);
    let hash=2166136261;
    for(let i=0;i<identity.length;i++)hash=Math.imul(hash^identity.charCodeAt(i),16777619);
    return (hash>>>0)%4;
  }

  function relationGraphDrift(id,time) {
    const seed=[...String(id)].reduce((value,c)=>value*31+c.charCodeAt(0),17)>>>0;
    const noise=(t,s)=>{
      const cell=Math.floor(t),u=t-cell,ease=u*u*(3-2*u);
      const sample=i=>{const value=Math.sin(i*127.1+s*311.7)*43758.5453;return (value-Math.floor(value))*2-1;};
      return sample(cell)*(1-ease)+sample(cell+1)*ease;
    };
    return {x:noise(time/6+seed%23,seed)*3.1+noise(time/13,seed+7)*.9,
      y:noise(time/7.5+seed%17,seed+19)*2.5+noise(time/17,seed+31)*.7};
  }

  function relationGraphCanDrift(n, recommendations = false) {
    // Gentle idle floating belongs to books inside a soft group only.
    // Recommendation spokes get a smaller drift at the call site; ordinary
    // relation spokes must not keep rerouting while nobody is dragging.
    return Boolean((n.groupId || recommendations) && !n.current && !n.group);
  }

  function relationGraphReconcileDrag(spring,before) {
    for(const [axis,target,velocity] of [['x','targetX','vx'],['y','targetY','vy']]) {
      if(Math.abs(spring.n[axis]-before[axis])<.001)continue;
      // A wall/contact correction is the reachable pointer position. Do not
      // keep accelerating toward the rejected target on subsequent frames.
      spring[target]=spring.n[axis];spring[velocity]=0;
    }
  }

  function relationGraphSetDragTarget(drag,target) {
    // Many pointer positions outside one corner project to the same target.
    // Reapplying it overrides contact reconciliation and kicks the neighbours
    // again. Retain the reachable target until the projected input changes.
    if(Number.isFinite(drag.requestedTargetX) && Math.hypot(target.x-drag.requestedTargetX,target.y-drag.requestedTargetY)<.05)return false;
    drag.requestedTargetX=target.x;drag.requestedTargetY=target.y;
    drag.targetX=target.x;drag.targetY=target.y;
    return true;
  }

  function relationGraphReleaseSpring(drag) {
    // Accept the position visible at release. Following the last pointer target
    // afterwards makes a quick drag coast across the group without any input.
    return {...drag,targetX:drag.n.x,targetY:drag.n.y,
      vx:Math.max(-2,Math.min(2,drag.vx*.2)),vy:Math.max(-2,Math.min(2,drag.vy*.2))};
  }

  function relationGraphElasticPoint(p,target,reducedMotion=false) {
    if(reducedMotion){p.x=target.x;p.y=target.y;p.vx=p.vy=0;return;}
    for(const axis of ['x','y']) {
      const velocity=axis==='x'?'vx':'vy';
      p[velocity]=Math.max(-12,Math.min(12,((p[velocity]||0)+(target[axis]-p[axis])*.045)*.76));
      p[axis]+=p[velocity];
      if(Math.abs(target[axis]-p[axis])<.006 && Math.abs(p[velocity])<.006){p[axis]=target[axis];p[velocity]=0;}
    }
  }

  function relationGraphDragSpring(n,spring,reducedMotion=false,limits=null) {
    if(reducedMotion){n.x=spring.targetX;n.y=spring.targetY;spring.vx=spring.vy=0;}
    else {
      const dx=spring.targetX-n.x,dy=spring.targetY-n.y,distance=Math.hypot(dx,dy);
      if(distance>70){n.x=spring.targetX-dx/distance*70;n.y=spring.targetY-dy/distance*70;}
      spring.vx=Math.max(-22,Math.min(22,((spring.vx||0)+(spring.targetX-n.x)*.14)*.79));
      spring.vy=Math.max(-22,Math.min(22,((spring.vy||0)+(spring.targetY-n.y)*.14)*.79));
      n.x+=spring.vx;n.y+=spring.vy;
    }
    if(limits) {
      const before={x:n.x,y:n.y};
      relationGraphFitDragNode(n,limits);
      if(n.x!==before.x){spring.vx=0;spring.targetX=n.x;}
      if(n.y!==before.y){spring.vy=0;spring.targetY=n.y;}
    }
    n.fx=n.x;n.fy=n.y;
    const resting=Math.hypot(n.x-spring.targetX,n.y-spring.targetY)<.12 && Math.hypot(spring.vx,spring.vy)<.12;
    if(resting){n.x=n.fx=spring.targetX;n.y=n.fy=spring.targetY;spring.vx=spring.vy=0;}
    return resting;
  }

  function relationGraphCoverDistance(p,n) {
    if(!n.groupId && n.captionMetrics) {
      const centre=n.current?30:24,m=n.captionMetrics,x=n.x+(n.captionAbove?0:n.captionOffset||0);
      const titleTop=n.captionAbove?n.y-40-m.titleHeight:n.y-centre+m.titleTop;
      const kindTop=n.captionAbove?titleTop-4-m.kindHeight:n.y-centre+m.kindTop;
      const rectangle=(top,width,height)=>{
        const dx=Math.abs(p.x-x)-width/2,dy=Math.abs(p.y-top-height/2)-height/2;
        return Math.hypot(Math.max(dx,0),Math.max(dy,0))+Math.min(Math.max(dx,dy),0);
      };
      return Math.min(Math.hypot(p.x-n.x,p.y-n.y)-centre-2,
        rectangle(titleTop,n.labelWidth,m.titleHeight),rectangle(kindTop,m.kindWidth||n.labelWidth,m.kindHeight));
    }
    const top=n.y-(n.current?46:40),bottom=n.y-24+n.boxHeight+12;
    const cx=n.x,cy=(top+bottom)/2,hx=n.labelWidth/2+12,hy=(bottom-top)/2,r=20;
    const qx=Math.abs(p.x-cx)-(hx-r),qy=Math.abs(p.y-cy)-(hy-r);
    return Math.hypot(Math.max(qx,0),Math.max(qy,0))+Math.min(Math.max(qx,qy),0)-r;
  }

  function relationGraphPressPoint(p,n,centre) {
    let dx=centre.x-n.x,dy=centre.y-(n.y-24+n.boxHeight/2),distance=Math.hypot(dx,dy);
    if(distance<1){dx=p.x-n.x;dy=p.y-n.y;distance=Math.hypot(dx,dy)||1;}
    dx/=distance;dy/=distance;
    let low=0;
    if(relationGraphCoverDistance(p,n)>=12) {
      const cy=(n.y-(n.current?46:40)+n.y-24+n.boxHeight+12)/2;
      low=Math.max(0,(n.x-p.x)*dx+(cy-p.y)*dy);
      if(relationGraphCoverDistance({x:p.x+dx*low,y:p.y+dy*low},n)>=12)return p;
    }
    let high=low+16;
    while(high<1024 && relationGraphCoverDistance({x:p.x+dx*high,y:p.y+dy*high},n)<12)high*=2;
    for(let i=0;i<12;i++){
      const middle=(low+high)/2;
      if(relationGraphCoverDistance({x:p.x+dx*middle,y:p.y+dy*middle},n)<12)low=middle;else high=middle;
    }
    return {x:p.x+dx*high,y:p.y+dy*high};
  }

  function relationGraphResample(outline,count) {
    const segments=outline.map((p,i)=>({p,q:outline[(i+1)%outline.length],length:Math.hypot(outline[(i+1)%outline.length].x-p.x,outline[(i+1)%outline.length].y-p.y)}));
    const perimeter=segments.reduce((sum,s)=>sum+s.length,0);
    return Array.from({length:count},(_,i)=>{
      let remaining=i*perimeter/count,segment=segments[0];
      for(const s of segments){segment=s;if(remaining<=s.length)break;remaining-=s.length;}
      const t=remaining/(segment.length||1);
      return {x:segment.p.x+(segment.q.x-segment.p.x)*t,y:segment.p.y+(segment.q.y-segment.p.y)*t};
    });
  }

  function relationGraphUntangle(points) {
    const result=points.slice(),cross=(a,b,c)=>(b.x-a.x)*(c.y-a.y)-(b.y-a.y)*(c.x-a.x);
    for(let pass=0;pass<12;pass++) {
      let changed=false;
      for(let i=0;i<result.length;i++)for(let j=i+2;j<result.length;j++) {
        if(i===0 && j===result.length-1)continue;
        const a=result[i],b=result[(i+1)%result.length],c=result[j],d=result[(j+1)%result.length];
        if(cross(a,b,c)*cross(a,b,d)<-1e-7 && cross(c,d,a)*cross(c,d,b)<-1e-7) {
          const segment=result.slice(i+1,j+1).reverse();result.splice(i+1,segment.length,...segment);changed=true;
        }
      }
      if(!changed)break;
    }
    return result;
  }

  function relationGraphGroupConnector(a,b,link={}) {
    const state=link.state||link,ports=[{x:1,y:0},{x:0,y:-1},{x:-1,y:0},{x:.87,y:.5},{x:-.87,y:.5},{x:.7071,y:-.7071},{x:-.7071,y:-.7071},{x:.87,y:-.5},{x:-.87,y:-.5}];
    const sourceRadius=a.current?30:24;
    const outline=b.outline.length<48?relationGraphResample(b.outline,64):b.outline;
    // Attachment is a world-space point, never a mutable contour vertex index.
    const candidates=[];
    const nearbyEdges=outline.map((p,i)=>{
      const q=outline[(i+1)%outline.length],dx=q.x-p.x,dy=q.y-p.y,u=Math.max(0,Math.min(1,((a.x-p.x)*dx+(a.y-p.y)*dy)/(dx*dx+dy*dy||1)));
      return {p,i,distance:Math.hypot(a.x-p.x-dx*u,a.y-p.y-dy*u)};
    }).sort((p,q)=>p.distance-q.distance).slice(0,32);
    nearbyEdges.forEach(({p,i})=>{
      const q=outline[(i+1)%outline.length],dx=q.x-p.x,dy=q.y-p.y;
      const u=Math.max(0,Math.min(1,((a.x-p.x)*dx+(a.y-p.y)*dy)/(dx*dx+dy*dy||1)));
      const edge={x:p.x+dx*u,y:p.y+dy*u},distance=Math.hypot(a.x-edge.x,a.y-edge.y)||1;
      let visible=true;
      outline.forEach((v,j)=>{
        const w=outline[(j+1)%outline.length],sx=w.x-v.x,sy=w.y-v.y,rx=edge.x-a.x,ry=edge.y-a.y,den=rx*sy-ry*sx;
        if(Math.abs(den)<.00001)return;
        const vx=v.x-a.x,vy=v.y-a.y,t=(vx*sy-vy*sx)/den,s=(vx*ry-vy*rx)/den;
        if(t>.001 && t<.975 && s>=0 && s<=1)visible=false;
      });
      if(!visible)return;
      const nx=(a.x-edge.x)/distance,ny=(a.y-edge.y)/distance,ex=edge.x+nx*3,ey=edge.y+ny*3;
      const facingPorts=[...ports,{x:-nx,y:-ny}];
      facingPorts.forEach((port,portIndex)=>{
        const facing=port.x*(edge.x-a.x)/distance+port.y*(edge.y-a.y)/distance;
        if(facing<.1)return;
        if(ports.filter(other=>other.x*(edge.x-a.x)/distance+other.y*(edge.y-a.y)/distance>facing+.0001).length>=5)return;
        const portLength=Math.hypot(port.x,port.y)||1;
        const sx=a.x+port.x/portLength*sourceRadius,sy=a.y+port.y/portLength*sourceRadius,len=Math.hypot(ex-sx,ey-sy),arm=Math.min(65,len*.32);
        const ux=(ex-sx)/(len||1),uy=(ey-sy)/(len||1),budget=Math.min(20,len*.1);
        const control=(x,y)=>{const along=Math.max(len*.2,Math.min(len*.8,(x-sx)*ux+(y-sy)*uy));
          const across=Math.max(-budget,Math.min(budget,-(x-sx)*uy+(y-sy)*ux));
          return {x:sx+ux*along-uy*across,y:sy+uy*along+ux*across};};
        const c1=control(sx+port.x*arm,sy+port.y*arm),c2=control(ex+nx*arm,ey+ny*arm);
        const point=t=>({x:(1-t)**3*sx+3*(1-t)**2*t*c1.x+3*(1-t)*t*t*c2.x+t**3*ex,
          y:(1-t)**3*sy+3*(1-t)**2*t*c1.y+3*(1-t)*t*t*c2.y+t**3*ey});
        const samples=Array.from({length:19},(_,k)=>point((k+1)/20));
        let blocked=0;
        const ownLabel=a.captionAbove?{x:a.x,y:a.y+(a.captionTop||-96)+32,labelWidth:a.labelWidth||112,boxHeight:Math.max(1,-48-(a.captionTop||-96))}
          :{x:a.x+(a.captionOffset||0),y:a.y+64,labelWidth:a.labelWidth||112,boxHeight:Math.max(1,(a.boxHeight||104)-64)};
        const obstacles=[...(link.obstacles||[]),...(a.current?[]:[ownLabel])];
        for(const n of obstacles)for(let k=1;k<20;k++) {
          const v=samples[k-1];
          if(v.x>n.x-n.labelWidth/2-6 && v.x<n.x+n.labelWidth/2+6 && v.y>n.y-32 && v.y<n.y-24+n.boxHeight+6){blocked++;break;}
        }
        const proximity=state.cableAnchor?Math.hypot(edge.x-state.cableAnchor.x,edge.y-state.cableAnchor.y)*.12:0;
        let fan=0;
        for(const cable of link.cables||[])for(const anchor of cable.anchors||[]) {
          if(anchor.id===a.id)fan+=Math.max(0,18-Math.hypot(sx-anchor.point.x,sy-anchor.point.y))*80;
        }
        for(const other of link.neighbours||[]) {
          if(other.id===a.id || other.id===b.id)continue;
          const ox=other.x-a.x,oy=other.y-a.y,ol=Math.hypot(ox,oy)||1;
          const cosine=(port.x*ox+port.y*oy)/ol;
          fan+=Math.max(0,(cosine-.9)/.1)*180;
        }
        const score=blocked*10000+fan+len+(1-facing)*45+proximity+(state.portIndex!==undefined && state.portIndex!==portIndex?8:0);
        candidates.push({sx,sy,ex,ey,c1,c2,len:len+sourceRadius,ra:sourceRadius,rb:0,point,portIndex,score,anchor:{x:edge.x,y:edge.y}});
      });
    });
    if(!candidates.length)return {sx:a.x,sy:a.y,ex:a.x,ey:a.y,cx:a.x,cy:a.y,len:0,ra:1,rb:1,point:()=>({x:a.x,y:a.y})};
    const selected=candidates.sort((p,q)=>p.score-q.score)[0];
    state.portIndex=selected.portIndex;
    state.cableAnchor=selected.anchor;
    return selected;
  }

  function relationGraphCaptionOffset(n,neighbours) {
    const crossings=neighbours.filter(p=>p.y>n.y+40).map(p=>(p.x-n.x)*55/(p.y-n.y))
      .filter(x=>Math.abs(x)<(n.labelWidth||112)/2+10);
    if(!crossings.length)return 0;
    return (crossings.reduce((sum,x)=>sum+x,0)>=0?-1:1)*((n.labelWidth||112)/2+20);
  }

  function relationGraphCaptionPosition(n,neighbours,limits) {
    const half=(n.labelWidth||112)/2;
    const m=n.captionMetrics,centre=n.current?30:24;
    const below={top:m?m.titleTop-centre:32,bottom:m?m.kindTop+m.kindHeight-centre:94};
    const above={top:m?-44-m.titleHeight-m.kindHeight:-96,bottom:-40};
    // Captions belong to their cover. Only actual text/cover collisions and
    // viewport edges may choose its upper or lower slot; cables route around it.
    const slots=[{x:0,above:false,...below},{x:0,above:true,...above}];
    let best=slots[0],score=Infinity,retained=null;
    slots.forEach((slot,index)=>{
      // On first layout pick the side away from attached cables. Once chosen,
      // changing cable angles must never relocate the caption during a drag.
      let initialCrossings=0;
      if(!n.captionRecipe)for(const p of limits?.captionPeers||neighbours)for(let i=1;i<40;i++) {
        const x=(p.x-n.x)*i/40,y=(p.y-n.y)*i/40;
        if(x>-half-6 && x<half+6 && y>slot.top-5 && y<slot.bottom+5){initialCrossings++;break;}
      }
      let occupied=0;
      for(const p of neighbours) {
        if(p.group)continue;
        const px=p.x-n.x+(p.captionOffset||0),py=p.y-n.y;
        const top=py+(p.captionAbove?(p.captionTop||-96):32),bottom=p.captionAbove?py-40:py+94;
        if(slot.x-half<px+(p.labelWidth||112)/2 && slot.x+half>px-(p.labelWidth||112)/2 && slot.top<bottom && slot.bottom>top)occupied++;
        if(slot.x-half<px+30 && slot.x+half>px-30 && slot.top<py+30 && slot.bottom>py-30)occupied++;
      }
      const edgeY=limits?.requestedY??n.y;
      const vertical=limits?.height?Math.max(0,-limits.height/2+12-(edgeY+slot.top))+Math.max(0,edgeY+slot.bottom-(limits.height/2-12)):0;
      const overflow=(limits?Math.max(0,-limits.width/2+8-(n.x+slot.x-half))+Math.max(0,n.x+slot.x+half-(limits.width/2-8)):0)+vertical;
      const changed=n.captionRecipe && (n.captionRecipe.above!==slot.above || n.captionRecipe.x!==slot.x);
      // Discard offsets from older layouts immediately.
      const returningToCover=n.captionRecipe?.x && slot.x===0;
      const cost=initialCrossings*100+(n.current?0:occupied*300)+overflow*100+index+(changed && !returningToCover?25:0);
      // Keep an accepted centred caption while its actual space is clear.
      // A centre-to-centre ray changing direction is not a text collision.
      if(n.captionRecipe && !changed && slot.x===0 && !occupied && !overflow)retained=slot;
      if(cost<score){score=cost;best=slot;}
    });
    return retained||best;
  }

  function relationGraphUpdateCaption(n,neighbours,limits,dragging=false) {
    if(n.current)return {x:0,above:false,top:38,bottom:68};
    // Idle drift must not move a title to the opposite side and resize its body.
    // Choose while interacting (or fitting a new viewport), then retain it.
    return n.captionRecipe && !n.captionRecipe.x && !dragging?n.captionRecipe:relationGraphCaptionPosition(n,neighbours,limits);
  }

  function relationGraphBookCable(a,b,link,group) {
    // End each cable on the visible cover ring, rather than outside its halo.
    const dx=b.x-a.x,dy=b.y-a.y,distance=Math.hypot(dx,dy)||1,ra=a.current?32:26,rb=b.current?32:26;
    if(distance<88)return {sx:a.x,sy:a.y,ex:b.x,ey:b.y,cx:(a.x+b.x)/2,cy:(a.y+b.y)/2,len:0,ra,rb,point:t=>({x:a.x+dx*t,y:a.y+dy*t})};
    const ports=Array.from({length:9},(_,i)=>({x:Math.cos(-i*Math.PI/8),y:Math.sin(-i*Math.PI/8)}));
    // Side ports need enough clearance to turn outside the titles below covers.
    const state=link.state||{},arm=Math.max(76,Math.min(100,(distance-ra-rb)*.32));
    const gap=Math.max(0,distance-ra-rb),progress=Math.max(0,Math.min(1,(gap-40)/140));
    // A vertical cable still needs to clear the title directly below a cover.
    const titleClearance=Math.max(0,1-Math.abs(dx)/distance*2.5);
    const curvature=link.adjacency ? Math.min(.25,progress*progress*(3-2*progress))
      :Math.max(progress*progress*(3-2*progress),titleClearance);
    const relevant=(link.obstacles||[]).filter(n=>n.x>=Math.min(a.x,b.x)-120 && n.x<=Math.max(a.x,b.x)+120 && n.y>=Math.min(a.y,b.y)-120 && n.y<=Math.max(a.y,b.y)+120);
    const geometry=[a,b,...relevant];
    const unchanged=state.routingGeometry?.length===geometry.length && geometry.every((p,i)=>{
      const old=state.routingGeometry[i];
      return Math.hypot(p.x-old.x,p.y-old.y)<6 && Boolean(p.captionAbove)===old.captionAbove
        && (p.captionTop||0)===old.captionTop && (p.labelWidth||0)===old.labelWidth && (p.boxHeight||0)===old.boxHeight;
    });
    const cached=unchanged && state.sourcePort!==undefined && state.targetPort!==undefined;
    const labelBox=n=>n.captionAbove?{x:n.x,y:n.y+(n.captionTop||-96)+32,labelWidth:n.labelWidth||112,boxHeight:Math.max(1,-40-(n.captionTop||-96)-8)}
      :{x:n.x+(n.captionOffset||0),y:n.y+64,labelWidth:n.labelWidth||112,boxHeight:Math.max(1,(n.boxHeight||104)-64)};
    const obstacles=[...(link.obstacles||[]),...[a,b].filter(n=>!n.current).map(labelBox)];
    const sx=a.x+dx/distance*ra,sy=a.y+dy/distance*ra,ex=b.x-dx/distance*rb,ey=b.y-dy/distance*rb;
    // A shallow arc softens the cable without retaining large bends at close range.
    if(state.bowSign===undefined)state.bowSign=a.current && dx>Math.abs(dy)*.3?-1:1;
    const bow=Math.min(26,gap*.1)*Math.min(1,gap/140)*state.bowSign;
    const c1={x:sx+(ex-sx)/3-dy/distance*bow,y:sy+(ey-sy)/3+dx/distance*bow};
    const c2={x:ex-(ex-sx)/3-dy/distance*bow,y:ey-(ey-sy)/3+dx/distance*bow};
    const straight={sx,sy,ex,ey,c1,c2,len:distance,ra,rb,
      point:t=>({x:(1-t)**3*sx+3*(1-t)**2*t*c1.x+3*(1-t)*t*t*c2.x+t**3*ex,
        y:(1-t)**3*sy+3*(1-t)**2*t*c1.y+3*(1-t)*t*t*c2.y+t**3*ey})};
    const gentleSamples=Array.from({length:33},(_,i)=>straight.point(i/32));
    const blockedStraight=obstacles.some(n=>gentleSamples.slice(1,-1).some(p=>
      p.x>n.x-n.labelWidth/2-5 && p.x<n.x+n.labelWidth/2+5 && p.y>n.y-32 && p.y<n.y-24+n.boxHeight+5));
    const sharedStraight=(link.cables||[]).some(cable=>{
      if((cable.anchors||[]).some(anchor=>{
        if(anchor.id===a.id && a.current)return false;
        const tip=anchor.id===a.id?{x:sx,y:sy}:anchor.id===b.id?{x:ex,y:ey}:null;
        return tip && Math.hypot(tip.x-anchor.point.x,tip.y-anchor.point.y)<12;
      }))return true;
      return gentleSamples.filter((_,i)=>i>=4 && i<=28 && i%2===0).some(p=>
        !cable.shared.some(n=>Math.hypot(p.x-n.x,p.y-n.y)<42) && cable.samples.some(q=>Math.hypot(p.x-q.x,p.y-q.y)<12));
    });
    const outsideStraight=link.limits && gentleSamples.some(p=>
      Math.abs(p.x)>link.limits.width/2-5 || Math.abs(p.y)>link.limits.height/2-5);
    if(!blockedStraight && !sharedStraight && !outsideStraight) {
      state.sourceAngle=Math.atan2(dy,dx);state.targetAngle=Math.atan2(-dy,-dx);
      delete state.sourcePort;delete state.targetPort;state.routeBend=0;
      return straight;
    }
    const make=(p,q,bend=0)=>{
      const blendPort=(port,ux,uy)=>{
        const x=ux+(port.x-ux)*curvature,y=uy+(port.y-uy)*curvature,length=Math.hypot(x,y)||1;
        return {x:x/length,y:y/length};
      };
      p=blendPort(p,dx/distance,dy/distance);q=blendPort(q,-dx/distance,-dy/distance);
      const sx=a.x+p.x*ra,sy=a.y+p.y*ra,ex=b.x+q.x*rb,ey=b.y+q.y*rb;
      const c1={x:sx+p.x*arm,y:sy+p.y*arm},c2={x:ex+q.x*arm,y:ey+q.y*arm};
      c1.x-=dy/distance*bend;c1.y+=dx/distance*bend;
      c2.x-=dy/distance*bend;c2.y+=dx/distance*bend;
      const chord=Math.hypot(ex-sx,ey-sy)||1,ux=(ex-sx)/chord,uy=(ey-sy)/chord;
      c1.x=(sx+(ex-sx)/3)*(1-curvature)+c1.x*curvature;
      c1.y=(sy+(ey-sy)/3)*(1-curvature)+c1.y*curvature;
      c2.x=(ex-(ex-sx)/3)*(1-curvature)+c2.x*curvature;
      c2.y=(ey-(ey-sy)/3)*(1-curvature)+c2.y*curvature;
      const endInset=link.adjacency?chord*.2:0;
      for(const [control,low,high] of [[c1,endInset,chord*.45],[c2,chord*.55,chord-endInset]]) {
        const projection=(control.x-sx)*ux+(control.y-sy)*uy;
        const correction=Math.max(low,Math.min(high,projection))-projection;
        control.x+=ux*correction;control.y+=uy*correction;
      }
      // Keep detours in the same shallow-arc family: no opposing control bends.
      const normal=control=>-(control.x-sx)*uy+(control.y-sy)*ux;
      const n1=normal(c1),n2=normal(c2),sign=Math.sign(n1+n2)||state.bowSign;
      const budget=link.adjacency?Math.min(40,Math.max(4,gap*.2)):Math.min(80,Math.max(8,gap*.65));
      for(const [control,n] of [[c1,n1],[c2,n2]]) {
        // The bend must fade toward each endpoint too. A bounded overall bow
        // alone still permits a sideways tangent and a hook beside the cover.
        const along=(control.x-sx)*ux+(control.y-sy)*uy;
        const tangentBudget=link.adjacency?Math.min(along,chord-along)*.4:budget;
        const limited=sign*Math.min(budget,tangentBudget,Math.abs(n));
        control.x-=uy*(limited-n);control.y+=ux*(limited-n);
      }
      return {sx,sy,ex,ey,c1,c2,len:distance,ra,rb,point:t=>({x:(1-t)**3*sx+3*(1-t)**2*t*c1.x+3*(1-t)*t*t*c2.x+t**3*ex,y:(1-t)**3*sy+3*(1-t)**2*t*c1.y+3*(1-t)*t*t*c2.y+t**3*ey})};
    };
    let selected;
    ports.forEach((p,i)=>ports.forEach((q,j)=>{
      if(cached && (i!==state.sourcePort || j!==state.targetPort))return;
      const facingA=(p.x*dx+p.y*dy)/distance,facingB=-(q.x*dx+q.y*dy)/distance;
      if(facingA<-.05 || facingB<-.05)return;
      for(const bend of cached?[state.routeBend||0]:link.cables?.length?[0,-32,32,-64,64]:[0]) {
      const curve=make(p,q,bend);let blocked=0,outside=0,crossings=0,overlap=0,portOverlap=0;
      const samples=Array.from({length:25},(_,k)=>curve.point(k/24));
      const collisionSamples=link.cables?.length?Array.from({length:13},(_,k)=>curve.point((k+2)/16)):[];
      for(const n of obstacles)for(let k=1;k<24;k++){
        const v=samples[k];
        if(v.x>n.x-n.labelWidth/2-5 && v.x<n.x+n.labelWidth/2+5 && v.y>n.y-32 && v.y<n.y-24+n.boxHeight+5){blocked++;break;}
      }
      for(let k=1;k<24;k++){
        const v=samples[k];
        if(link.limits && (Math.abs(v.x)>link.limits.width/2-5 || Math.abs(v.y)>link.limits.height/2-5))outside++;
        if(group && (Math.abs(v.x-group.x)>group.groupWidth/2-6 || Math.abs(v.y-group.y)>group.groupHeight/2-6))outside++;
      }
      for(const spoke of link.spokes||[])for(let k=0;k<24;k++) {
        const u=samples[k],v=samples[k+1],rx=v.x-u.x,ry=v.y-u.y,sx=spoke.b.x-spoke.a.x,sy=spoke.b.y-spoke.a.y,den=rx*sy-ry*sx;
        if(Math.abs(den)<.00001)continue;
        const px=spoke.a.x-u.x,py=spoke.a.y-u.y,t=(px*sy-py*sx)/den,w=(px*ry-py*rx)/den;
        if(t>0 && t<1 && w>.05 && w<.95){crossings++;break;}
      }
      // Separate cables meeting the same cover instead of merging their tails.
      const fanPenalty=(origin,destination,port)=>{
        const own=Math.atan2(destination.y-origin.y,destination.x-origin.x),portAngle=Math.atan2(port.y,port.x);
        const angular=(x,y)=>Math.abs(Math.atan2(Math.sin(x-y),Math.cos(x-y)));
        return (link.neighbours||[]).filter(n=>n.id!==origin.id && n.id!==destination.id &&
          (link.adjacency?.[origin.id]||[]).includes(n.id)).reduce((sum,n)=>{
            const other=Math.atan2(n.y-origin.y,n.x-origin.x);
            return sum+Math.max(0,angular(portAngle,own)-angular(portAngle,other)+.22)*180;
          },0);
      };
      const fan=fanPenalty(a,b,p)+fanPenalty(b,a,q);
      for(const cable of link.cables||[]) {
      for(const anchor of cable.anchors||[]) {
        if((anchor.id===a.id && a.current)||(anchor.id===b.id && b.current))continue;
        const tip=anchor.id===a.id?{x:curve.sx,y:curve.sy}:anchor.id===b.id?{x:curve.ex,y:curve.ey}:null;
        if(tip)portOverlap+=Math.max(0,16-Math.hypot(tip.x-anchor.point.x,tip.y-anchor.point.y));
      }
      for(const v of collisionSamples) {
        if(cable.shared.some(n=>Math.hypot(v.x-n.x,v.y-n.y)<42))continue;
        let nearest=Infinity;
        for(const p of cable.samples)nearest=Math.min(nearest,Math.hypot(v.x-p.x,v.y-p.y));
        overlap+=Math.max(0,16-nearest);
      }
      }
      const score=blocked*10000+outside*1000+portOverlap*1000+overlap*500+crossings*300+fan+Math.abs(bend)*.2+(2-facingA-facingB)*30+(state.sourcePort!==undefined && state.sourcePort!==i?10:0)+(state.targetPort!==undefined && state.targetPort!==j?10:0);
      if(!selected || score<selected.score)selected={p,q,i,j,score,fan,bend,overlap};
      }
    }));
    if(cached && !selected){delete state.sourcePort;delete state.targetPort;return relationGraphBookCable(a,b,link,group);}
    const angle=(previous,desired,ux,uy)=>{
      if(previous===undefined || Math.cos(previous)*ux+Math.sin(previous)*uy<-.1)return desired;
      const delta=Math.atan2(Math.sin(desired-previous),Math.cos(desired-previous));
      const next=previous+delta*.25;
      // Never take the long way around a cover or rotate through its back side.
      return Math.cos(next)*ux+Math.sin(next)*uy<-.05?desired:next;
    };
    state.sourceAngle=angle(state.sourceAngle,Math.atan2(selected.p.y,selected.p.x),dx/distance,dy/distance);
    state.targetAngle=angle(state.targetAngle,Math.atan2(selected.q.y,selected.q.x),-dx/distance,-dy/distance);
    state.sourcePort=selected.i;state.targetPort=selected.j;
    state.routeBend=selected.bend;
    if(!cached)state.routingGeometry=geometry.map(p=>({x:p.x,y:p.y,captionAbove:Boolean(p.captionAbove),
      captionTop:p.captionTop||0,labelWidth:p.labelWidth||0,boxHeight:p.boxHeight||0}));
    return make({x:Math.cos(state.sourceAngle),y:Math.sin(state.sourceAngle)},{x:Math.cos(state.targetAngle),y:Math.sin(state.targetAngle)},state.routeBend);
  }

  function relationGraphCurve(a, b, group, link = {}) {
    if(link.kind && !link.canonical) {
      const reversed=String(a.id)>String(b.id),state=link.state||{};
      let curve;
      if(link.frame!==undefined && state.frame===link.frame)curve=state.curve;
      else {
        curve=relationGraphCurve(reversed?b:a,reversed?a:b,group,{...link,canonical:true,lane:0});
        if(link.frame!==undefined){state.frame=link.frame;state.curve=curve;}
      }
      return reversed?{...curve,sx:curve.ex,sy:curve.ey,ex:curve.sx,ey:curve.sy,ra:curve.rb,rb:curve.ra,c1:curve.c2,c2:curve.c1,
        point:t=>curve.point(1-t)}:curve;
    }
    const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
    if (b.group && b.outline) return relationGraphGroupConnector(a,b,link);
    if(link.kind || link.spoke)return relationGraphBookCable(a,b,link,group);
    if (link.kind || group?.members?.length === 2) {
      const extent = (n,vx,vy) => {
        if(vy<=0 || Math.abs(vx/vy)*24>(n.labelWidth||64)/2+7)return 32;
        const horizontal=(Math.max(32,(n.labelWidth||64)/2)+7)/(Math.abs(vx/len)||.00001);
        const vertical=(vy>0 ? Math.max(32,(n.boxHeight||104)-24)+8 : 32)/(Math.abs(vy/len)||.00001);
        return Math.min(horizontal,vertical,len*.4);
      };
      const startRadius = extent(a,dx,dy);
      const endRadius = extent(b,-dx,-dy);
      const sx = a.x + dx / len * startRadius, sy = a.y + dy / len * startRadius;
      const ex = b.x - dx / len * endRadius, ey = b.y - dy / len * endRadius;
      const orientation = String(a.id) < String(b.id) ? 1 : -1;
      const lane=Math.sign(link.lane||0)*Math.min(Math.abs(link.lane||0),(len-startRadius-endRadius)*.45);
      let cx = (sx+ex)/2 - dy/len * lane * orientation;
      let cy = (sy+ey)/2 + dx/len * lane * orientation;
      if(link.obstacles?.length) {
        const choices=[lane*orientation,...[-1,1].flatMap(sign=>[48,80,120,180].map(bend=>sign*bend))];
        let best=Infinity;
        for(const bend of choices) {
          const tx=(sx+ex)/2-dy/len*bend,ty=(sy+ey)/2+dx/len*bend;
          let hits=0;
          for(const n of link.obstacles)for(let i=1;i<24;i++) {
            const t=i/24,x=(1-t)**2*sx+2*(1-t)*t*tx+t*t*ex,y=(1-t)**2*sy+2*(1-t)*t*ty+t*t*ey;
            if(x>n.x-n.labelWidth/2-8 && x<n.x+n.labelWidth/2+8 && y>n.y-34 && y<n.y-24+n.boxHeight+8){hits++;break;}
          }
          let outside=0;
          if(link.limits)for(let i=1;i<24;i++){
            const t=i/24,x=(1-t)**2*sx+2*(1-t)*t*tx+t*t*ex,y=(1-t)**2*sy+2*(1-t)*t*ty+t*t*ey;
            if(Math.abs(x)>link.limits.width/2-5 || Math.abs(y)>link.limits.height/2-5)outside++;
          }
          const score=hits*10000+outside*1000+Math.abs(bend-lane*orientation)+(link.state?.bend!==undefined?Math.abs(bend-link.state.bend)*.2:0);
          if(score<best){best=score;cx=tx;cy=ty;}
        }
        if(link.state) {
          const desired=((cy-(sy+ey)/2)*dx-(cx-(sx+ex)/2)*dy)/len;
          link.state.bend=link.state.bend===undefined?desired:link.state.bend+(desired-link.state.bend)*.18;
          cx=(sx+ex)/2-dy/len*link.state.bend;cy=(sy+ey)/2+dx/len*link.state.bend;
        }
      }
      return {sx, sy, ex, ey, cx, cy,
        ra:startRadius, rb:endRadius, len,
        point:t => ({x:(1-t)**2*sx+2*(1-t)*t*cx+t*t*ex, y:(1-t)**2*sy+2*(1-t)*t*cy+t*t*ey})};
    }
    let nx = -dy / len, ny = dx / len;
    let cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2;
    if (group) {
      // A local bend stays stable when a work crosses the group centre.
      const bend = Math.min(24, len * .12) * (String(a.id) < String(b.id) ? 1 : -1);
      cx += nx * bend; cy += ny * bend;
    } else {
      const bend = b.group ? -Math.min(110, len * .28) : Math.min(24, len * .08);
      cx += nx * bend; cy += ny * bend;
    }
    const laneDirection = String(a.id) < String(b.id) ? 1 : -1;
    cx += nx * (link.lane || 0) * laneDirection;
    cy += ny * (link.lane || 0) * laneDirection;
    const radius = (n, vx, vy) => {
      const distance = Math.hypot(vx, vy) || 1;
      if (n.group && n.outline) {
        const ux = vx/distance, uy = vy/distance;
        let hit = Infinity;
        n.outline.forEach((p,i) => {
          const q = n.outline[(i+1)%n.outline.length], ex = q.x-p.x, ey = q.y-p.y;
          const denominator = ux*ey-uy*ex;
          if (Math.abs(denominator) < .00001) return;
          const px = p.x-n.x, py = p.y-n.y;
          const t = (px*ey-py*ex)/denominator, u = (px*uy-py*ux)/denominator;
          if (t >= 0 && u >= 0 && u <= 1) hit = Math.min(hit,t);
        });
        if (Number.isFinite(hit)) return hit;
      }
      return n.group ? 1 / Math.sqrt((vx / distance / (n.groupWidth / 2)) ** 2 + (vy / distance / (n.groupHeight / 2)) ** 2)
        : n.current ? 32 : 25;
    };
    const startLength = Math.hypot(cx - a.x, cy - a.y) || 1, endLength = Math.hypot(cx - b.x, cy - b.y) || 1;
    const ra = radius(a, cx - a.x, cy - a.y) + 7, rb = radius(b, cx - b.x, cy - b.y) + 7;
    const sx = a.x + (cx - a.x) / startLength * ra, sy = a.y + (cy - a.y) / startLength * ra;
    const ex = b.x + (cx - b.x) / endLength * rb, ey = b.y + (cy - b.y) / endLength * rb;
    return { sx, sy, ex, ey, cx, cy, ra, rb, len,
      point: u => ({ x: (1-u)*(1-u)*sx + 2*(1-u)*u*cx + u*u*ex,
        y: (1-u)*(1-u)*sy + 2*(1-u)*u*cy + u*u*ey }) };
  }

  function relationGraphEndMark(curve,distance) {
    const samples=Array.from({length:41},(_,i)=>curve.point(i/40));
    const lengths=samples.slice(1).map((p,i)=>Math.hypot(p.x-samples[i].x,p.y-samples[i].y));
    const total=lengths.reduce((sum,value)=>sum+value,0);
    let remaining=Math.min(distance,total*.65),t=1;
    for(let i=39;i>=0;i--){if(remaining<=lengths[i]){t=(i+1-remaining/(lengths[i]||1))/40;break;}remaining-=lengths[i];}
    const p=curve.point(t),before=curve.point(Math.max(0,t-.005)),after=curve.point(Math.min(1,t+.005));
    const length=Math.hypot(after.x-before.x,after.y-before.y)||1;
    return {...p,t,total,tx:(after.x-before.x)/length,ty:(after.y-before.y)/length};
  }

  const relationGraphCircle=Array.from({length:20},(_,i)=>({x:Math.cos(i*Math.PI/10),y:Math.sin(i*Math.PI/10)}));
  function relationGraphMemberEnvelope(n) {
    const centre=n.current?30:24;
    const top=n.captionAbove?Math.min(-centre,n.captionTop||-96):-24;
    const bottom=n.captionAbove?centre:-24+n.boxHeight;
    return {x:n.x+(n.captionOffset||0)/2,y:n.y+(top+bottom)/2,
      rx:Math.max(32,(n.labelWidth+Math.abs(n.captionOffset||0))/2)*1.35+16,
      ry:(bottom-top)/2*1.35+16};
  }
  function relationGraphBodyLimits(members) {
    let left=Infinity,right=-Infinity,top=Infinity,bottom=-Infinity;
    for(const n of members) {
      const p=relationGraphMemberEnvelope(n);
      left=Math.min(left,p.x-p.rx);right=Math.max(right,p.x+p.rx);top=Math.min(top,p.y-p.ry);bottom=Math.max(bottom,p.y+p.ry);
    }
    const titleX=(left+right)/2;
    for(const n of members)if(n.captionAbove)top=Math.min(top,n.y+(n.captionTop||-96)-24);
    return {left:Math.min(left,titleX-96),right:Math.max(right,titleX+96),top:top-44,bottom};
  }

  function relationGraphFitBody(members,held,limits) {
    if(members.includes(held)) {
      // At a wall, constrain each visible member instead of translating the
      // held book backwards because its companion reached a different wall.
      for(const n of members) {
        const p=relationGraphMemberEnvelope(n),rx=Math.max(p.rx,96);
        const x=Math.max(-limits.width/2+12+rx,Math.min(limits.width/2-12-rx,p.x));
        const y=Math.max(-limits.height/2+56+p.ry,Math.min(limits.height/2-12-p.ry,p.y));
        n.x+=x-p.x;n.y+=y-p.y;
      }
      return;
    }
    const b=relationGraphBodyLimits(members);
    const shift=(low,high,size)=>high-low<=size-24?Math.max(0,-size/2+12-low)-Math.max(0,high-size/2+12):0;
    const dx=shift(b.left,b.right,limits.width),dy=shift(b.top,b.bottom,limits.height);
    for(const n of members){n.x+=dx;n.y+=dy;}
  }
  function relationGraphBodyBounds(id,members) {
    if(id.startsWith('node:')) {
      // A displaced caption is separate from its cover. An enclosing ellipse
      // turns the empty space between them into an invisible repelling body.
      const n=members[0],centre=n.current?30:24,metrics=n.captionMetrics;
      const parts=[relationGraphCircle.map(v=>({x:n.x+v.x*(centre+2),y:n.y+v.y*(centre+2)}))];
      const rectangle=(x,top,width,height)=>[
        {x:x-width/2,y:top},{x:x+width/2,y:top},
        {x:x+width/2,y:top+height},{x:x-width/2,y:top+height}
      ];
      if(metrics) {
        const x=n.x+(n.captionAbove?0:n.captionOffset||0);
        const titleTop=n.captionAbove?n.y-40-metrics.titleHeight:n.y-centre+metrics.titleTop;
        const kindTop=n.captionAbove?titleTop-4-metrics.kindHeight:n.y-centre+metrics.kindTop;
        parts.push(rectangle(x,titleTop,n.labelWidth,metrics.titleHeight),rectangle(x,kindTop,metrics.kindWidth||n.labelWidth,metrics.kindHeight));
      } else {
        const top=n.captionAbove?n.y+(n.captionTop||-96):n.y+32;
        const bottom=n.captionAbove?n.y-40:n.y-centre+n.boxHeight;
        parts.push(rectangle(n.x+(n.captionAbove?0:n.captionOffset||0),top,n.labelWidth,Math.max(1,bottom-top)));
      }
      return {outline:parts.flat(),parts};
    }
    return relationGraphGroupBounds(null,members,[],true);
  }
  function relationGraphBodyContact(a,b) {
    let contact=null;
    for(const left of a.parts||[a.outline])for(const right of b.parts||[b.outline]) {
      const overlap=relationGraphOverlap(left,right,true,a.parts||b.parts?2:8);
      if(overlap && (!contact || overlap.depth>contact.depth))contact=overlap;
    }
    return contact;
  }
  function relationGraphFitNode(n,limits) {
    const {outline}=relationGraphBodyBounds('node:'+n.id,[n]);
    const shift=(low,high,size)=>high-low<=size-24?Math.max(0,-size/2+12-low)-Math.max(0,high-size/2+12):0;
    n.x+=shift(Math.min(...outline.map(p=>p.x)),Math.max(...outline.map(p=>p.x)),limits.width);
    n.y+=shift(Math.min(...outline.map(p=>p.y)),Math.max(...outline.map(p=>p.y)),limits.height);
  }
  function relationGraphFitDragNode(n,limits) {
    // A grouped book must use the same membrane/heading clearance as the
    // held member in the collision solver, including at both corner axes.
    if(n.groupId)relationGraphFitBody([n],n,limits);
    else relationGraphFitNode(n,limits);
  }
  function relationGraphGroupBounds(base, members, neighbours = [],geometryOnly=false,requiredMembers=members) {
    const oval = (x,y,rx,ry) => relationGraphCircle.map(p=>({x:x+p.x*rx,y:y+p.y*ry}));
    const envelopeMembers=requiredMembers===members?members:[...members,...requiredMembers];
    const points = envelopeMembers.flatMap(n => {const p=relationGraphMemberEnvelope(n);return oval(p.x,p.y,p.rx,p.ry);});
    const memberLeft = Math.min(...points.map(p=>p.x)), memberRight = Math.max(...points.map(p=>p.x));
    const memberTop=Math.min(...points.map(p=>p.y),...envelopeMembers.filter(n=>n.captionAbove).map(n=>n.y+(n.captionTop||-96)-24));
    const titleX = (memberLeft+memberRight)/2, titleY = memberTop-14;
    if(base?.caption!==false)points.push(...oval(titleX,titleY,96,30));
    points.sort((a,b)=>a.x-b.x || a.y-b.y);
    const cross = (a,b,c)=>(b.x-a.x)*(c.y-a.y)-(b.y-a.y)*(c.x-a.x);
    const halfHull = list => {
      const hull=[];
      for (const p of list) { while (hull.length>1 && cross(hull[hull.length-2],hull[hull.length-1],p)<=0) hull.pop(); hull.push(p); }
      return hull.slice(0,-1);
    };
    let outline=[...halfHull(points),...halfHull([...points].reverse())];
    if(geometryOnly)return {outline};
    const enclosingOutline=outline;
    const start=outline.reduce((best,p,i)=>p.y<outline[best].y?i:best,0);
    outline=[...outline.slice(start),...outline.slice(0,start)];
    const ownLeft=Math.min(...outline.map(p=>p.x))-24,ownRight=Math.max(...outline.map(p=>p.x))+24;
    const ownTop=Math.min(...outline.map(p=>p.y))-24,ownBottom=Math.max(...outline.map(p=>p.y))+24;
    neighbours=neighbours.flatMap(body=>body.parts?body.parts.map(outline=>({...body,outline})):body).filter(body=>{
      const shape=body.outline;
      return Math.max(...shape.map(p=>p.x))>=ownLeft && Math.min(...shape.map(p=>p.x))<=ownRight
        && Math.max(...shape.map(p=>p.y))>=ownTop && Math.min(...shape.map(p=>p.y))<=ownBottom;
    });
    if(neighbours.length) {
      const segments=outline.map((p,i)=>({p,q:outline[(i+1)%outline.length],length:Math.hypot(outline[(i+1)%outline.length].x-p.x,outline[(i+1)%outline.length].y-p.y)}));
      const perimeter=segments.reduce((sum,s)=>sum+s.length,0);
      outline=Array.from({length:64},(_,i)=>{
        let remaining=i*perimeter/64,segment=segments[0];
        for(const s of segments){segment=s;if(remaining<=s.length)break;remaining-=s.length;}
        const t=remaining/(segment.length||1);
        let p={x:segment.p.x+(segment.q.x-segment.p.x)*t,y:segment.p.y+(segment.q.y-segment.p.y)*t};
        for(const neighbour of neighbours) {
          let inside=false,nearest=null;
          neighbour.outline.forEach((a,j)=>{
            const b=neighbour.outline[(j+1)%neighbour.outline.length];
            if((a.y>p.y)!==(b.y>p.y) && p.x<(b.x-a.x)*(p.y-a.y)/(b.y-a.y)+a.x)inside=!inside;
            const dx=b.x-a.x,dy=b.y-a.y,u=Math.max(0,Math.min(1,((p.x-a.x)*dx+(p.y-a.y)*dy)/(dx*dx+dy*dy||1)));
            const q={x:a.x+dx*u,y:a.y+dy*u},distance=Math.hypot(p.x-q.x,p.y-q.y);
            if(!nearest || distance<nearest.distance)nearest={...q,distance};
          });
          if(nearest && (inside || nearest.distance<20)) {
            const distance=nearest.distance||1,amount=Math.min(46,inside?distance+18:20-distance)*.9;
            p.x+=(inside?nearest.x-p.x:p.x-nearest.x)/distance*amount;
            p.y+=(inside?nearest.y-p.y:p.y-nearest.y)/distance*amount;
          }
        }
        return p;
      });
      for(let pass=0;pass<2;pass++)outline=outline.map((p,i)=>{
        const a=outline[(i+63)%64],b=outline[(i+1)%64];
        return {x:p.x*.7+(a.x+b.x)*.15,y:p.y*.7+(a.y+b.y)*.15};
      });
      const colliders=[...new Set(neighbours.flatMap(body=>body.members||[]))].filter(n=>n.x+n.labelWidth/2+24>=ownLeft && n.x-n.labelWidth/2-24<=ownRight
        && n.y-24+n.boxHeight+24>=ownTop && n.y-(n.current?46:40)-24<=ownBottom);
      if(colliders.length) {
        const centre={x:members.reduce((sum,n)=>sum+n.x,0)/members.length,y:members.reduce((sum,n)=>sum+n.y+n.boxHeight/2-24,0)/members.length};
        const protect=p=>{for(const collider of colliders)p=relationGraphPressPoint(p,collider,centre);return p;};
        outline=outline.map(protect);
        outline=relationGraphResample(outline,128).map(protect);
      }
    }
    outline=relationGraphUntangle(outline);
    const inside=p=>{let result=false;for(let i=0,j=outline.length-1;i<outline.length;j=i++) {
      const a=outline[i],b=outline[j];if((a.y>p.y)!==(b.y>p.y)&&p.x<(b.x-a.x)*(p.y-a.y)/(b.y-a.y)+a.x)result=!result;
    }return result;};
    // Contact may dent the padding, but must never cut a book or its captions
    // out of its own body while the physical particles are still separating.
    const protectedPoints=requiredMembers.flatMap(n=>{
      const centre=n.current?30:24,top=n.captionAbove?(n.captionTop||-96):-centre,bottom=n.captionAbove?centre:-centre+n.boxHeight;
      const x=n.x+(n.captionOffset||0)/2,half=(n.labelWidth+Math.abs(n.captionOffset||0))/2;
      return [{x:x-half,y:n.y+top},{x:x+half,y:n.y+top},{x:x-half,y:n.y+bottom},{x:x+half,y:n.y+bottom}];
    });
    if(protectedPoints.some(p=>!inside(p)))outline=relationGraphResample(enclosingOutline,Math.max(64,outline.length));
    return relationGraphContourBounds(outline);
  }

  function relationGraphContourBounds(outline) {
    const left=Math.min(...outline.map(p=>p.x)), right=Math.max(...outline.map(p=>p.x));
    const top=Math.min(...outline.map(p=>p.y)), bottom=Math.max(...outline.map(p=>p.y));
    const local=outline.map(p=>({x:p.x-left,y:p.y-top}));
    const first=local[0], last=local[local.length-1];
    const path=`M ${(first.x+last.x)/2} ${(first.y+last.y)/2}`+local.map((p,i)=>{
      const next=local[(i+1)%local.length];
      return ` Q ${p.x} ${p.y} ${(p.x+next.x)/2} ${(p.y+next.y)/2}`;
    }).join('')+' Z';
    return {x:(left+right)/2,y:(top+bottom)/2,width:right-left,height:bottom-top,path,outline};
  }

  function relationGraphContactContours(a,b,bodyA,bodyB) {
    if(Math.abs(a.x-b.x)>(a.width+b.width)/2+4 || Math.abs(a.y-b.y)>(a.height+b.height)/2+4)return [a,b];
    let best=null;
    for(const shape of [bodyA,bodyB])for(let i=0;i<shape.length;i++) {
      const p=shape[i],q=shape[(i+1)%shape.length],length=Math.hypot(q.x-p.x,q.y-p.y)||1;
      let nx=-(q.y-p.y)/length,ny=(q.x-p.x)/length,amin=Infinity,amax=-Infinity,bmin=Infinity,bmax=-Infinity;
      for(const p of bodyA){const v=p.x*nx+p.y*ny;amin=Math.min(amin,v);amax=Math.max(amax,v);}
      for(const p of bodyB){const v=p.x*nx+p.y*ny;bmin=Math.min(bmin,v);bmax=Math.max(bmax,v);}
      let gap=bmin-amax;
      if(amin-bmax>gap){gap=amin-bmax;nx=-nx;ny=-ny;amax=-amin;bmin=-bmax;}
      if(!best || gap>best.gap)best={nx,ny,gap,plane:(amax+bmin)/2};
    }
    if(best.gap<4)return [a,b];
    const clip=(outline,sign)=>{
      const result=[],distance=p=>sign*(p.x*best.nx+p.y*best.ny-best.plane)+2;
      for(let i=0;i<outline.length;i++) {
        const p=outline[i],q=outline[(i+1)%outline.length],dp=distance(p),dq=distance(q);
        if(dp<=0)result.push(p);
        if((dp<=0)!==(dq<=0)){const t=dp/(dp-dq);result.push({x:p.x+(q.x-p.x)*t,y:p.y+(q.y-p.y)*t});}
      }
      return result.length>=3?relationGraphContourBounds(result):null;
    };
    return [clip(a.outline,1)||a,clip(b.outline,-1)||b];
  }

  function relationGraphViewportContour(bounds,width,height) {
    let outline=bounds.outline;
    for(const [nx,ny,limit] of [[1,0,width/2-8],[-1,0,width/2-8],[0,1,height/2-8],[0,-1,height/2-8]]) {
      if(outline.every(p=>p.x*nx+p.y*ny<=limit))continue;
      const clipped=[];
      for(let i=0;i<outline.length;i++) {
        const p=outline[i],q=outline[(i+1)%outline.length],dp=p.x*nx+p.y*ny-limit,dq=q.x*nx+q.y*ny-limit;
        if(dp<=0)clipped.push(p);
        if((dp<=0)!==(dq<=0)){const t=dp/(dp-dq);clipped.push({x:p.x+(q.x-p.x)*t,y:p.y+(q.y-p.y)*t});}
      }
      if(clipped.length>=3)outline=clipped;
    }
    return outline===bounds.outline?bounds:relationGraphContourBounds(outline);
  }

  function relationGraphOverlap(a,b,withDirection=false,clearance=8) {
    if(Math.max(...a.map(p=>p.x))+clearance<=Math.min(...b.map(p=>p.x)) || Math.max(...b.map(p=>p.x))+clearance<=Math.min(...a.map(p=>p.x))
      || Math.max(...a.map(p=>p.y))+clearance<=Math.min(...b.map(p=>p.y)) || Math.max(...b.map(p=>p.y))+clearance<=Math.min(...a.map(p=>p.y)))return 0;
    let depth=Infinity,separation=null;
    for (const polygon of [a,b]) for(let i=0;i<polygon.length;i++) {
      const p=polygon[i],q=polygon[(i+1)%polygon.length],length=Math.hypot(q.x-p.x,q.y-p.y)||1;
      const nx=-(q.y-p.y)/length,ny=(q.x-p.x)/length;
      let amin=Infinity,amax=-Infinity,bmin=Infinity,bmax=-Infinity;
      for(const v of a){const p=v.x*nx+v.y*ny;amin=Math.min(amin,p);amax=Math.max(amax,p);}
      for(const v of b){const p=v.x*nx+v.y*ny;bmin=Math.min(bmin,p);bmax=Math.max(bmax,p);}
      const overlap=Math.min(amax,bmax)-Math.max(amin,bmin)+clearance;
      if(overlap<=0)return 0;
      depth=Math.min(depth,overlap);
      // Match the correction to the measured penetration. An extra fixed kick
      // at almost-zero contact fights wall/spring constraints and chatters.
      if(withDirection){const positive=bmax-amin+clearance,negative=amax-bmin+clearance,distance=positive<negative?positive:-negative;
        if(!separation || Math.abs(distance)<separation.depth)separation={nx:nx*Math.sign(distance),ny:ny*Math.sign(distance),depth:Math.abs(distance)};}
    }
    return withDirection?separation:depth;
  }

  function relationGraphSoftLinks(particles,edges=[]) {
    const links=[],seen=new Set();
    for(const n of particles)if(n.groupId){n.bodyRestX=n.x;n.bodyRestY=n.y;}
    for(const a of particles) {
      const nearby=particles.filter(b=>b!==a && (a.groupId ? b.groupId===a.groupId : !b.groupId && edges.some(e=>
        (e.source===a.id && e.target===b.id)||(e.target===a.id && e.source===b.id))))
        .sort((b,c)=>Math.hypot(b.x-a.x,b.y-a.y)-Math.hypot(c.x-a.x,c.y-a.y)).slice(0,2);
      for(const b of nearby) {
        const key=[a.id,b.id].sort().join(':');if(seen.has(key))continue;seen.add(key);
        links.push({a,b,length:Math.hypot(b.x-a.x,b.y-a.y)});
      }
    }
    return links;
  }

  function relationGraphCommitPose(particles,links) {
    // A drop accepts the new spacing. Keeping the initial spring lengths makes
    // companions rearrange themselves for seconds after the pointer is released.
    for(const n of particles)if(n.groupId){n.bodyRestX=n.x;n.bodyRestY=n.y;}
    for(const link of links)link.length=Math.hypot(link.b.x-link.a.x,link.b.y-link.a.y);
  }

  function relationGraphSoftStep(particles,links,held,limits) {
    const previous=particles.map(n=>({x:n.x,y:n.y}));
    const move=(n,x,y)=>{if(n===held)return;n.x+=x;n.y+=y;};
    const shapeGroups=new Map();
    for(const n of particles)if(n.groupId && Number.isFinite(n.bodyRestX)){if(!shapeGroups.has(n.groupId))shapeGroups.set(n.groupId,[]);shapeGroups.get(n.groupId).push(n);}
    for(let pass=0;pass<3;pass++) {
      // Preserve the broad shape of a multi-book body with soft springs. A
      // chain of distance-only springs otherwise folds a ring into one column.
      for(const members of shapeGroups.values())if(members.length>=4) {
        let tx=0,ty=0,total=0;
        for(const n of members){const weight=n===held?members.length-1:1;tx+=(n.x-n.bodyRestX)*weight;ty+=(n.y-n.bodyRestY)*weight;total+=weight;}
        tx/=total;ty/=total;
        for(const n of members)move(n,Math.max(-3,Math.min(3,(n.bodyRestX+tx-n.x)*.06)),Math.max(-3,Math.min(3,(n.bodyRestY+ty-n.y)*.06)));
      }
      // Position corrections are damped directly: no velocity or perpetual force.
      for(const {a,b,length} of links) {
        const dx=b.x-a.x,dy=b.y-a.y,distance=Math.hypot(dx,dy)||1;
        const stretchedGroup=a.groupId && a.groupId===b.groupId && distance>length+32;
        const correction=stretchedGroup?Math.min(8,(distance-length-32)*.35):Math.max(-1.8,Math.min(1.8,(distance-length)*.02));
        const weights=(a===held?0:1)+(b===held?0:1)||1;
        move(a,dx/distance*correction/weights,dy/distance*correction/weights);
        move(b,-dx/distance*correction/weights,-dy/distance*correction/weights);
      }
      for(let i=0;i<particles.length;i++)for(let j=i+1;j<particles.length;j++) {
        const a=particles[i],b=particles[j];
        // Different bodies use their visible cover/caption parts below. This
        // local spacing guard is only for members sharing one membrane.
        if(!a.groupId || a.groupId!==b.groupId)continue;
        const ae=relationGraphMemberEnvelope(a),be=relationGraphMemberEnvelope(b),dx=be.x-ae.x,dy=be.y-ae.y;
        const extra=(a.groupId||'ordinary')===(b.groupId||'ordinary')?10:26;
        const rx=(a.labelWidth+b.labelWidth)/2+extra,ry=(ae.ry+be.ry-32)/1.35+extra;
        const normal=Math.hypot(dx/rx,dy/ry);
        if(normal>=1)continue;
        const distance=Math.hypot(dx,dy)||1,nx=distance>1?dx/distance:(i%2?1:-1),ny=distance>1?dy/distance:0;
        const correction=Math.min(7,(1-normal)*Math.min(rx,ry)*.55),weights=(a===held?0:1)+(b===held?0:1)||1;
        move(a,-nx*correction/weights,-ny*correction/weights);move(b,nx*correction/weights,ny*correction/weights);
      }
      const bodies=new Map();
      for(const n of particles){const id=n.groupId||'node:'+n.id;if(!bodies.has(id))bodies.set(id,[]);bodies.get(id).push(n);}
      const shapes=[...bodies].map(([id,members])=>({id,members,...relationGraphBodyBounds(id,members)}));
      for(let i=0;i<shapes.length;i++)for(let j=i+1;j<shapes.length;j++) {
        const a=shapes[i],b=shapes[j],separation=relationGraphBodyContact(a,b);if(!separation)continue;
        const {nx,ny,depth}=separation,amount=Math.min(12,depth*.35);
        let contact=held && (a.members.includes(held)||b.members.includes(held))?{x:held.x,y:held.y+held.boxHeight/2-24}:null;
        if(!contact) {
          let nearest=Infinity;
          for(const p of a.members)for(const q of b.members) {
            const distance=Math.hypot(p.x-q.x,p.y-q.y);
            if(distance<nearest){nearest=distance;contact={x:(p.x+q.x)/2,y:(p.y+q.y)/2+(p.boxHeight+q.boxHeight)/4-24};}
          }
        }
        const heldA=held&&a.members.includes(held),heldB=held&&b.members.includes(held);
        const blocked=(body,x,y)=>body.outline.some(p=>Math.abs(p.x+x)>limits.width/2-12 || Math.abs(p.y+y)>limits.height/2-12);
        if(heldA && blocked(b,-nx*amount,-ny*amount)){held.x+=nx*amount;held.y+=ny*amount;}
        if(heldB && blocked(a,nx*amount,ny*amount)){held.x-=nx*amount;held.y-=ny*amount;}
        for(const n of a.members){let weight=Math.exp(-Math.hypot(n.x-contact.x,n.y+n.boxHeight/2-24-contact.y)/110);if(heldB)weight=.5+weight*.5;move(n,nx*amount*weight,ny*amount*weight);}
        for(const n of b.members){let weight=Math.exp(-Math.hypot(n.x-contact.x,n.y+n.boxHeight/2-24-contact.y)/110);if(heldA)weight=.5+weight*.5;move(n,-nx*amount*weight,-ny*amount*weight);}
      }
      // Use the same visible bounds for drag and collision corrections. A
      // second, different clamp in the painter would undo this every frame.
      for(const n of particles)if(!n.groupId)relationGraphFitNode(n,limits);
      // A compact viewport must constrain the whole membrane, including its
      // heading, rather than only the individual covers inside it.
      for(const [id,members] of bodies) {
        if(id.startsWith('node:'))continue;
        relationGraphFitBody(members,held,limits);
      }
    }
    // Resolve the small residual overlap left by local elastic corrections.
    // Move the free body as a whole only at contact; retain each body's local
    // deformation, rather than allowing two compact skins to pass through.
    const grouped=new Map();
    for(const n of particles)if(n.groupId){if(!grouped.has(n.groupId))grouped.set(n.groupId,[]);grouped.get(n.groupId).push(n);}
    const groups=[...grouped];
    for(let pass=0;pass<3;pass++)for(let i=0;i<groups.length;i++)for(let j=i+1;j<groups.length;j++) {
      const [aid,a]=groups[i],[bid,b]=groups[j],al=relationGraphBodyLimits(a),bl=relationGraphBodyLimits(b);
      if(al.right+12<bl.left || bl.right+12<al.left || al.bottom+12<bl.top || bl.bottom+12<al.top)continue;
      const as=relationGraphBodyBounds(aid,a),bs=relationGraphBodyBounds(bid,b);
      const overlap=relationGraphOverlap(as.outline,bs.outline,true);if(!overlap)continue;
      const distance=Math.min(overlap.depth>16?2:4,overlap.depth),dx=overlap.nx*distance,dy=overlap.ny*distance;
      const free=(shape,x,y)=>shape.outline.every(p=>Math.abs(p.x+x)<=limits.width/2-12 && Math.abs(p.y+y)<=limits.height/2-12);
      const moveBody=(members,x,y)=>{for(const n of members){n.x+=x;n.y+=y;}};
      if(!b.includes(held) && free(bs,-dx,-dy))moveBody(b,-dx,-dy);
      else if(free(as,dx,dy))moveBody(a,dx,dy);
      else if(free(bs,-dx,-dy))moveBody(b,-dx,-dy);
    }
    let movement=0;
    // Two-book bodies must stay compact even when wall and contact corrections
    // oppose their spring. Apply this after those corrections, not before them.
    for(const {a,b,length} of links)if(a.groupId && a.groupId===b.groupId && grouped.get(a.groupId)?.length===2) {
      const dx=b.x-a.x,dy=b.y-a.y,distance=Math.hypot(dx,dy),excess=distance-length-32;
      if(excess>0) {
        const weightA=a===held?0:b===held?1:.5,weightB=1-weightA;
        a.x+=dx/distance*excess*weightA;a.y+=dy/distance*excess*weightA;
        b.x-=dx/distance*excess*weightB;b.y-=dy/distance*excess*weightB;
      }
    }
    // Pair constraints can move the companion beyond a wall. Fit the complete
    // body again afterwards so viewport clipping cannot slice through a cover.
    for(const members of grouped.values())relationGraphFitBody(members,held,limits);
    particles.forEach((n,i)=>{movement=Math.max(movement,Math.hypot(n.x-previous[i].x,n.y-previous[i].y));n.fx=n.x;n.fy=n.y;});
    return movement;
  }

  async function renderRelationGraph(works, graphData = {}) {
    const section = $('[data-relation-graph-section]');
    if (!section) return;
    relationGraphInput={works,graphData};
    relationGraph?.destroy();
    const stage = $('[data-relation-graph-stage]'), status = $('[data-relation-graph-status]');
    stage.replaceChildren(); status.hidden = true;
    const motionEnabled=relationGraphSettings?.motionEnabled!==false;
    stage.dataset.motion=String(motionEnabled);
    const displaySettings=relationGraphSettings && {...relationGraphSettings,enabled:graphData.recommendations?relationGraphSettings.recommendationsEnabled:relationGraphSettings.enabled};
    section.querySelector('h2').textContent=graphData.recommendations?'추천 관계도':'관계도';
    section.hidden = !works.length || !relationGraphVisible(displaySettings,relationGraphMobile.matches);
    if (section.hidden) { relationGraph = null; return; }
    const topology = relationGraphTopology(works, graphData, detailTitle());
    const nodes = [{ id: 'current', label: detailTitle(), kind: '현재 작품',relationType:works.some(work=>work.type==='original')?'adaptation':undefined,
      cover: meta.cover_image || books[0]?.cover_image, current: true },
      ...works.map((work, index) => ({ id: String(index), label: work.book.display_name || work.book.series_name || '제목 없음',
        cover: work.book.cover, kind: work.label, relationType: work.type, book: work.book,
        recommendationTone: graphData.recommendations?relationGraphRecommendationTone(work.book,index):undefined,
        shortLabel: String(work.book.display_name || work.book.series_name || '').replace(detailTitle(), '').replace(/^[:：\s·\-]+/u, '').trim() }))];
    for (const [index, group] of topology.groups.entries()) {
      for (const [step, id] of group.members.entries()) { const n = nodes.find(n => n.id === id); n.groupId = group.id;n.groupCategory=group.category; n.groupIndex = index; n.step = group.sequence ? step + 1 : null; if(group.category!=='focused-family')n.shortLabel = n.label.slice(group.prefix.length).replace(/^[:：\s·\-]+/u, '') || n.label; }
      nodes.push({ id: group.id, label: group.label, group: true, groupIndex: index, members: group.members, sequence: group.sequence,category:group.category });
    }
    const canvas = node('div', 'ds-relation-graph-canvas');
    const layer = node('div', 'ds-relation-graph-nodes');
    const content=node('div','ds-relation-graph-content');
    content.style.transformOrigin='0 0';content.style.position='relative';
    content.append(canvas,layer);stage.append(content);
    const preview = node('div', 'ds-relation-preview');
    preview.id = 'rabbit-relation-preview-' + Math.random().toString(36).slice(2);
    preview.setAttribute('role', 'tooltip'); preview.hidden = true; content.append(preview);
    let previewNode = null, previewWidth = 270, previewHeight = 180;
    let graph = null, dead = false, resize = null, visibility = null, drag = null;
    let width = 0, height = 0, availableWidth=0,displayScale=1, layout = null, lineColor = '', accentColor = '', suppressClick = false;
    let highlighted = null, stageVisible = true, labelBounds = [], graphFont = '';
    let softLinks=[],settleFrames=0,quietFrames=0,releasedSpring=null,settleAnchor=null,settleUntil=0;
    const relationRoutes=new Map(),renderedCables=new Map();let edgeFrame=0;
    const motionEpoch=performance.now()/1000;
    const profileEnabled=new URLSearchParams(location.search).has('rabbitGraphProfile');
    const profile={idle:[],drag:[],last:0,mode:'',published:0};
    let lastTheme = 0;
    // This graph has its own user setting. The default remains animated even
    // when the browser inherits Windows' reduced-motion preference.
    const reducedMotion = !motionEnabled;
    const links = relationGraphLinks(nodes, topology);
    for(const n of nodes){n.extraRelations=relationGraphNodeRelations(n,links,nodes);n.peerDescriptions=relationGraphPeerDescriptions(n,links,nodes);}
    const adjacency={};
    for(const l of links)for(const [a,b] of [[l.source,l.target],[l.target,l.source]]) {
      if(!adjacency[a])adjacency[a]=[];if(!adjacency[a].includes(b))adjacency[a].push(b);
    }
    // Relationship captions travel with their target books, never the cable.
    const linkLabels = links.map(() => null);
    const controller = { destroy() {
      if (dead) return; dead = true; resize?.disconnect(); visibility?.disconnect();
      themeObserver.disconnect(); detachObserver.disconnect();
      hidePreview();
      document.removeEventListener('visibilitychange', updateAnimation);
      graph?._destructor();
    } };
    relationGraph = controller;
    function wakeSoftBody(){settleFrames=240;quietFrames=0;settleUntil=performance.now()+2600;}
    function shown(n) {return n.group?n:{...n,x:n.x+(n.floatX||0),y:n.y+(n.floatY||0)};}
    function positionPreview() {
      if (!previewNode || preview.hidden) return;
      const target=shown(previewNode);
      const p = graph ? graph.graph2ScreenCoords(target.x, target.y)
        : { x: target.x + width / 2, y: target.y + height / 2 };
      const preferredLeft = p.x + 38 + previewWidth > width - 8 ? p.x - previewWidth - 38 : p.x + 38;
      const left = Math.max(8, Math.min(width - previewWidth - 8, preferredLeft));
      const preferredTop = p.y - previewHeight - 30 < 8 ? p.y + 40 : p.y - previewHeight - 30;
      const top = Math.max(8, Math.min(height - previewHeight - 8, preferredTop));
      preview.style.transform = `translate(${left}px, ${top}px)`;
    }
    function showPreview(n) {
      if (drag) return;
      previewNode = n; preview.replaceChildren();
      preview.style.setProperty('--graph-tone', n.inkColor || accentColor);
      const image = document.createElement('img');
      const cover = safeUrl(n.cover, true);
      if (cover) { image.src = cover; image.alt = ''; preview.append(image); }
      const info = node('div', 'ds-relation-preview-info');
      info.append(node('span', 'ds-relation-preview-kind', n.kind), node('strong', '', n.label));
      for(const description of n.peerDescriptions)info.append(node('span','ds-relation-preview-series',description));
      if (n.groupId) info.append(node('span', 'ds-relation-preview-series', nodes.find(item => item.id === n.groupId).label + (n.step ? ' · ' + n.step + '번째' : '')));
      info.append(node('small', '', n.current ? '현재 보고 있는 작품' : '클릭하여 작품 상세 보기'));
      preview.append(info); preview.hidden = false;
      n.element.setAttribute('aria-describedby', preview.id);
      previewWidth = preview.offsetWidth; previewHeight = preview.offsetHeight; positionPreview();
    }
    function hidePreview() {
      previewNode?.element.removeAttribute('aria-describedby');
      previewNode = null; preview.hidden = true;
    }
    function readTheme() {
      lineColor = getComputedStyle(stage).color;
      accentColor = getComputedStyle(root).getPropertyValue('--ds-accent').trim() || lineColor;
      graphFont = getComputedStyle(root).fontFamily;
      for (const n of nodes) n.inkColor = n.group ? getComputedStyle(n.outlineElement).stroke : getComputedStyle(n.art).borderTopColor;
      accentColor = nodes[0].inkColor || accentColor;
      if (previewNode) preview.style.setProperty('--graph-tone', previewNode.inkColor || accentColor);
    }
    const themeObserver = new MutationObserver(readTheme);
    themeObserver.observe(document.documentElement, { attributes: true });
    themeObserver.observe(document.body, { attributes: true });
    const detachObserver = new MutationObserver(() => { if (!root.isConnected) controller.destroy(); });
    detachObserver.observe(container.parentElement || document.body, { childList: true });
    function fit() {
      if (!root.isConnected) { controller.destroy(); return; }
      const nextWidth = stage.clientWidth;
      if (!nextWidth || nextWidth === availableWidth) return;
      availableWidth=nextWidth;
      const viewport=relationGraphViewport(availableWidth,works.length+1,topology.groups.length>0);
      width=viewport.width;displayScale=viewport.scale;
      layout = graphData.recommendations?relationGraphRecommendationLayout(nodes.length,width)
        :topology.groups.length ? relationGraphGroupedLayout(nodes, width, topology.groups) : relationGraphBranchLayout(nodes,width,topology.extraEdges);
      height = layout.height+(topology.groups.length || nodes.length>5?72:24);
      stage.style.height = height*displayScale + 'px';
      stage.dataset.scale=String(displayScale);
      content.style.width=width+'px';content.style.height=height+'px';
      content.style.transform=`scale(${displayScale})`;
      nodes.forEach((n, i) => {
        [n.ax, n.ay] = layout.positions[i]; n.x = n.ax; n.y = n.ay; n.vx = 0; n.vy = 0;
        n.fx=n.x; n.fy=n.y;
        n.floatX=n.floatY=n.floatStrength=0;
        const group = n.groupId ? topology.groups.find(item => item.id === n.groupId) : null;
        n.labelWidth = group?.members.length === 2
          ? Math.min(112, layout.groupBoxes.get(group.id).width - 32)
          : n.groupId ? 80 : layout.labelWidth;
        n.element.style.width = n.labelWidth + 'px';
        if (!n.group) {
          n.boxHeight=n.element.offsetHeight||101;
          n.captionMetrics={titleHeight:n.titleElement.offsetHeight,kindHeight:n.kindElement.offsetHeight,kindWidth:n.kindElement.offsetWidth,titleTop:n.titleElement.offsetTop,kindTop:n.kindElement.offsetTop};
          n.captionPlacement=null;
          n.captionRecipe=null;
          n.captionAbove=false;n.captionOffset=0;n.captionTop=32;
        }
        if (n.group) {
          const box = layout.groupBoxes.get(n.id); n.groupWidth = box.width; n.groupHeight = box.height;
          n.baseBounds = {x:n.ax,y:n.ay,width:box.width,height:box.height};
          n.smoothedMembers = null;
          n.element.style.width = box.width + 'px'; n.element.style.height = box.height + 'px';
          n.element.classList.toggle('ds-relation-cluster-vertical', n.members.length === 2 && box.width < 382);
          n.fx = n.ax; n.fy = n.ay;
        }
      });
      softLinks=relationGraphSoftLinks(nodes.filter(n=>!n.group),topology.extraEdges);settleFrames=0;quietFrames=0;releasedSpring=null;
      for(const link of links){for(const key of ['cableAnchor','portIndex','sourcePort','targetPort','sourceAngle','targetAngle','routeBend','bowSign'])delete link[key];}
      relationRoutes.clear();
      if (graph) {
        graph.width(width).height(height).centerAt(0, 0).zoom(1);
        graph.d3ReheatSimulation();
      }
      paintNodes();
      // Fit the actual covers, captions and membranes once per resize. Keeping
      // this out of the animation loop prevents a moving or dragged graph from
      // changing its own viewport height on every frame.
      const stageRect=stage.getBoundingClientRect();
      const boxes=[...layer.querySelectorAll('.ds-relation-node-art,.ds-relation-node-title,.ds-relation-node-kind,.ds-relation-cluster-shape')]
        .map(element=>element.getBoundingClientRect()).filter(rect=>rect.width && rect.height);
      if(boxes.length) {
        const top=(Math.min(...boxes.map(rect=>rect.top))-stageRect.top)/displayScale;
        const bottom=(Math.max(...boxes.map(rect=>rect.bottom))-stageRect.top)/displayScale;
        const fitted=Math.max(150,Math.ceil(bottom-top+48)),shift=height/2-(top+bottom)/2;
        if(Math.abs(fitted-height)>12) {
          for(const n of nodes) {
            n.y+=shift;n.ay+=shift;n.fy=n.y;
            if(n.baseBounds)n.baseBounds.y+=shift;
            for(const p of n.smoothedMembers||[])p.y+=shift;
          }
          height=fitted;stage.style.height=height*displayScale+'px';content.style.height=height+'px';
          if(graph)graph.height(height).centerAt(0,0);
          paintNodes();
        }
      }
    }
    function updateCaptions(interactive=false) {
      for(const n of nodes.filter(n=>!n.group)) {
        if(!interactive && n.captionRecipe)continue;
        // Use the requested cover position for edge clearance before the old
        // caption clamps the book short of the edge and masks the overflow.
        const radius=(n.current?30:24)+12;
        const requestedY=drag?.n===n && Number.isFinite(drag.requestedY)
          ?Math.max(-height/2+radius,Math.min(height/2-radius,drag.requestedY)):undefined;
        const neighbours=nodes.filter(peer=>peer.id!==n.id && !peer.group).map(shown);
        const captionPeers=nodes.filter(peer=>(adjacency[n.id]||[]).includes(peer.id)).map(shown);
        const caption=relationGraphUpdateCaption(shown(n),neighbours,
          {width,height:interactive?height:undefined,requestedY,captionPeers},interactive);
        n.captionRecipe=caption;
        n.captionAbove=caption.above;n.captionOffset=caption.x;
        n.captionTop=caption.above?-44-n.captionMetrics.titleHeight-n.captionMetrics.kindHeight:32;
      }
    }
    function paintNodes() {
      updateCaptions();
      for(const group of nodes.filter(n=>n.group)) {
        const members=nodes.filter(n=>n.groupId===group.id).map(shown);
        if(!group.smoothedMembers)group.smoothedMembers=members.map(n=>({...n,vx:0,vy:0}));
        members.forEach((n,i)=>{const p=group.smoothedMembers[i];relationGraphElasticPoint(p,n,reducedMotion||drag?.n.id===n.id||releasedSpring?.n.id===n.id);p.labelWidth=n.labelWidth;p.boxHeight=n.boxHeight;p.captionAbove=n.captionAbove;p.captionTop=n.captionTop;p.captionOffset=n.captionOffset;});
      }
      const bodies=new Map();
      for(const n of nodes.filter(n=>!n.group).map(shown)){const id=n.groupId||'node:'+n.id;if(!bodies.has(id))bodies.set(id,[]);bodies.get(id).push(n);}
      const pressure=[...bodies].map(([id,members])=>({id,members,...relationGraphBodyBounds(id,members)}));
      const contactGroups=new Set();
      for(let i=0;i<pressure.length;i++)for(let j=i+1;j<pressure.length;j++)if(!pressure[i].id.startsWith('node:')&&!pressure[j].id.startsWith('node:')&&relationGraphOverlap(pressure[i].outline,pressure[j].outline)){
        contactGroups.add(pressure[i].id);contactGroups.add(pressure[j].id);
      }
      for(const group of nodes.filter(n=>n.group && contactGroups.has(n.id)))for(const p of group.smoothedMembers){const actual=shown(nodes.find(n=>n.id===p.id));p.x=actual.x;p.y=actual.y;p.vx=p.vy=0;}
      for(const body of pressure)if(contactGroups.has(body.id))Object.assign(body,relationGraphBodyBounds(body.id,body.members));
      const skins=nodes.filter(n=>n.group).map(group=>({group,bounds:relationGraphGroupBounds(group.baseBounds,group.smoothedMembers,pressure.filter(body=>body.id!==group.id),false,nodes.filter(n=>n.groupId===group.id).map(shown))}));
      for(let i=0;i<skins.length;i++)for(let j=i+1;j<skins.length;j++) {
        const a=skins[i],b=skins[j];
        [a.bounds,b.bounds]=relationGraphContactContours(a.bounds,b.bounds,pressure.find(p=>p.id===a.group.id).outline,pressure.find(p=>p.id===b.group.id).outline);
      }
      for (const skin of skins) {
        const group=skin.group,bounds=relationGraphViewportContour(skin.bounds,width,height);
        group.x = group.fx = bounds.x; group.y = group.fy = bounds.y;
        group.groupWidth = bounds.width; group.groupHeight = bounds.height;
        group.outline = bounds.outline;
        group.element.style.width = bounds.width + 'px'; group.element.style.height = bounds.height + 'px';
        group.outlineElement.setAttribute('d', bounds.path);
        group.element.style.setProperty('--graph-outline', `path("${bounds.path}")`);
      }
      for (const n of nodes) {
        const target=shown(n);
        if(!n.group) {
          const caption=n.captionRecipe;
          n.captionAbove=caption.above;n.captionOffset=caption.x;
          const title=n.titleElement,kind=n.kindElement,centre=n.current?30:24;
          const metrics=n.captionMetrics;
          const placement=(n.captionAbove?'above':'below')+':'+n.captionOffset;
          if(n.captionPlacement!==placement) {
          if(n.captionAbove) {
            const titleTop=centre-40-metrics.titleHeight,kindTop=titleTop-4-metrics.kindHeight;
            title.style.transform=`translateY(${titleTop-metrics.titleTop}px)`;
            kind.style.transform=`translateY(${kindTop-metrics.kindTop}px)`;
            n.captionTop=kindTop-centre;
          } else {
            title.style.transform=`translateX(${n.captionOffset}px)`;kind.style.transform=`translateX(${n.captionOffset}px)`;n.captionTop=32;
          }
          n.element.dataset.captionPosition=n.captionAbove?'above':'below';
          n.captionPlacement=placement;
          }
        }
        const p = graph ? graph.graph2ScreenCoords(target.x, target.y) : { x: target.x + width / 2, y: target.y + height / 2 };
        const transform = n.group
          ? `translate(${p.x - n.groupWidth / 2}px, ${p.y - n.groupHeight / 2}px)`
          : `translate(${p.x - n.labelWidth / 2}px, ${p.y - (n.current ? 30 : 24)}px)`;
        if(n.element.style.transform!==transform)n.element.style.transform=transform;
      }
      positionPreview();
    }
    function endDrag(event) {
      if (!drag || event.pointerId !== drag.id) return;
      const released=drag,{ n, moved } = released; drag = null; suppressClick = moved;
      if(moved)relationGraphCommitPose(nodes.filter(member=>!member.group),softLinks);
      n.element.classList.remove('is-dragging');
      if (n.element.hasPointerCapture(event.pointerId)) n.element.releasePointerCapture(event.pointerId);
      releasedSpring=moved?relationGraphReleaseSpring(released):null;
      settleAnchor=moved?n:null;
      n.x=n.ax=n.fx; n.y=n.ay=n.fy; n.vx=0; n.vy=0;
      if(moved)wakeSoftBody();
      if(profileEnabled)stage.dataset.frameProfile=JSON.stringify({idle:relationGraphFrameStats(profile.idle),drag:relationGraphFrameStats(profile.drag)});
      graph?.d3ReheatSimulation();
    }
    function updateAnimation() {
      if (!graph || dead) return;
      const running = stageVisible && !document.hidden;
      stage.dataset.running = String(running);
      if (running) graph.resumeAnimation(); else graph.pauseAnimation();
    }
    document.addEventListener('visibilitychange', updateAnimation);
    for (const n of nodes) {
      if (n.group) {
        const cluster = node('div', 'ds-relation-cluster');
        cluster.dataset.groupTone = String(n.groupIndex % 4);
        if(n.category)cluster.dataset.groupCategory=n.category;
        cluster.setAttribute('role', 'group'); cluster.setAttribute('aria-label', n.label + ' · ' + n.members.length + '작품 · ' + n.members.map(id => nodes.find(item => item.id === id).label).join(n.sequence?' → ':' · '));
        if (n.members.length === 2) cluster.classList.add('ds-relation-cluster-pair');
        const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
        svg.classList.add('ds-relation-cluster-shape'); svg.setAttribute('aria-hidden','true');
        const outline=document.createElementNS('http://www.w3.org/2000/svg','path');
        svg.append(outline); cluster.append(svg); n.outlineElement=outline;
        const caption = node('div', 'ds-relation-cluster-caption');
        caption.append(node('span', '', n.label), node('small', '', n.members.length + '작품'));
        cluster.append(caption); n.element = cluster; layer.prepend(cluster); continue;
      }
      const button = node('button', 'ds-relation-node'); button.type = 'button';
      button.dataset.graphNode = n.id;
      button.dataset.current = String(Boolean(n.current));
      button.dataset.relationKind = n.relationType || 'current';
      if(Number.isInteger(n.recommendationTone))button.dataset.groupTone=String(n.recommendationTone);
      if(n.relationType==='adaptation'||n.groupCategory==='adaptation')button.style.setProperty('--graph-tone','var(--graph-tone-3)');
      else if (n.groupId && n.groupCategory!=='focused-family') button.style.setProperty('--graph-tone', `var(--graph-tone-${(n.groupIndex+(n.step||1)-1)%4})`);
      button.setAttribute('aria-label', n.label + ' · ' + n.kind + (n.current ? '' : ' · 상세 보기'));
      const art = node('span', 'ds-relation-node-art');
      n.art = art;
      if (n.step) art.dataset.step = String(n.step).padStart(2, '0');
      const cover = safeUrl(n.cover, true);
      if (cover) {
        const img = document.createElement('img'); img.src = cover; img.alt = ''; img.draggable = false;
        img.addEventListener('error', () => art.replaceChildren(node('span', '', '표지 없음')), { once: true }); art.append(img);
      } else art.append(node('span', '', '표지 없음'));
      n.titleElement=node('span','ds-relation-node-title',n.current?'':n.shortLabel||n.label);
      if(n.current)n.titleElement.style.display='none';
      n.kindElement=node('span','ds-relation-node-kind',n.kind);
      const extraRelations=n.extraRelations;
      if(extraRelations.length && !n.current) {
        n.kindElement.replaceChildren(node('span','',n.kind),node('span','ds-relation-node-relations',extraRelations.join(' · ')));
        button.setAttribute('aria-label',button.getAttribute('aria-label')+' · '+extraRelations.join(' · '));
      }
      button.append(art,n.titleElement,n.kindElement);
      n.element = button; layer.append(button);
      button.addEventListener('dragstart', event => event.preventDefault());
      button.addEventListener('pointerenter', event => { if (event.pointerType !== 'touch') { highlighted = n.id; showPreview(n); } });
      button.addEventListener('pointerleave', () => { if (!drag) highlighted = null; hidePreview(); });
      button.addEventListener('focus', () => { highlighted = n.id; showPreview(n); });
      button.addEventListener('blur', () => { if (!drag) highlighted = null; hidePreview(); });
      button.addEventListener('keydown', event => { if (event.key === 'Escape') hidePreview(); });
      button.addEventListener('pointerdown', event => {
        if (!graph || event.button !== 0) return;
        hidePreview();
        n.x+=n.floatX||0;n.y+=n.floatY||0;n.floatX=n.floatY=n.floatStrength=0;
        const rect=stage.getBoundingClientRect(),point=graph.screen2GraphCoords((event.clientX-rect.left)/displayScale,(event.clientY-rect.top)/displayScale);
        releasedSpring=null;
        settleAnchor=null;
        suppressClick = false; drag = { n, id: event.pointerId, x: event.clientX, y: event.clientY, moved: false,
          targetX:n.x,targetY:n.y,requestedY:n.y,offsetX:n.x-point.x,offsetY:n.y-point.y,vx:0,vy:0 };
        n.fx = n.x; n.fy = n.y; button.setPointerCapture(event.pointerId);
      });
      button.addEventListener('pointermove', event => {
        if (!drag || drag.id !== event.pointerId || drag.n !== n) return;
        if (Math.hypot(event.clientX - drag.x, event.clientY - drag.y) > 5) drag.moved = true;
        if (!drag.moved) return;
        event.preventDefault(); button.classList.add('is-dragging');
        const rect = stage.getBoundingClientRect();
        const point = graph.screen2GraphCoords((event.clientX - rect.left)/displayScale,(event.clientY - rect.top)/displayScale);
        drag.requestedY=point.y+drag.offsetY;
        updateCaptions(true);
        const target={...n,x:point.x+drag.offsetX,y:point.y+drag.offsetY};
        relationGraphFitDragNode(target,{width,height});
        if(relationGraphSetDragTarget(drag,target)) {
          wakeSoftBody();
          graph.d3ReheatSimulation();
        }
      });
      for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) button.addEventListener(name, endDrag);
      button.addEventListener('click', event => {
        if (suppressClick && event.detail !== 0) { suppressClick = false; return; }
        if (n.current || !allowNavigation()) return;
        if (typeof window.openBookDetail === 'function') window.openBookDetail(null, n.book.series_name, n.book.library_id, n.book.book_id);
        else notify('상세페이지 연결을 사용할 수 없습니다.', true);
      });
    }
    fit(); readTheme();
    resize = new ResizeObserver(fit); resize.observe(stage);
    try {
      const ForceGraph = await loadRelationGraphEngine();
      if (dead || !root.isConnected) return;
      const cableLength=l=>{
        if(!l.kind)return -1;
        const a=nodes.find(n=>n.id===l.source),b=nodes.find(n=>n.id===l.target);
        return Math.hypot(b.x-a.x,b.y-a.y);
      };
      // Reserve short local connections before routing longer cables around them.
      const drawingLinks=[...links].sort((a,b)=>cableLength(a)-cableLength(b));
      graph = new ForceGraph(canvas).width(width).height(height).backgroundColor('transparent')
        .minZoom(1).maxZoom(1)
        .graphData({ nodes, links:drawingLinks }).nodeCanvasObject(() => {}).enablePointerInteraction(false)
        .enableNodeDrag(false).enableZoomInteraction(false).enablePanInteraction(false)
        .linkCanvasObjectMode(() => 'replace').linkCanvasObject((l, c) => {
          if (typeof l.source !== 'object') return;
          const a = shown(l.source), b = shown(l.target), dx = b.x - a.x, dy = b.y - a.y;
          const group = l.groupId ? nodes.find(n => n.id === l.groupId) : null;
          if(l.kind && !relationRoutes.has(l.pairKey))relationRoutes.set(l.pairKey,{});
          const routing = b.group ? {...l,state:l,idle:!drag && settleFrames===0,
              neighbours:nodes.filter(n=>(adjacency[a.id]||[]).includes(n.id)).map(shown),
              obstacles:nodes.filter(n=>!n.group && n.id!==a.id && n.groupId!==b.id).map(shown)}
            : l.kind ? {...l,state:relationRoutes.get(l.pairKey),frame:edgeFrame,idle:!drag && settleFrames===0,limits:{width,height},
              adjacency,neighbours:nodes.filter(n=>!n.group).map(shown),
              spokes:nodes.filter(n=>!n.group && !n.groupId && !n.current && n.id!==a.id && n.id!==b.id).map(n=>({a:shown(nodes[0]),b:shown(n)})),
              obstacles:nodes.filter(n=>!n.group && n.id!==a.id && n.id!==b.id).map(shown)} : l.spoke ? {
                ...l,state:l,limits:{width,height},idle:!drag && settleFrames===0,adjacency,neighbours:nodes.filter(n=>!n.group).map(shown),
                obstacles:nodes.filter(n=>!n.group && n.id!==a.id && n.id!==b.id).map(shown)
              } : l;
          const cableKey=l.pairKey||[a.id,b.id].sort().join(':');
          routing.cables=[...renderedCables].filter(([key])=>key!==cableKey).map(([,cable])=>({
            samples:cable.samples,shared:[a,b].filter(n=>cable.ids.includes(n.id)),anchors:cable.ids.map((id,i)=>({id,point:cable.samples[i?32:0]}))
          }));
          const curve=relationGraphCurve(a,b,group,routing);
          if(!b.group && !(l.pairIndex>0))renderedCables.set(cableKey,{ids:[a.id,b.id],samples:Array.from({length:33},(_,i)=>curve.point(i/32))});
          const { sx, sy, ex, ey, cx, cy, c1, c2, ra, rb, len, point, points } = curve;
          const label = linkLabels[links.indexOf(l)];
          if (label) label.hidden = len <= ra + rb;
          if (len <= ra + rb) return;
          const active = nodes.find(n => n.id === highlighted);
          const hot = highlighted === a.id || highlighted === b.id
            || (active?.groupId && (active.groupId === l.groupId || active.groupId === b.id));
          const relationColor = b.inkColor || accentColor;
          c.save(); c.beginPath(); c.moveTo(sx, sy); c.lineJoin = 'round'; c.lineCap = 'round';
          if (points) for (const p of points.slice(1)) c.lineTo(p.x, p.y);
          else if(c1)c.bezierCurveTo(c1.x,c1.y,c2.x,c2.y,ex,ey);
          else c.quadraticCurveTo(cx, cy, ex, ey);
          c.strokeStyle = relationColor; c.globalAlpha = l.pairIndex>0?0:hot ? .18 : .07; c.lineWidth = 6; c.stroke();
          const gradient = c.createLinearGradient(sx, sy, ex, ey);
          gradient.addColorStop(0, a.inkColor || accentColor); gradient.addColorStop(1, relationColor);
          c.strokeStyle = gradient; c.globalAlpha = l.pairIndex>0?0:hot ? .98 : .68; c.lineWidth = hot ? 2.1 : 1.35; c.stroke();
          if (label) {
            if(!l.labelRoles && label.textContent!==l.label)label.textContent=l.label;
            label.style.whiteSpace=l.labelRoles?'normal':'nowrap';label.style.borderColor=relationColor;
            const description=l.labelRoles?l.labelRoles.map(item=>item.role+': '+item.title).join(' / '):a.label+' → '+b.label+' · '+l.label;
            if(label.getAttribute('aria-label')!==description){label.removeAttribute('aria-hidden');label.setAttribute('aria-label',description);}
            const labelWidth=label.offsetWidth,labelHeight=label.offsetHeight;
            const preferredDistance=relationGraphEndMark(curve,0).total*.5;
            const mark=relationGraphEndMark(curve,preferredDistance);
            const preferred=(l.labelRoles?0:l.pairSize>1?14:0)+(l.targetSlot||0)*22;
            const clearance=Math.abs(mark.tx)*labelHeight/2+Math.abs(mark.ty)*labelWidth/2+8;
            const offsets=[preferred,-preferred,clearance,-clearance,clearance+12,-clearance-12,
              ...(l.labelRoles?[clearance+28,-clearance-28,clearance+44,-clearance-44]:[])];
            const obstacles=nodes.map(shown).flatMap(n=>n.group?[{
              left:n.x-80,right:n.x+80,top:n.y-n.groupHeight/2+8,bottom:n.y-n.groupHeight/2+58
            }]:[{left:n.x-(n.current?44:38),right:n.x+(n.current?44:38),top:n.y-(n.current?44:38),bottom:n.y+(n.current?44:38)},
              {left:n.x+(n.captionOffset||0)-n.labelWidth/2,right:n.x+(n.captionOffset||0)+n.labelWidth/2,top:n.y+(n.captionAbove?n.captionTop:32),bottom:n.captionAbove?n.y-38:n.y-(n.current?30:24)+n.boxHeight+4}]);
            const arrow=relationGraphEndMark(curve,0);
            obstacles.push({left:arrow.x-6,right:arrow.x+6,top:arrow.y-6,bottom:arrow.y+6});
            let position={x:mark.x,y:mark.y},chosenBox,best=Infinity,side=preferred;
            for(const distance of [preferredDistance,preferredDistance-22,preferredDistance+22,preferredDistance-44,preferredDistance+44,
              ...(l.labelRoles?[preferredDistance*.5,preferredDistance*1.3]:[])])for(const offset of offsets) {
              const anchor=relationGraphEndMark(curve,distance);
              const p={x:anchor.x-anchor.ty*offset,y:anchor.y+anchor.tx*offset};
              const box={left:p.x-labelWidth/2,right:p.x+labelWidth/2,top:p.y-labelHeight/2,bottom:p.y+labelHeight/2};
              const conflicts=[...obstacles,...labelBounds].reduce((sum,o)=>sum+Math.max(0,Math.min(box.right,o.right+3)-Math.max(box.left,o.left-3))*Math.max(0,Math.min(box.bottom,o.bottom+3)-Math.max(box.top,o.top-3)),0);
              const outside=box.left < -width/2+3 || box.right > width/2-3 || box.top < -height/2+3 || box.bottom > height/2-3;
              const beyondGroup=group && (box.left<group.x-group.groupWidth/2+4 || box.right>group.x+group.groupWidth/2-4 || box.top<group.y-group.groupHeight/2+4 || box.bottom>group.y+group.groupHeight/2-4);
              const score=conflicts*10+Math.abs(offset-preferred)+Math.abs(distance-preferredDistance)*.3+(outside?100000:0)+(beyondGroup?10000:0);
              if(score<best){best=score;position=p;chosenBox=box;side=offset;}
            }
            l.labelSide=side;if(chosenBox)labelBounds.push(chosenBox);
            const screen=graph.graph2ScreenCoords(position.x,position.y);
            label.style.transform=`translate(${screen.x}px, ${screen.y}px) translate(-50%, -50%)`;
          }
          if (l.kind) {
            const arrow = relationGraphEndMark(curve,0),angle=Math.atan2(arrow.ty,arrow.tx);
            c.save(); c.translate(arrow.x, arrow.y); c.rotate(angle); c.beginPath(); c.moveTo(-5, -3); c.lineTo(0, 0); c.lineTo(-5, 3);
            c.strokeStyle = relationColor; c.globalAlpha = 1; c.lineWidth = 1.8; c.stroke(); c.restore();
          }
          if (!reducedMotion && !(l.pairIndex>0)) {
            const t = (performance.now() * .000045 + (Number(b.id) || 0) * .173) % 1;
            const tail = Math.max(0, t - .18), p = point(t), q = point(tail);
            const glow = c.createLinearGradient(q.x, q.y, p.x + .01, p.y + .01);
            glow.addColorStop(0, 'transparent'); glow.addColorStop(1, relationColor);
            c.beginPath(); c.moveTo(q.x, q.y);
            for (let i = 1; i <= 8; i++) { const v = point(tail + (t-tail)*i/8); c.lineTo(v.x, v.y); }
            const pulse=Math.sin(t*Math.PI);
            c.strokeStyle=glow;c.globalAlpha=pulse*.28;c.lineWidth=10;c.shadowColor=relationColor;c.shadowBlur=12;c.stroke();
            c.globalAlpha=pulse*(hot?1:.9);c.lineWidth=3.6;c.stroke();
            c.beginPath();c.arc(p.x,p.y,hot?4.5:3.6,0,Math.PI*2);c.fillStyle=relationColor;c.fill();
            c.shadowBlur=0;c.beginPath();c.arc(p.x,p.y,1.5,0,Math.PI*2);c.fillStyle='#fff';c.globalAlpha=pulse*.95;c.fill();
          }
          c.restore();
        })
        .d3VelocityDecay(1).d3AlphaDecay(1)
        .cooldownTicks(reducedMotion ? 1 : Infinity).cooldownTime(Infinity)
        .onRenderFramePre(() => {
          labelBounds = [];
          renderedCables.clear();
          edgeFrame++;
          const spring=drag?.moved?drag:releasedSpring;
          if(spring && relationGraphDragSpring(spring.n,spring,reducedMotion,{width,height}) && spring===releasedSpring)releasedSpring=null;
          if(drag?.moved)updateCaptions(true);
          if(drag?.moved || settleFrames>0) {
            const heldBefore=spring?{x:spring.n.x,y:spring.n.y}:null;
            const movement=relationGraphSoftStep(nodes.filter(n=>!n.group),softLinks,drag?.n||releasedSpring?.n||settleAnchor,{width,height});
            if(spring)relationGraphReconcileDrag(spring,heldBefore);
            quietFrames=movement<.025?quietFrames+1:0;
            if(!drag){settleFrames--;if((quietFrames>=8 || performance.now()>=settleUntil) && !releasedSpring)settleFrames=0;if(!settleFrames)settleAnchor=null;}
          }
          stage.dataset.settling=String(Boolean(drag?.moved || settleFrames>0));
          const time=performance.now()/1000-motionEpoch;
          for(const n of nodes.filter(n=>!n.group)) {
            if(reducedMotion || !relationGraphCanDrift(n,Boolean(graphData.recommendations)) || drag?.n===n){n.floatX=n.floatY=0;continue;}
            n.floatStrength+=((drag || settleFrames>0?0:1)-n.floatStrength)*.025;
            const drift=relationGraphDrift(n.id,time);
            const amplitude=graphData.recommendations ? 0.55 : 1;
            n.floatX=drift.x*n.floatStrength*amplitude;n.floatY=drift.y*n.floatStrength*amplitude;
          }
        })
        .onRenderFramePost(() => {
          if (!root.isConnected) { controller.destroy(); return; }
          if (performance.now() - lastTheme > 1000) { readTheme(); lastTheme = performance.now(); }
          paintNodes();
          if(profileEnabled) {
            const now=performance.now(),mode=drag?.moved?'drag':'idle';
            if(profile.last && profile.mode===mode && profile[mode].length<3000)profile[mode].push(now-profile.last);
            profile.last=now;profile.mode=mode;
            if(now-profile.published>1000){profile.published=now;stage.dataset.frameProfile=JSON.stringify({idle:relationGraphFrameStats(profile.idle),drag:relationGraphFrameStats(profile.drag)});}
          }
        });
      for (const force of ['center','charge','link','anchor']) graph.d3Force(force,null);
      graph.centerAt(0, 0).zoom(1); paintNodes();
      visibility = new IntersectionObserver(entries => {
        if (dead) return;
        stageVisible = entries.some(entry => entry.isIntersecting); updateAnimation();
      }); visibility.observe(stage);
    } catch (_) {
      if (!dead) { status.textContent = '움직임을 불러오지 못했습니다. 원형 표지를 눌러 작품을 열 수 있습니다.'; status.hidden = false; }
    }
  }

  function renderRelations(relations, graphData = {}) {
    const section = $('[data-relations-section]');
    const target = $('[data-relations]');
    const cards = [];
    const graphWorks = [];
    const seenWorks = new Set();
    for (const [type, label] of Object.entries(relationLabels)) {
      const items = Array.isArray(relations?.[type]) ? relations[type] : [];
      if (!items.length) continue;
      for (const book of items) {
        const key = String(book?.library_id || '') + ':' + String(book?.series_name || book?.display_name || book?.book_id || '').trim().toLocaleLowerCase();
        if (!key || seenWorks.has(key)) continue;
        seenWorks.add(key);
        cards.push(bookCard(book, cards.length, true, label));
        graphWorks.push({ book, label, type });
      }
    }
    target.replaceChildren(...cards);
    section.hidden = cards.length === 0 && !canEdit;
    renderRelationGraph(graphWorks, graphData);
  }

  function renderRecommendations(items) {
    const section = $('[data-recommendation-section]');
    const target = $('[data-recommendations]');
    const books = relationGraphRecommendationSample(items);
    target.replaceChildren(...books.map((book, index) => bookCard(book, index, true)));
    section.hidden = books.length === 0;
    if(!relationGraphInput?.works.length || relationGraphInput.graphData.recommendations)
      renderRelationGraph(books.map(book=>({book,label:'추천',type:'recommendation'})),{recommendations:true});
  }

  function renderLibraryPath() {
    const path = $('[data-library-path]');
    path.replaceChildren();
    const entries = [[libraryTypes[type], 'home'], [libraryName, libraryId]];
    for (const [label, id] of entries) {
      if (!label) continue;
      const item = node('span', 'ds-library-entry');
      const link = node('button', 'ds-library-link', label);
      link.type = 'button';
      link.title = label + ' 홈으로 이동';
      link.disabled = id == null;
      link.addEventListener('click', () => {
        if (typeof window.selectCategory !== 'function') return notify('서재 이동 기능을 연결하지 못했습니다.', true);
        if (allowNavigation()) window.selectCategory(String(id));
      });
      item.append(link);
      path.append(item);
    }
    const genres = split(meta.genre).filter((term) => !excludedGenres.has(term.toLocaleLowerCase()));
    for (const term of genres) {
      const item = node('span', 'ds-library-genre');
      const link = node('button', 'ds-library-link', term);
      link.type = 'button';
      link.setAttribute('aria-label', '장르 ' + term + ' 필터');
      link.addEventListener('click', () => applyFilter('genre', term));
      item.append(link);
      path.append(item);
    }
  }

  function detailTitle() {
    const raw = String(meta.series_alias || meta.series_name || context.seriesName
      || books[0]?.series_name || books[0]?.title || '').trim();
    const withoutAuthor = raw.replace(/(?:\s*\[[^\[\]]*\])+\s*$/u, '').trim();
    // Adult folder names can contain nested or unmatched square brackets.
    // Keep their text while removing the folder markup from the heading.
    const display = type === 'adult'
      ? (withoutAuthor || raw).replace(/[\[\]]/gu, '').replace(/\s+/gu, ' ').trim()
      : withoutAuthor || raw;
    return display || '도서 상세';
  }

  function detailOriginalTitle() {
    const original = String(meta.localized_series || books.find((book) => book.localized_series)?.localized_series || '').trim();
    return type === 'adult' ? original.replace(/[\[\]]/gu, '').replace(/\s+/gu, ' ').trim() : original;
  }

  function renderHeader() {
    renderLibraryPath();
    const displayTitle = detailTitle();
    $('[data-title]').textContent = displayTitle;
    renderMetadataHold();
    const originalTitle = detailOriginalTitle();
    const originalTitleNode = $('[data-original-title]');
    originalTitleNode.hidden = !originalTitle || originalTitle.toLocaleLowerCase() === String(displayTitle).trim().toLocaleLowerCase();
    originalTitleNode.textContent = originalTitleNode.hidden ? '' : originalTitle;
    renderSummary(meta.summary);

    const cover = $('[data-cover]');
    const coverSrc = safeUrl(meta.cover_image || books[0]?.cover_image, true);
    cover.hidden = !coverSrc;
    $('[data-no-cover]').hidden = Boolean(coverSrc);
    if (coverSrc && cover.src !== coverSrc) cover.src = coverSrc;
    cover.alt = ($('[data-title]').textContent || '도서') + ' 표지';
    cover.onerror = () => {
      cover.hidden = true;
      $('[data-no-cover]').hidden = false;
    };

    const grouped = groupedVolumeMode();
    const groups = grouped ? volumeGroups() : [];
    const done = grouped
      ? groups.filter((group) => group.books.length > 0 && group.books.every(completed)).length
      : books.filter(completed).length;
    const inProgress = grouped
      ? groups.filter((group) => group.books.some(reading)).length
      : books.filter(reading).length;
    const readingTotal = grouped ? groups.length : books.length;
    const percent = media
      ? (Number(meta.is_completed) === 1 ? 100 : Math.min(100, Math.round(num(meta.total_progress_pct))))
      : books.length ? Math.round(books.reduce((sum, book) => sum + progress(book), 0) / books.length) : 0;
    $('[data-percent]').textContent = percent + '%';
    $('[data-series-progress]').value = percent;
    $('[data-reading-state]').textContent = done === readingTotal && done
      ? (media ? '모두 재생했어요' : '모두 읽었어요')
      : inProgress || done
        ? (media ? '재생 중' : '읽는 중')
        : (media ? '재생 전' : '읽기 전');
    const readingUnit = chapterOnlyMode() ? '화' : grouped ? '권' : unit;
    $('[data-reading-caption]').textContent = readingTotal + readingUnit + ' 중 ' + done + readingUnit
      + (media ? ' 완료' : ' 완독');

    const next = continueBook();
    $('[data-read-label]').textContent = type === 'audiobook'
      ? '시리즈 듣기'
      : type === 'video'
        ? '시리즈 재생'
        : '시리즈 읽기';
    $('[data-action=read]').disabled = !next;
    $('[data-action=collection]').disabled = !books.length;
    const resetListening = $('[data-action=reset-listening]');
    resetListening.hidden = type !== 'audiobook';
    resetListening.disabled = !books.length;
    const favorite = $('[data-action=favorite]');
    const isFavorite = books.length > 0 && books.every((book) => Number(book.is_favorite) === 1);
    favorite.disabled = !books.length;
    favorite.setAttribute('aria-pressed', String(isFavorite));
    favorite.setAttribute('aria-label', isFavorite ? '즐겨찾기 해제' : '즐겨찾기 추가');
    favorite.querySelector('i').className = (isFavorite ? 'fa-solid' : 'fa-regular') + ' fa-heart';
    const standaloneBook = isStandalone()
      ? books.find((book) => !completed(book) && canListenBook(book)) || books.find(canListenBook)
      : null;
    const listenButtonElement = $('[data-action=listen]');
    const actionRow = $('.ds-action-row');
    const canListenStandalone = canListenBook(standaloneBook);
    listenButtonElement.hidden = !canListenStandalone;
    actionRow.dataset.hasListen = String(canListenStandalone);
    listenButtonElement.onclick = canListenStandalone ? (event) => {
      event.preventDefault();
      event.stopPropagation();
      listenToBook(standaloneBook);
    } : null;

    const latest = books.map((book) => book.last_read_at || '').sort().at(-1);
    const current = $('[data-current]');
    current.replaceChildren(node('small', '', latest ? (media ? '최근 재생 ' : '최근 읽은 날 ') + formatDate(latest) : '다음 이야기'));
    if (next) {
      const link = node('button', 'ds-text-button', fileDisplayLabel(next));
      link.type = 'button';
      link.addEventListener('click', () => read(next, true));
      current.append(link);
    } else {
      current.append(node('span', '', '등록된 도서가 없습니다.'));
    }

    const unlockButton = $('[data-action=unlock-metadata]');
    const metadataLocked = Number(meta.metadata_locked) === 1
      || books.some((book) => Number(book.metadata_locked) === 1);
    unlockButton.hidden = media || !metadataLocked;
    unlockButton.setAttribute('data-series-name', meta.series_name || context.seriesName || '');
    unlockButton.setAttribute('data-library-id', libraryId ?? context.libraryId ?? '');
    unlockButton.setAttribute('data-book-id', books[0]?.id || '');
    $('[data-action=edit]').hidden = !canEdit;
    $('[data-action=manage-relations]').hidden = !canEdit || media;
    if (canEdit && !media) $('[data-relations-section]').hidden = false;
    all('[data-action=metadata-search-toggle]').forEach((button) => { button.hidden = !canEdit; });
    $('[data-edit-caption]').textContent = canEdit
      ? '시리즈 정보를 편집할 수 있습니다.'
      : type === 'video'
        ? '비디오 메타정보는 읽기 전용입니다.'
        : '메타정보 편집은 관리자만 할 수 있습니다.';
    renderInfo();
    renderSeries();
    renderAppearance();
    requestAnimationFrame(fitSummary);
  }

  function renderMetadataHold() {
    const panel = $('[data-metadata-hold]');
    const candidates = Array.isArray(metadataHold?.candidates) ? metadataHold.candidates : [];
    const sourceLabels = { ridi: '리디', naver: '네이버시리즈', kyobo: '교보문고', yes24: '예스24',
      kakaopage: '카카오페이지', kakao_webtoon: '카카오웹툰',
      munpia: '문피아', novelpia: '노벨피아' };
    panel.hidden = !canEdit || metadataHold?.reason !== 'author_conflict';
    const list = $('[data-metadata-hold-candidates]');
    list.replaceChildren(...candidates.map((candidate) => {
      const source = sourceLabels[candidate?.source] || String(candidate?.source || '제공처');
      const title = String(candidate?.title || '').trim();
      let author = String(candidate?.author || '작가 미상').trim();
      if (candidate?.source === 'yes24') {
        author = author.replace(/\s+글\s*(?:[·ㆍ/]\s*)?그림$/u, '');
      }
      return node('li', '', `${source} · ${title} · ${author}`);
    }));
  }

  async function confirmMetadataSameWork(event) {
    const button = event.currentTarget;
    if (!canEdit || button.disabled) return;
    button.disabled = true;
    button.textContent = '적용 중...';
    try {
      const result = await request('/api/media/context-menu/book/plugins/action', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plugin_id: 'rabbit_plugins', action_id: 'metadata_confirm_same_work', type,
          context: { book_id: books[0]?.id || meta.id } }),
      });
      const refreshed = await refreshMetadataFromServer({ force: true });
      if (!refreshed) {
        await loadDetailData(false);
        coverRevision += 1;
        renderMetadataRefresh();
      }
      invalidateSeriesList();
      notify(result.message || '동일 작품의 메타데이터를 적용했습니다.');
    } catch (error) {
      notify(error.message || '후보를 적용하지 못했습니다.', true);
    } finally {
      button.disabled = false;
      button.textContent = '동일 작품';
    }
  }

  function editMode(on) {
    if (saving) return;
    const form = $('[data-edit-form]');
    form.hidden = !on;
    $('[data-edit-panel]').hidden = !on;
    $('[data-action=edit]').hidden = on || !canEdit;
    $('[data-action=unlock-metadata]').hidden = on || media || !(Number(meta.metadata_locked) === 1
      || books.some((book) => Number(book.metadata_locked) === 1));
    if (!on) {
      dirty = false;
      return;
    }
    editFields();
    form.reset();
    $('[data-banner-field]').hidden = media;
    $('[data-banner-remove-field]').hidden = media || !meta.banner_image;
    for (const [key] of activeEditFields()) {
      if (!form.elements[key]) continue;
      const value = key === 'books_lv'
        ? editContentRatingValue()
        : key === 'link'
        ? split(meta[key]).join(', ')
        : key === 'cover_artist' || key === 'translator'
        ? (manualEmptyFields.has(key) ? ''
          : key === 'cover_artist' ? meta.cover_artist || meta.artist || '' : meta.translator || '')
        : key === 'release_date'
        ? (publicationDate() || '').slice(0, 10)
        : key === 'publication_start_date'
          ? meta.publication_dates?.start || ''
          : key === 'publication_end_date'
            ? meta.publication_dates?.end || ''
            : meta[key] && meta[key] !== '-' ? meta[key] : '';
      if (key === 'books_lv') {
        const select = form.elements[key];
        for (const option of Array.from(select.options)) if (option.dataset.currentRating) option.remove();
        if (value && !Array.from(select.options).some((option) => option.value === value)) {
          const option = node('option', '', '현재 값: ' + value);
          option.value = value;
          option.dataset.currentRating = 'true';
          select.append(option);
        }
      }
      form.elements[key].value = value;
    }
    form.elements.summary.value = meta.summary || '';
    const series = meta.series_name || context.seriesName;
    const scope = editScope?.books ?? books.length;
    const libraries = editScope?.libraries;
    $('[data-edit-scope]').textContent = type === 'audiobook'
      ? '같은 제목 또는 폴더명의 오디오북 ' + scope + '개, ' + (libraries ?? 1) + '개 서재에 적용됩니다.'
      : '‘' + series + '’ 시리즈 전체 ' + scope + '권에 적용됩니다.'
        + (Number(libraries) > 1 ? ' 같은 이름의 시리즈가 있는 ' + libraries + '개 서재가 함께 수정됩니다.' : '');
    if (fields[0] && form.elements[fields[0][0]]) form.elements[fields[0][0]].focus();
  }

  function toggleMobileInfo() {
    const panel = $('[data-info-panel]');
    const button = $('[data-action=mobile-info-toggle]');
    if (!panel || !button) return;
    const open = panel.dataset.mobileInfoOpen !== 'true';
    panel.dataset.mobileInfoOpen = String(open);
    button.setAttribute('aria-expanded', String(open));
    button.textContent = open ? '정보 닫기' : '정보';
    panel.setAttribute('aria-hidden', String(!open));
    positionMobileInfoPanel();
    document.body.classList.toggle('ds-mobile-info-open', open);
  }

  function positionMobileInfoPanel() {
    const panel = $('[data-info-panel]');
    if (!panel || panel.dataset.mobileInfoOpen !== 'true' || !window.matchMedia('(max-width: 700px)').matches) return;
    const header = document.querySelector('.library-header');
    const headerRect = header?.getBoundingClientRect?.();
    const fallbackTop = 88;
    const rawTop = headerRect && headerRect.bottom > 0 ? headerRect.bottom + 8 : fallbackTop;
    const top = Math.max(12, Math.min(Math.round(rawTop), Math.max(12, window.innerHeight - 180)));
    panel.style.setProperty('--ds-mobile-info-top', `${top}px`);
  }

  function closeMobileInfo() {
    const panel = $('[data-info-panel]');
    if (!panel || panel.dataset.mobileInfoOpen !== 'true') return;
    toggleMobileInfo();
  }

  async function save(event) {
    event.preventDefault();
    if (!canEdit || saving) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    const editingReleaseDate = activeEditFields().some(([key]) => key === 'release_date');
    const file = data.get('cover_image');
    if (file?.size && (file.size > 10 * 1024 * 1024 || !['image/jpeg', 'image/png', 'image/webp'].includes(file.type))) {
      return notify('표지는 10MB 이하의 JPG, PNG, WebP 파일을 선택해 주세요.', true);
    }
    if (!file?.size) data.delete('cover_image');
    const bannerFile = data.get('banner_image');
    const removeBanner = data.get('remove_banner') === '1';
    if (bannerFile?.size && (bannerFile.size > 10 * 1024 * 1024 || !['image/jpeg', 'image/png', 'image/webp'].includes(bannerFile.type))) {
      return notify('배너는 10MB 이하의 JPG, PNG, WebP 파일을 선택해 주세요.', true);
    }
    if (bannerFile?.size && removeBanner) return notify('배너 교체와 제거 중 하나만 선택해 주세요.', true);
    data.delete('banner_image');
    data.delete('remove_banner');
    const links = split(data.get('link'));
    if (links.some((link) => {
      try { return !['http:', 'https:'].includes(new URL(link).protocol); }
      catch { return true; }
    })) return notify('관련 링크는 http:// 또는 https:// 주소로 입력해 주세요.', true);
    data.set('link', links.join(', '));
    data.set('type', type);
    data.set('series', meta.series_name || context.seriesName);
    saving = true;
    all('[data-edit-form] button, [data-edit-form] input, [data-edit-form] textarea, [data-edit-form] select')
      .forEach((element) => { element.disabled = true; });
    try {
      const bannerData = bannerFile?.size ? await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = () => reject(new Error('배너 파일을 읽지 못했습니다.'));
        reader.readAsDataURL(bannerFile);
      }) : '';
      await request('/api/media/detail/edit', { method: 'POST', body: data });
      if (!root.isConnected) return;
      if (data.has('books_lv')) {
        // The series edit updates every book. Discard the old derived ratings
        // so lowering or clearing a rating is visible before the next fetch.
        for (const item of [meta, ...books]) {
          item.books_lv = String(data.get('books_lv') || '');
          for (const key of ['content_rating_level', 'content_rating_label', 'age_rating_level', 'age_rating_label', 'age_rating']) delete item[key];
        }
      }
      let detailFieldsError = '';
      let detailFieldsWarning = '';
      let bannerError = '';
      let coverArtistSaved = true;
      let translatorSaved = true;
      if (type === 'general' || type === 'adult') {
        try {
          const result = await request('/api/media/context-menu/book/plugins/action', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              plugin_id: 'rabbit_plugins',
              action_id: 'save_detail_metadata',
              type,
              context: {
                series_name: meta.series_name || context.seriesName,
                cover_artist: String(data.get('cover_artist') || '').trim(),
                manual_fields: Object.fromEntries(
                  [...activeEditFields().map(([key]) => key), 'summary']
                    .map((key) => [key, String(data.get(key) || '').trim()])),
                ...(editingReleaseDate
                  ? { release_date: String(data.get('release_date') || '').trim() }
                  : { publication_start_date: String(data.get('publication_start_date') || '').trim(),
                      publication_end_date: String(data.get('publication_end_date') || '').trim() }),
                manual_chapter_count: String(data.get('manual_chapter_count') || '').trim(),
              },
            }),
          });
          coverArtistSaved = result.cover_artist_saved !== false;
          translatorSaved = result.translator_saved !== false;
          manualEmptyFields = new Set(result.manual_empty_fields || []);
          detailFieldsWarning = (result.warnings || []).join(' ');
        } catch (error) {
          detailFieldsError = error.message;
        }
      }
      for (const [key] of activeEditFields()) {
        if (key === 'release_date' || key === 'publication_start_date' || key === 'publication_end_date' || key === 'manual_chapter_count') continue;
        if ((key !== 'cover_artist' || (!detailFieldsError && coverArtistSaved))
            && (key !== 'translator' || (!detailFieldsError && translatorSaved))) {
          meta[key] = String(data.get(key) || '');
        }
      }
      if (!detailFieldsError && (type === 'general' || type === 'adult')) {
        if (editingReleaseDate) {
          meta.release_date = String(data.get('release_date') || '').trim();
          books.forEach((book) => { book.release_date = meta.release_date; });
        }
        meta.manual_chapter_count = String(data.get('manual_chapter_count') || '').trim();
        meta.publication_dates = {
          start: data.has('publication_start_date')
            ? String(data.get('publication_start_date') || '') : meta.publication_dates?.start || '',
          end: data.has('publication_end_date')
            ? String(data.get('publication_end_date') || '') : meta.publication_dates?.end || '',
        };
      }
      if ((bannerData || removeBanner) && (type === 'general' || type === 'adult')) {
        try {
          const bannerResult = await request('/api/media/context-menu/book/plugins/action', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              plugin_id: 'rabbit_plugins', action_id: 'save_series_banner', type,
              context: { series_name: meta.series_name || context.seriesName,
                library_id: libraryId, banner_data: bannerData,
                remove_banner: removeBanner },
            }),
          });
          meta.banner_image = bannerResult.banner_image;
        } catch (error) {
          bannerError = error.message;
        }
      }
      meta.summary = String(data.get('summary') || '');
      meta.metadata_locked = type === 'audiobook' ? 0 : 1;
      dirty = false;

      let refreshed = true;
      try {
        const params = new URLSearchParams({
          type,
          series: context.seriesName,
          library_id: context.libraryId || 'all',
          representative_book_id: media ? meta.id : books[0]?.id || '',
        });
        const result = await request('/api/media/detail?' + params);
        if (result.meta) Object.assign(meta, result.meta);
      } catch {
        refreshed = false;
      }
      if (manualEmptyFields.has('cover_artist')) meta.artist = '';
      if (!detailFieldsError && coverArtistSaved && meta.cover_artist && meta.cover_artist !== '-') meta.artist = meta.cover_artist;
      coverRevision += 1;
      invalidateSeriesList();
      saving = false;
      editMode(false);
      renderHeader();
      loadDiscovery({ refresh: true });
      notify(detailFieldsError
        ? '나머지 메타정보는 저장했지만 그림작가와 연재일을 저장하지 못했습니다. ' + detailFieldsError
        : bannerError ? '나머지 메타정보는 저장했지만 배너를 저장하지 못했습니다. ' + bannerError
        : detailFieldsWarning ? '일부 상세정보는 저장했지만 그림작가 변경은 저장하지 못했습니다. ' + detailFieldsWarning
        : refreshed ? '시리즈 메타정보를 저장했습니다.' : '저장은 완료했습니다. 최신 표지는 페이지를 다시 열면 확인할 수 있습니다.',
      Boolean(detailFieldsError || detailFieldsWarning || bannerError));
    } catch (error) {
      if (root.isConnected) notify('저장하지 못했습니다. 입력 내용은 유지됩니다. ' + error.message, true);
    } finally {
      saving = false;
      all('[data-edit-form] button, [data-edit-form] input, [data-edit-form] textarea, [data-edit-form] select')
        .forEach((element) => { element.disabled = false; });
    }
  }

  async function loadDetailData(render = true, preparedData = null) {
    const sequence = ++detailDataSequence;
    if (!books.length) {
      detailDataReady = true;
      return true;
    }
    try {
      const data = preparedData || await request(apiUrl('files'));
      if (!root.isConnected || sequence !== detailDataSequence) return;
      summaryHtmlEnabled = data.support_summary_html === true;
      const graphSettings={enabled:data.relation_graph_enabled!==false,mobileEnabled:data.relation_graph_mobile_enabled!==false,recommendationsEnabled:data.recommendation_graph_enabled!==false,motionEnabled:data.relation_graph_motion_enabled!==false};
      const graphSettingsChanged=!relationGraphSettings || graphSettings.enabled!==relationGraphSettings.enabled || graphSettings.mobileEnabled!==relationGraphSettings.mobileEnabled || graphSettings.recommendationsEnabled!==relationGraphSettings.recommendationsEnabled || graphSettings.motionEnabled!==relationGraphSettings.motionEnabled;
      relationGraphSettings=graphSettings;
      if(graphSettingsChanged)refreshRelationGraphDisplay();
      excludedGenres = excludedTerms(data.exclude_genres);
      excludedTags = excludedTerms(data.exclude_tags);
      contentKind = data.content_kind || contentKind;
      metadataSources = Array.isArray(data.metadata_sources) ? data.metadata_sources : metadataSources;
      metadataAutoEnabled = data.metadata_auto_enabled === true;
      metadataHold = data.metadata_hold || {};
      metadataFields = Array.isArray(data.metadata_fields) ? data.metadata_fields : metadataFields;
      manualEmptyFields = new Set(Array.isArray(data.manual_empty_fields) ? data.manual_empty_fields : []);
      // A later manual/provider match may fill a previously cleared field.
      // Stored values then take precedence over the old empty-field marker.
      for (const key of ['summary', 'genre', 'tags', 'cover_artist', 'translator', 'isbn']) {
        const value = String(meta[key] || (key === 'translator' ? data.comicinfo?.translator : '') || '').trim();
        if (value && value !== '-' && value !== '등록된 설명이 없습니다.') manualEmptyFields.delete(key);
      }
      if ((data.files || []).some((file) => file.release_date)) manualEmptyFields.delete('release_date');
      canDownload = data.can_download !== false;
      const currentBooks = new Map(books.map((book) => [Number(book.id), book]));
      for (const file of data.files || []) {
        const book = currentBooks.get(Number(file.id));
        if (!book) continue;
        if (file.file_path) book.file_path = file.file_path;
        if (file.file_format) book.file_format = file.file_format;
        for (const key of ['metadata_title', 'title_alias', 'link']) {
          if (Object.prototype.hasOwnProperty.call(file, key)) book[key] = file[key] || '';
        }
        book.release_date = file.release_date || '';
        book.pages_read = file.pages_read;
        book.is_completed = file.is_completed;
        book.last_read_at = file.last_read_at;
        if (file.volume != null) book.volume = file.volume;
        if (file.count != null) book.count = file.count;
        if (file.number != null) book.number = file.number;
      }
      if (contentKind === 'book') meta.release_date = (data.files || []).find((file) => file.release_date)?.release_date || '';
      // The core detail response is the authoritative post-scan database
      // state. kavita.yaml and ComicInfo.xml have already been imported into
      // those rows by the scanner, so combining every file again can briefly
      // resurrect values that an explicit metadata overwrite replaced.
      // File rows are therefore only a fallback when the series value is
      // genuinely empty.
      const storedGenres = split(meta.genre);
      const storedTags = split(meta.tags);
      const combinedGenres = (storedGenres.length || manualEmptyFields.has('genre')
        ? storedGenres
        : (data.files || []).flatMap((file) => split(file.genre)));
      const combinedTags = (storedTags.length || manualEmptyFields.has('tags')
        ? storedTags
        : (data.files || []).flatMap((file) => split(file.tags)));
      const cleanTerms = distinctGenreTags(combinedGenres.join(', '), combinedTags.join(', '));
      meta.genre = cleanTerms.genre;
      meta.tags = cleanTerms.tags;
      applyContentRating(data.files || []);
      const comicinfo = data.comicinfo || {};
      meta.uci = meta.uci || comicinfo.uci || comicinfo.UCI || '';
      meta.document_publication_date = manualEmptyFields.has('release_date') ? '' : comicinfo.date || '';
      meta.series_name = meta.series_name || data.series_name || books[0]?.series_name || '';
      meta.localized_series = meta.localized_series || data.localized_series || comicinfo.localized_series
        || books.find((book) => book.localized_series)?.localized_series || '';
      if (type === 'adult') {
        for (const key of ['teams', 'locations', 'characters']) {
          meta[key] = meta[key] || data.adult_detail?.[key] || '';
        }
      }
      if (summaryHtmlEnabled && !manualEmptyFields.has('summary')
          && !hasStoredSummary(meta.summary) && String(comicinfo.summary || '').trim()) {
        meta.summary = comicinfo.summary;
      }
      meta.artist = manualEmptyFields.has('cover_artist') ? '' : meta.cover_artist && meta.cover_artist !== '-'
        ? meta.cover_artist
        : comicinfo.artist || '';
      meta.translator = manualEmptyFields.has('translator') ? ''
        : comicinfo.translator || meta.translator || '';
      meta.comicinfo_format = comicinfo.format || '';
      meta.comicinfo_count = comicinfo.count;
      meta.comicinfo_volume = comicinfo.volume;
      meta.comicinfo_number = comicinfo.number;
      meta.is_standalone = data.is_standalone === true;
      meta.publication_final_volume_found = comicinfo.final_volume_found === true;
      meta.publication_available_volume_count = Number(comicinfo.available_volume_count) || 0;
      meta.publication_volume_coverage = data.publication_volume_coverage || {};
      meta.holdings_coverage = data.holdings_coverage || null;
      const storedPublicationStatus = String(meta.publication_status ?? '').trim();
      meta.publication_status_label = ['0', '1', '2'].includes(storedPublicationStatus)
        ? (meta.publication_status_label || '')
        : (comicinfo.publication_status_label || meta.publication_status_label || '');
      meta.publication_dates = { ...(meta.publication_dates || {}), ...(data.publication_dates || {}) };
      meta.publication_coverage = data.publication_coverage || {};
      meta.manual_chapter_count = data.manual_chapter_count || '';
      canEdit = type !== 'video' && data.can_edit === true;
      editScope = data.edit_scope;
      libraryName = data.library_name || '';
      libraryId = data.library_id ?? context.libraryId ?? null;
      detailDataReady = true;
      detailDataFailed = false;
      if (render) renderHeader();
      return true;
    } catch (error) {
      detailDataFailed = true;
      if (root.isConnected && !detailDataReady) renderInfo();
      if (root.isConnected) notify('추가 상세 정보를 확인하지 못했습니다. ' + error.message, true);
      return false;
    }
  }

  function renderMetadataRefresh() {
    if (!root.isConnected) return;
    renderHeader();
  }

  async function loadDiscovery({ refresh = false } = {}) {
    const requestKey = discoveryInputKey();
    if (discoveryLoaded && (!refresh || discoveryLoadedKey === requestKey)) return;
    if (loadingDiscovery) {
      if (refresh) discoveryRefreshQueued = true;
      return;
    }
    const loading = $('[data-discovery-loading]');
    const hasExistingResults = discoveryLoaded || discoveryAttempted;
    if (!books.length) {
      renderRelations({});
      renderRecommendations([]);
      discoveryLoaded = true;
      return;
    }
    discoveryAttempted = true;
    loadingDiscovery = true;
    loading.setAttribute('aria-busy', 'true');
    if (!hasExistingResults) {
      loading.removeAttribute('data-state');
      // 관련/추천 결과가 준비되기 전에는 빈 공간만 유지한다. 매우 짧게 나타났다
      // 사라지는 로딩 문구가 상세 진입과 뷰어 복귀 때 화면을 깜빡이게 만들었다.
      loading.hidden = true;
      loading.textContent = '';
      renderRelations({});
      renderRecommendations([]);
    }
    try {
      const data = await request(apiUrl('discovery'));
      if (!root.isConnected) return;
      // 메타데이터가 응답 중간에 바뀌었다면 이전 기준의 결과를 화면에
      // 덮어쓰지 않고 최신 기준으로 한 번만 다시 요청합니다.
      if (requestKey !== discoveryInputKey()) {
        discoveryRefreshQueued = true;
        return;
      }
      renderDiscoveryPayload(data);
      discoveryLoaded = true;
      discoveryLoadedKey = requestKey;
    } catch (error) {
      if (root.isConnected && !hasExistingResults) {
        loading.textContent = '관련작품과 추천항목을 불러오지 못했습니다. ' + error.message;
        loading.dataset.state = 'error';
        loading.hidden = false;
      }
    } finally {
      loadingDiscovery = false;
      loading.removeAttribute('aria-busy');
      if (loading.dataset.state !== 'error' && (!hasExistingResults || discoveryLoaded)) {
        loading.hidden = true;
        delete loading.dataset.state;
      }
      if (discoveryRefreshQueued && root.isConnected) {
        discoveryRefreshQueued = false;
        queueMicrotask(() => loadDiscovery({ refresh: true }));
      }
    }
  }

  function collectionPayload() {
    if (media) {
      return type === 'audiobook'
        ? { audiobook_id: Number(meta.id) }
        : { video_id: Number(meta.id) };
    }
    return { series_name: meta.series_name || context.seriesName };
  }

  function closeCollectionMenu() {
    const menu = $('[data-collection-menu]');
    menu.hidden = true;
    $('[data-action=collection]').setAttribute('aria-expanded', 'false');
  }

  async function addToCollection(collectionId) {
    const menu = $('[data-collection-menu]');
    menu.querySelectorAll('button').forEach((button) => { button.disabled = true; });
    try {
      const params = new URLSearchParams({ db_type: type });
      await request('/api/v1/collections/' + encodeURIComponent(collectionId) + '/items?' + params, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(collectionPayload()),
      });
      menu.dataset.loaded = 'false';
      closeCollectionMenu();
      if (root.isConnected) notify('컬렉션에 추가했습니다.');
    } catch (error) {
      menu.querySelectorAll('button').forEach((button) => { button.disabled = false; });
      if (root.isConnected) notify('컬렉션에 추가하지 못했습니다. ' + error.message, true);
    }
  }

  async function openCreateCollection() {
    closeCollectionMenu();
    try {
      const { openCreateCollectionModal } = await import('/static/js/tab_collections.js');
      if (!root.isConnected) return;
      openCreateCollectionModal((collectionId) => addToCollection(collectionId));
    } catch {
      notify('새 컬렉션 창을 열지 못했습니다.', true);
    }
  }

  async function loadCollections() {
    const menu = $('[data-collection-menu]');
    if (menu.dataset.loaded === 'true' || loadingCollections) return;
    loadingCollections = true;
    menu.replaceChildren(node('div', 'ds-collection-empty', '컬렉션을 불러오는 중…'));
    try {
      const params = new URLSearchParams({ db_type: type });
      const data = await request('/api/v1/collections?' + params);
      if (!root.isConnected || menu.hidden) return;
      menu.replaceChildren();
      if (!data.collections?.length) {
        menu.append(node('div', 'ds-collection-empty', '아직 컬렉션이 없습니다.'));
      } else {
        for (const collection of data.collections) {
          const button = node('button', 'ds-collection-option');
          button.type = 'button';
          button.setAttribute('role', 'menuitem');
          const dot = icon('circle');
          dot.style.color = collection.color || '#7c3aed';
          const name = node('span', '', collection.name || '이름 없는 컬렉션');
          const count = node('small', '', (Number(collection.item_count) || 0) + '개');
          button.append(dot, name, count);
          button.addEventListener('click', () => addToCollection(collection.id));
          menu.append(button);
        }
      }
      const create = node('button', 'ds-collection-create', '+ 새 컬렉션');
      create.type = 'button';
      create.setAttribute('role', 'menuitem');
      create.addEventListener('click', openCreateCollection);
      menu.append(create);
      menu.dataset.loaded = 'true';
    } catch (error) {
      if (root.isConnected) menu.replaceChildren(node('div', 'ds-collection-empty', error.message));
    } finally {
      loadingCollections = false;
    }
  }

  function activeEditFields() {
    const editableFields = media ? fields.filter(([key]) => key !== 'translator') : fields;
    const base = contentKind !== 'book' && !isStandalone() ? editableFields : editableFields.flatMap(([key, label]) => key === 'publication_start_date'
      ? [['release_date', '출간일']]
      : key === 'publication_end_date' ? [] : [[key, label]]);
    return type === 'adult' ? base.flatMap((field) => field[0] === 'cover_artist'
      ? [field, ['teams', '그룹'], ['locations', '작품'], ['characters', '캐릭터']]
      : [field]) : base;
  }

  function editFields() {
    const target = $('[data-edit-fields]');
    target.replaceChildren();
    for (const [key, label] of activeEditFields()) {
      const field = node('label', 'ds-field', label);
      const input = document.createElement(key === 'books_lv' ? 'select' : 'input');
      input.name = key;
      if (key === 'books_lv') {
        for (const [value, text] of [['', '미지정'], ['everyone', '전체 이용가'], ['ma15+', '15세 이용가'], ['m', '18세 이용가'], ['r18', '성인망가 (R18)'], ['adult only 18+', '포르노 (Adult Only 18+)']]) {
          const option = node('option', '', text);
          option.value = value;
          input.append(option);
        }
        field.append(input);
        target.append(field);
        continue;
      }
      input.type = key === 'manual_chapter_count' ? 'number' : key === 'release_date' || key === 'publication_start_date' || key === 'publication_end_date' ? 'date' : 'text';
      if (key === 'manual_chapter_count') { input.min = '1'; input.max = '1000000'; input.step = '1'; input.placeholder = '비워 두면 표시하지 않음'; }
      input.maxLength = key === 'link' ? 2000 : ['cover_artist', 'translator'].includes(key) ? 500 : 4000;
      if (key === 'link') input.placeholder = '여러 주소는 쉼표(,)로 구분';
      field.append(input);
      target.append(field);
    }
  }

  try {
    // Start independent rating work, but do not reveal provisional metadata.
    loadRating();
    if (context.initialDetailData) {
      // No await on the prepared path: metadata is applied before first paint.
      loadDetailData(false, context.initialDetailData);
      if (detailDataReady) {
        renderHeader();
        root.dataset.ready = 'true';
      }
    }
  } catch (error) {
    console.warn('[Rabbit detail] 초기 화면 렌더링을 건너뛰었습니다.', error);
  }

  try {
    const { state } = await import('/static/js/state.js');
    if (!root.isConnected) return;
    type = state.currentLibraryType;
    if (!['general', 'adult', 'audiobook', 'video'].includes(type)) throw new Error('지원하지 않는 서재 유형입니다.');
    media = type === 'audiobook' || type === 'video';
    unit = type === 'audiobook' ? '트랙' : type === 'video' ? '편' : '권';
    if (media) meta.isbn = meta.web_id || '';
    if (type === 'audiobook') fields = [['author', '작가'], ['isbn', 'WEB ID'], ['publisher', '출판사']];
    editFields();

    if (media) {
      $('[data-action=read] i').className = 'fa-solid ' + (type === 'audiobook' ? 'fa-headphones' : 'fa-play');
      $('[data-reading-caption]').textContent = type === 'audiobook' ? '트랙 진행률' : '에피소드 진행률';
    }
    $('[data-series-books]').setAttribute('aria-label', type === 'audiobook' ? '오디오북 트랙' : type === 'video' ? '비디오 에피소드' : '시리즈 권별 목록');

    $('[data-action=read]').addEventListener('click', () => read(continueBook(), true));
    $('[data-action=reset-listening]').addEventListener('click', (event) => {
      if (type === 'audiobook') window.markSeriesUnread?.(event, context.seriesName || meta.series_name || '', libraryId);
    });
    $('[data-action=collection]').addEventListener('click', async () => {
      const button = $('[data-action=collection]');
      const menu = $('[data-collection-menu]');
      if (!menu.hidden) {
        closeCollectionMenu();
        return;
      }
      menu.hidden = false;
      button.setAttribute('aria-expanded', 'true');
      await loadCollections();
    });
    $('[data-action=favorite]').addEventListener('click', async () => {
      if (saving || !books.length) return;
      const button = $('[data-action=favorite]');
      const selected = button.getAttribute('aria-pressed') !== 'true';
      const data = new FormData();
      data.set('type', type);
      data.set('series_name', meta.series_name || context.seriesName);
      data.set('is_favorite', selected ? '1' : '0');
      button.disabled = true;
      try {
        await request('/api/media/series/favorite', { method: 'POST', body: data });
        if (!root.isConnected) return;
        books.forEach((book) => { book.is_favorite = selected ? 1 : 0; });
        button.setAttribute('aria-pressed', String(selected));
        button.setAttribute('aria-label', selected ? '즐겨찾기 해제' : '즐겨찾기 추가');
        button.querySelector('i').className = (selected ? 'fa-solid' : 'fa-regular') + ' fa-heart';
        notify(selected ? '즐겨찾기에 추가했습니다.' : '즐겨찾기를 해제했습니다.');
      } catch (error) {
        if (root.isConnected) notify(error.message, true);
      } finally {
        button.disabled = false;
      }
    });
    $('[data-action=summary]').addEventListener('click', (event) => {
      const button = event.currentTarget;
      const expanded = button.getAttribute('aria-expanded') !== 'true';
      button.setAttribute('aria-expanded', String(expanded));
      button.textContent = expanded ? '접기' : '더 보기';
      fitSummary();
    });
    $('[data-action=mobile-info-toggle]').addEventListener('click', toggleMobileInfo);
    $('[data-action=mobile-info-close]').addEventListener('click', closeMobileInfo);
    $('[data-mobile-info-backdrop]').addEventListener('click', closeMobileInfo);
    $('[data-action=edit]').addEventListener('click', () => {
      if (!canEdit) return;
      closeMobileInfo();
      editMode(true);
    });
    all('[data-action=metadata-search-toggle]').forEach((button) => button.addEventListener('click', openMetadataSearch));
    $('[data-action=metadata-confirm-same-work]')?.addEventListener('click', confirmMetadataSameWork);
    $('[data-action=metadata-search-close]').addEventListener('click', closeMetadataSearch);
    $('[data-metadata-search-form]').addEventListener('submit', searchMetadata);
    $('[data-action=cancel-edit]').addEventListener('click', () => {
      if (!dirty || window.confirm('저장하지 않은 변경사항을 버릴까요?')) editMode(false);
    });
    $('[data-edit-form]').addEventListener('input', () => { dirty = true; });
    $('[data-edit-form]').addEventListener('change', () => { dirty = true; });
    $('[data-edit-form]').addEventListener('submit', save);

    const stopNavigation = (event) => {
      if (!root.isConnected) return;
      const element = event.target.closest?.('a, button, [data-id], .menu-item, .book-card');
      if (!element || !(dirty || saving) || root.contains(element)) return;
      if (!allowNavigation()) {
        event.preventDefault();
        event.stopImmediatePropagation();
      }
    };
    const closeMenuOnOutsideClick = (event) => {
      const target = event.target;
      if (!target.closest?.('[data-action=collection], [data-collection-menu]')) closeCollectionMenu();
    };
    const closeMenuOnEscape = (event) => {
      if (event.key === 'Escape' && !$('[data-collection-menu]').hidden) closeCollectionMenu();
    };
    const beforeUnload = (event) => {
      if (root.isConnected && (dirty || saving)) {
        event.preventDefault();
        event.returnValue = '';
      }
    };
    // The core detail delegator handles this role at document level and can
    // stop propagation before an individual anchor listener runs. Handle the
    // download in capture phase so the toast and download start together.
    const handleDownloadClick = (event) => {
      const target = event.target.closest?.('[data-role="detail-download-link"]');
      if (!target || !root.contains(target)) return;
      event.preventDefault();
      event.stopPropagation();
      triggerDownload(target.href);
    };
    const handleReadingChange = (event) => {
      const change = event.detail || {};
      if (change.type !== type || !root.isConnected) return;
      const markedRead = event.type === 'book-reading-updated' && Number(change.is_completed) === 1;
      let changed = false;
      for (const book of books) {
        const resetsAudiobook = type === 'audiobook' && String(change.id) === String(meta.id || context.representativeBookId);
        const matches = resetsAudiobook || (change.scope === 'series'
          ? String(change.libraryId) === String(libraryId) && (books.some(item => String(item.id) === String(change.id)) || String(change.seriesName) === String(context.seriesName || meta.series_name || books[0]?.series_name || ''))
          : String(change.id) === String(book.id));
        if (!matches) continue;
        book.pages_read = markedRead ? Math.max(1, num(book.total_pages)) : 0;
        book.is_completed = markedRead ? 1 : 0;
        book.last_read_at = markedRead ? new Date().toISOString() : null;
        changed = true;
      }
      if (changed) {
        renderHeader();
        renderSeries();
        loadDetailData();
      }
    };
    const handleViewerProgress = async (event) => {
      if (!root.isConnected || (event.detail?.type && event.detail.type !== type)
          || !books.some(book => String(book.id) === String(event.detail?.bookId))) return;
      await loadDetailData();
    };
    document.addEventListener('viewer-progress-flushed', handleViewerProgress);
    document.addEventListener('book-reading-reset', handleReadingChange);
    document.addEventListener('book-reading-updated', handleReadingChange);
    document.addEventListener('click', handleDownloadClick, true);
    document.addEventListener('click', stopNavigation, true);
    document.addEventListener('click', closeMenuOnOutsideClick);
    document.addEventListener('keydown', closeMenuOnEscape);
    window.addEventListener('beforeunload', beforeUnload);
    const repositionInfoPanel = () => positionMobileInfoPanel();
    window.addEventListener('resize', repositionInfoPanel, { passive: true });
    relationGraphMobile.addEventListener('change',refreshRelationGraphDisplay);
    const summaryObserver = new ResizeObserver(fitSummary);
    summaryObserver.observe($('.ds-description'));
    const observer = new MutationObserver(() => {
      if (root.isConnected) return;
      document.removeEventListener('viewer-progress-flushed', handleViewerProgress);
      document.removeEventListener('book-reading-reset', handleReadingChange);
      document.removeEventListener('book-reading-updated', handleReadingChange);
      document.removeEventListener('click', handleDownloadClick, true);
      document.removeEventListener('click', stopNavigation, true);
      document.removeEventListener('click', closeMenuOnOutsideClick);
      document.removeEventListener('keydown', closeMenuOnEscape);
      window.removeEventListener('beforeunload', beforeUnload);
      window.removeEventListener('resize', repositionInfoPanel);
      relationGraphMobile.removeEventListener('change',refreshRelationGraphDisplay);
      observer.disconnect();
      summaryObserver.disconnect();
      stopMetadataRefresh();
      clearTimeout(noticeTimer);
    });
    observer.observe(container.parentNode || document.body, { childList: true, subtree: true });

    bindBookMenu($('.ds-cover'), books[0], true);
    // Fetch recommendations independently; reveal the header only after the
    // DB-backed detail data has established labels, filters and permissions.
    loadDiscovery();
    const detailDataPromise = detailDataReady ? Promise.resolve(true) : loadDetailData(false);
    const detailLoaded = await detailDataPromise;
    if (!root.isConnected) return;
    if (root.dataset.ready !== 'true') renderHeader();
    root.dataset.ready = 'true';
    startMetadataRefresh();
  } catch (error) {
    root.dataset.ready = 'true';
    notify('상세 화면을 불러오지 못했습니다. ' + error.message, true);
  }
})();
