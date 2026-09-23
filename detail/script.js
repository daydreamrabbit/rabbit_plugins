// Rabbit Plugins 상세 화면을 렌더링하고 BookOasis의 읽기·편집 API에 연결합니다.
(async function () {
  'use strict';

  const root = container.querySelector('.ds');
  if (!root) return;

  const $ = (selector) => root.querySelector(selector);
  const all = (selector) => [...root.querySelectorAll(selector)];
  const meta = { ...(context.meta || {}) };
  const books = (context.books || []).filter((book) => Number.isInteger(Number(book.id)) && Number(book.id) > 0);
  const libraryTypes = { general: '일반도서', adult: '성인도서', audiobook: '오디오북', video: '영상강좌' };
  const relationLabels = {
    main_story: '본편', prequel: '전작', sequel: '후속작', spin_off: '스핀오프',
    side_story: '외전', alternative: '다른 판본', adaptation: '각색', other: '기타',
  };
  let type = 'general';
  let media = false;
  let unit = '권';
  let libraryName = '';
  let libraryId = context.libraryId || null;
  let canEdit = false;
  let editScope = null;
  let dirty = false;
  let saving = false;
  let summaryHtmlEnabled = false;
  let excludedGenres = new Set();
  let excludedTags = new Set();
  let contentKind = '';
  let metadataSources = [];
  let metadataAutoEnabled = false;
  let metadataHold = {};
  let metadataFields = [];
  let metadataRefreshTimer = null;
  let metadataRefreshAttempts = 0;
  let metadataRefreshInFlight = false;
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
  let loadingCollections = false;
  let noticeTimer;
  let fields = [
    ['series_alias', '표시 제목'],
    ['author', '작가'],
    ['cover_artist', '그림작가'],
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
    const raw = String(book.title_alias || book.title || '제목 없음').trim();
    // Metadata providers often prefix the display title with a publisher or
    // imprint tag such as "[미즈]". Keep the metadata title, but hide only
    // leading bracket tags in the volume and chapter cards.
    const withoutDescription = raw.split(/\s*작품\s*소개\s*[:：]/u, 1)[0].trim();
    return withoutDescription.replace(/^(?:(?:\[[^\]]+\]|\{[^}]+\})\s*)+/u, '').trim() || raw;
  };
  const fileDisplayLabel = (book, index = 0, options = {}) => {
    // 파일명이 권별 표시의 기준입니다. 메타데이터 제목에 권 번호가 있어도
    // 파일명에 붙은 한정판·완결 같은 판본 표기를 우선 보존합니다.
    const candidates = [book?.file_path, book?.title_alias, book?.title]
      .map((value) => String(value || '').replace(/\\/g, '/').split('/').pop() || '')
      .filter(Boolean);
    const sourceSuffix = /\s*\((?:리디|교보|예스(?:24)?|네이버|카카오|마나부|스캔|웹툰|레진|익헨|마블|일러스트|펌|확인)\)\s*(?:#\d+)?$/iu;
    const technicalSuffix = /\s*\[(?:\d{3,5}\s*[x×](?:\s*\d{2,5})?|\d{3,5}\s*[pP])\]\s*/gu;
    const volumeOnlyPattern = /((?:제\s*)?\d+(?:\.\d+)?\s*권(?:\s*\([^)]*\))*)/iu;
    const volumePattern = /((?:제\s*)?\d+(?:\.\d+)?\s*(?:권|화|회|편)(?:\s*\([^)]*\))*)/iu;
    for (const candidate of candidates) {
      const base = candidate.replace(/\.[^.]+$/u, '').replace(sourceSuffix, '').replace(technicalSuffix, ' ').trim();
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
      const volume = volumeNumber(book);
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

  function safeUrl(value, cover = false) {
    let raw = String(value || '').trim();
    if (!raw) return '';
    if (cover && !/^(https?:|\/)/i.test(raw)) raw = '/covers/' + raw.replace(/^covers\//, '');
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
  });

  function siteInfo(link) {
    const url = new URL(link);
    const host = url.hostname.replace(/^www\./i, '').toLocaleLowerCase();
    const domain = Object.keys(siteNames).find((name) => host === name || host.endsWith('.' + name));
    const faviconDomain = Object.keys(siteFavicons).find((name) => host === name || host.endsWith('.' + name));
    return {
      label: siteNames[domain] || host,
      favicon: siteFavicons[faviconDomain]
        || 'https://www.google.com/s2/favicons?domain=' + encodeURIComponent(host) + '&sz=32',
    };
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
      if (numeric >= 18) return { level: 18, rank: 2, label: '18세 이용가', specific: false };
      if (numeric >= 15) return { level: 15, rank: 1, label: '15세 이용가', specific: false };
      if (numeric >= 0) return { level: 0, rank: 0, label: '전체 이용가', specific: false };
      return null;
    }
    if (['porn', 'porno', 'pornography', 'pornographic', '포르노', '포르노그래피'].includes(compact)) {
      return { level: 18, rank: 4, label: '포르노', specific: true };
    }
    if (['adultonly18+', 'adultsonly18+', 'adultonly18', 'adultsonly18'].includes(compact)) {
      return { level: 18, rank: 4, label: '포르노', specific: true };
    }
    if (['성인망가', 'r18', 'r18+', 'x18+', 'xrated'].includes(compact) || /(?:r18|x-rated|청소년관람불가|성인망가)/i.test(text)) {
      return { level: 18, rank: 3, label: '성인망가', specific: true };
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
          signal: AbortSignal.timeout(20000),
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
        if (attempt === 0 && /HTTP (?:502|503|504)|요청 실패 \((?:502|503|504)\)/.test(error.message || '')) {
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
    // The provider intentionally keeps the filename-derived volume title.
    return metadataFields.some((field) => {
      if (field === 'title') return false;
      const value = String(metadataValue(field) || '').trim();
      return !value || value === '-' || value === '등록된 설명이 없습니다.';
    });
  }

  function metadataSnapshot() {
    const keys = ['series_alias', 'localized_series', 'author', 'cover_artist', 'publisher',
      'summary', 'genre', 'tags', 'isbn', 'link', 'cover_image'];
    return JSON.stringify({
      meta: keys.map((key) => String(meta[key] || '')),
      dates: meta.publication_dates || {},
      books: books.map((book) => [book.id, book.cover_image || '', book.release_date || '']),
    });
  }

  function stopMetadataRefresh() {
    if (metadataRefreshTimer) clearInterval(metadataRefreshTimer);
    metadataRefreshTimer = null;
    metadataRefreshInFlight = false;
  }

  async function refreshMetadataFromServer() {
    const beforeDiscoveryKey = discoveryInputKey();
    const params = new URLSearchParams({
      type,
      series: context.seriesName,
      library_id: context.libraryId || libraryId || 'all',
      representative_book_id: books[0]?.id || meta.id || '',
    });
    const latest = await request('/api/media/detail?' + params);
    if (!latest.meta) return false;
    const before = metadataSnapshot();
    Object.assign(meta, latest.meta);
    if (Array.isArray(latest.books)) {
      books.splice(0, books.length, ...latest.books);
    }
    const changed = before !== metadataSnapshot();
    if (!changed) return false;
    await loadDetailData(false);
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
    return JSON.stringify({ relations, recommendations });
  }

  function renderDiscoveryPayload(data) {
    const signature = discoveryPayloadSignature(data);
    if (signature === discoveryRenderedSignature) return false;
    renderRelations(data.relations || {});
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
      const element = node('i', 'fa-' + (filled ? 'solid' : 'regular') + ' fa-star');
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
        data = { ...data, ...result, my_rating: normalizeRating(result.my_rating ?? rating) };
        updateSummary();
        if (root.isConnected) notify('별점을 저장했습니다.');
      } catch (error) {
        if (root.isConnected) notify(error.message || '별점 저장에 실패했습니다.', true);
      } finally {
        submitting = false;
        if (root.isConnected) {
          target.querySelectorAll('button[data-rating-step]').forEach((button) => { button.disabled = false; });
          paint(data?.my_rating);
        }
      }
    };

    const initialRating = normalizeRating(num(meta.score) / 20);
    build(initialRating, false);
    if (type !== 'general') return;

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

  function bindBookMenu(target, book) {
    if (media || !book) return;
    target.title = title(book) + ' · 우클릭으로 도서 메뉴';
    target.addEventListener('contextmenu', (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (dirty || saving) return notify('편집을 저장하거나 취소한 후 도서 메뉴를 열어 주세요.');
      if (typeof window.showBookContextMenu !== 'function') return notify('코어 도서 메뉴를 연결하지 못했습니다. 새로고침해 주세요.', true);
      window.showBookContextMenu(event.clientX, event.clientY, Number(book.id), title(book), true, {
        seriesName: meta.series_name || context.seriesName,
        libraryId: book.library_id ?? libraryId ?? context.libraryId,
        markUnreadScope: 'book',
        fileFormat: String(book.file_format || '').toLowerCase(),
        // The core menu uses this flag to choose the symmetric read/unread
        // action.  Plugin-rendered cards do not carry the core card's
        // data-has-progress attribute, so pass the state explicitly.
        hasProgress: completed(book) || progress(book) > 0,
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
    button.type = 'button';
    const label = linked ? book.display_name || book.series_name || title(book) : fileDisplayLabel(book, index);
    button.setAttribute('aria-label', label + (linked ? ' 상세 보기' : media ? ' 재생' : ' 읽기'));
    const art = image(linked ? book.cover : book.cover_image, label);

    if (!linked) {
      bindBookMenu(art, book);
      const number = chapterOnlyMode() && chapterNumber(book) > 0
        ? numberText(chapterNumber(book)) + '화'
        : String(index + 1).padStart(2, '0');
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
    const titleNode = node('span', 'ds-book-title', label);
    let subtitle = null;
    let progressDisplay = null;
    if (linked) {
      button.append(titleNode);
      const originalTitle = String(book.localized_series || '').trim();
      if (originalTitle) button.append(node('span', 'ds-book-subtitle', originalTitle));
      button.addEventListener('click', async () => {
        if (!allowNavigation()) return;
        if (book.library_id != null && String(book.library_id) !== String(libraryId)
          && typeof window.selectCategory === 'function') {
          await window.selectCategory(String(book.library_id), true);
        }
        if (typeof window.openBookDetail === 'function') {
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
      card.append(button, titleNode, subtitle, progressDisplay);
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

  function renderInfo() {
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
    const totalVolumes = hasVolumesAndChapters ? groups.length : count > 0 ? count : books.length;
    const explicitStatus = String(meta.publication_status_label || '').trim();
    const hasExplicitStatus = explicitStatus && explicitStatus !== '알 수 없음';
    const availableVolumeCount = Number(meta.publication_available_volume_count) || 0;
    const countLabel = onlyChapters
      ? '총화수'
      : type === 'audiobook' ? '트랙 수' : type === 'video' ? '에피소드 수' : '총권수';
    const rows = [];
    if (books.length < 2) rows.push(['파일포맷', formats.join(' / ') || '—']);
    if (!onlyChapters) rows.push([countLabel, String(totalVolumes)]);
    const chapters = Number(meta.manual_chapter_count || (['완결', '연재중단'].includes(explicitStatus) ? meta.publication_coverage?.total : 0) || 0);
    if (Number.isInteger(chapters) && chapters > 0) {
      rows.push(['회차 수', String(chapters)]);
    }
    if (meta.author && meta.author !== '-') rows.push(['글작가', meta.author]);
    const artist = meta.artist || meta.cover_artist;
    if (artist && artist !== '-') rows.push(['그림작가', artist]);
    if (meta.publisher && meta.publisher !== '-') rows.push(['출판사', meta.publisher]);
    if (!media) {
      const chapterFinalFound = chapterTarget > 0
        && chapterNumbers.some((number) => number === chapterTarget)
        && !coverage.missing;
      const remoteCoverage = meta.publication_coverage || {};
      const incomplete = explicitStatus === '완결' && remoteCoverage.known && remoteCoverage.missing > 0;
      const status = incomplete
        ? '누락 (' + remoteCoverage.present + '/' + remoteCoverage.total + '화)'
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
      if (contentKind === 'book') {
        const published = meta.release_date || books.find((book) => book.release_date)?.release_date;
        rows.push(['출간일', published ? String(published).slice(0, 10) : '—']);
      } else {
        rows.push(['연재상태', status]);
        rows.push(['연재시작일', meta.publication_dates?.start || '—']);
        rows.push(['연재종료일', meta.publication_dates?.end || '—']);
      }
      rows.push(['연령등급', ageRating?.label || '—']);
    }
    const identifier = [meta.isbn, meta.web_id]
      .filter((value, index, all) => value && value !== '-' && all.indexOf(value) === index)
      .join(' / ') || '—';
    rows.push(['ISBN / Web ID', identifier]);

    const facts = $('[data-facts]');
    facts.replaceChildren();
    for (const [label, value] of rows) {
      const row = document.createElement('div');
      const factClass = label === '글작가'
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
      } else if ((label === '글작가' || label === '그림작가') && value && value !== '—') {
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

    const links = String(meta.link || '').split(/[,;\n]+/).map((value) => safeUrl(value.trim())).filter(Boolean);
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
    const labels = { series_db: '데이터베이스', ridi: '리디', naver: '네이버시리즈', naver_webtoon: '네이버웹툰', kyobo: '교보문고', kakao_webtoon: '카카오웹툰', kakaopage: '카카오페이지', munpia: '문피아', novelpia: '노벨피아' };
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
      const params = new URLSearchParams({ type, series: context.seriesName,
        library_id: context.libraryId || 'all', representative_book_id: books[0]?.id || '' });
      const detail = await request('/api/media/detail?' + params);
      if (detail.meta) Object.assign(meta, detail.meta);
      coverRevision += 1;
      await loadDetailData();
      invalidateSeriesList();
      closeMetadataSearch();
      notify(result.message || '메타데이터를 적용했습니다.');
    } catch (error) {
      notify(error.message || '메타데이터를 적용하지 못했습니다.', true);
      button.disabled = false; button.textContent = '이 메타데이터 적용';
    }
  }

  function renderSeries() {
    const section = $('[data-series-section]');
    const list = $('[data-series-books]');
    section.hidden = books.length < 2;
    if (groupedVolumeMode()) {
      const groups = volumeGroups();
      $('[data-series-description]').textContent = groups.length + '권 · 표지를 누르면 화 목록을 확인하고 책 아이콘으로 읽습니다.';
      list.replaceChildren(...groups.map((group, index) => volumeCard(group, index)));
      return;
    }
    if (chapterOnlyMode()) {
      $('[data-series-description]').textContent = books.length + '화 · 표지를 누르면 읽기를 시작합니다.';
      list.replaceChildren(...books.map((book, index) => bookCard(book, index)));
      return;
    }
    const label = type === 'audiobook' ? '트랙' : type === 'video' ? '에피소드' : '권';
    $('[data-series-description]').textContent = books.length + label + ' · 표지를 누르면 ' + (media ? '재생' : '읽기') + '를 시작합니다.';
    list.replaceChildren(...books.map((book, index) => bookCard(book, index)));
  }

  function renderRelations(relations) {
    const section = $('[data-relations-section]');
    const target = $('[data-relations]');
    const cards = [];
    const seenWorks = new Set();
    for (const [type, label] of Object.entries(relationLabels)) {
      const items = Array.isArray(relations?.[type]) ? relations[type] : [];
      if (!items.length) continue;
      for (const book of items) {
        const key = String(book?.series_name || book?.display_name || book?.book_id || '').trim().toLocaleLowerCase();
        if (!key || seenWorks.has(key)) continue;
        seenWorks.add(key);
        cards.push(bookCard(book, cards.length, true, label));
      }
    }
    target.replaceChildren(...cards);
    section.hidden = cards.length === 0;
  }

  function renderRecommendations(items) {
    const section = $('[data-recommendation-section]');
    const target = $('[data-recommendations]');
    const books = Array.isArray(items) ? items : [];
    target.replaceChildren(...books.map((book, index) => bookCard(book, index, true)));
    section.hidden = books.length === 0;
  }

  function renderLibraryPath() {
    const path = $('[data-library-path]');
    path.replaceChildren(node('span', '', 'LIBRARY'));
    const entries = [[libraryTypes[type], 'home'], [libraryName, libraryId]];
    for (const [label, id] of entries) {
      if (!label) continue;
      const item = node('span');
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
  }

  function renderHeader() {
    renderLibraryPath();
    const displayTitle = String(meta.series_alias || meta.series_name || context.seriesName || '도서 상세')
      .replace(/(?:\s*\[[^\]]*\])+\s*$/u, '').trim() || '도서 상세';
    $('[data-title]').textContent = displayTitle;
    renderMetadataHold();
    const originalTitle = String(meta.localized_series || '').trim();
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
    $('[data-reading-state]').textContent = done === books.length && done
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
    const favorite = $('[data-action=favorite]');
    const isFavorite = books.length > 0 && books.every((book) => Number(book.is_favorite) === 1);
    favorite.disabled = !books.length;
    favorite.setAttribute('aria-pressed', String(isFavorite));
    favorite.setAttribute('aria-label', isFavorite ? '즐겨찾기 해제' : '즐겨찾기 추가');
    favorite.querySelector('i').className = (isFavorite ? 'fa-solid' : 'fa-regular') + ' fa-heart';

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
    const sourceLabels = { ridi: '리디', naver: '네이버시리즈', kyobo: '교보문고',
      kakaopage: '카카오페이지', kakao_webtoon: '카카오웹툰',
      munpia: '문피아', novelpia: '노벨피아' };
    panel.hidden = !canEdit || metadataHold?.reason !== 'author_conflict';
    const list = $('[data-metadata-hold-candidates]');
    list.replaceChildren(...candidates.map((candidate) => {
      const source = sourceLabels[candidate?.source] || String(candidate?.source || '제공처');
      const title = String(candidate?.title || '').trim();
      const author = String(candidate?.author || '작가 미상').trim();
      return node('li', '', `${source} · ${title} · ${author}`);
    }));
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
    for (const [key] of activeEditFields()) {
      if (!form.elements[key]) continue;
      const value = key === 'books_lv'
        ? editContentRatingValue()
        : key === 'link'
        ? split(meta[key]).join(', ')
        : key === 'publication_start_date'
          ? meta.publication_dates?.start || ''
          : key === 'publication_end_date'
            ? meta.publication_dates?.end || ''
            : meta[key] || '';
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
    const file = data.get('cover_image');
    if (file?.size && (file.size > 10 * 1024 * 1024 || !['image/jpeg', 'image/png', 'image/webp'].includes(file.type))) {
      return notify('표지는 10MB 이하의 JPG, PNG, WebP 파일을 선택해 주세요.', true);
    }
    if (!file?.size) data.delete('cover_image');
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
      let coverArtistSaved = true;
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
                ...(contentKind === 'book'
                  ? { release_date: String(data.get('release_date') || '').trim() }
                  : { publication_start_date: String(data.get('publication_start_date') || '').trim(),
                      publication_end_date: String(data.get('publication_end_date') || '').trim() }),
                manual_chapter_count: String(data.get('manual_chapter_count') || '').trim(),
              },
            }),
          });
          coverArtistSaved = result.cover_artist_saved !== false;
          detailFieldsWarning = (result.warnings || []).join(' ');
        } catch (error) {
          detailFieldsError = error.message;
        }
      }
      for (const [key] of activeEditFields()) {
        if (key === 'release_date' || key === 'publication_start_date' || key === 'publication_end_date' || key === 'manual_chapter_count') continue;
        if (key !== 'cover_artist' || (!detailFieldsError && coverArtistSaved)) meta[key] = String(data.get(key) || '');
      }
      if (!detailFieldsError && (type === 'general' || type === 'adult')) {
        if (contentKind === 'book') {
          meta.release_date = String(data.get('release_date') || '').trim();
          books.forEach((book) => { book.release_date = meta.release_date; });
        }
        meta.manual_chapter_count = String(data.get('manual_chapter_count') || '').trim();
        meta.publication_dates = {
          start: String(data.get('publication_start_date') || meta.publication_dates?.start || ''),
          end: String(data.get('publication_end_date') || meta.publication_dates?.end || ''),
        };
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
      if (!detailFieldsError && coverArtistSaved && meta.cover_artist && meta.cover_artist !== '-') meta.artist = meta.cover_artist;
      coverRevision += 1;
      invalidateSeriesList();
      saving = false;
      editMode(false);
      renderHeader();
      loadDiscovery({ refresh: true });
      notify(detailFieldsError
        ? '나머지 메타정보는 저장했지만 그림작가와 연재일을 저장하지 못했습니다. ' + detailFieldsError
        : detailFieldsWarning ? '일부 상세정보는 저장했지만 그림작가 변경은 저장하지 못했습니다. ' + detailFieldsWarning
        : refreshed ? '시리즈 메타정보를 저장했습니다.' : '저장은 완료했습니다. 최신 표지는 페이지를 다시 열면 확인할 수 있습니다.',
      Boolean(detailFieldsError || detailFieldsWarning));
    } catch (error) {
      if (root.isConnected) notify('저장하지 못했습니다. 입력 내용은 유지됩니다. ' + error.message, true);
    } finally {
      saving = false;
      all('[data-edit-form] button, [data-edit-form] input, [data-edit-form] textarea, [data-edit-form] select')
        .forEach((element) => { element.disabled = false; });
    }
  }

  async function loadDetailData(render = true) {
    if (!books.length) return false;
    try {
      const data = await request(apiUrl('files'));
      if (!root.isConnected) return;
      summaryHtmlEnabled = data.support_summary_html === true;
      excludedGenres = excludedTerms(data.exclude_genres);
      excludedTags = excludedTerms(data.exclude_tags);
      contentKind = data.content_kind || contentKind;
      metadataSources = Array.isArray(data.metadata_sources) ? data.metadata_sources : metadataSources;
      metadataAutoEnabled = data.metadata_auto_enabled === true;
      metadataHold = data.metadata_hold || {};
      metadataFields = Array.isArray(data.metadata_fields) ? data.metadata_fields : metadataFields;
      canDownload = data.can_download !== false;
      const currentBooks = new Map(books.map((book) => [Number(book.id), book]));
      for (const file of data.files || []) {
        const book = currentBooks.get(Number(file.id));
        if (!book) continue;
        if (file.file_path) book.file_path = file.file_path;
        if (file.file_format) book.file_format = file.file_format;
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
      const combinedGenres = (storedGenres.length
        ? storedGenres
        : (data.files || []).flatMap((file) => split(file.genre)));
      const combinedTags = (storedTags.length
        ? storedTags
        : (data.files || []).flatMap((file) => split(file.tags)));
      const cleanTerms = distinctGenreTags(combinedGenres.join(', '), combinedTags.join(', '));
      meta.genre = cleanTerms.genre;
      meta.tags = cleanTerms.tags;
      applyContentRating(data.files || []);
      const comicinfo = data.comicinfo || {};
      meta.localized_series = meta.localized_series || data.localized_series || comicinfo.localized_series || '';
      if (summaryHtmlEnabled && !hasStoredSummary(meta.summary) && String(comicinfo.summary || '').trim()) {
        meta.summary = comicinfo.summary;
      }
      meta.artist = meta.cover_artist && meta.cover_artist !== '-'
        ? meta.cover_artist
        : comicinfo.artist || '';
      meta.comicinfo_format = comicinfo.format || '';
      meta.comicinfo_count = comicinfo.count;
      meta.comicinfo_volume = comicinfo.volume;
      meta.comicinfo_number = comicinfo.number;
      meta.publication_final_volume_found = comicinfo.final_volume_found === true;
      meta.publication_available_volume_count = Number(comicinfo.available_volume_count) || 0;
      const storedPublicationStatus = String(meta.publication_status ?? '').trim();
      meta.publication_status_label = ['0', '1', '2'].includes(storedPublicationStatus)
        ? (meta.publication_status_label || '')
        : (comicinfo.publication_status_label || meta.publication_status_label || '');
      meta.publication_dates = data.publication_dates || {};
      meta.publication_coverage = data.publication_coverage || {};
      meta.manual_chapter_count = data.manual_chapter_count || '';
      canEdit = type !== 'video' && data.can_edit === true;
      editScope = data.edit_scope;
      libraryName = data.library_name || '';
      libraryId = data.library_id ?? context.libraryId ?? null;
      if (render) renderHeader();
      return true;
    } catch (error) {
      if (root.isConnected) notify('추가 상세 정보를 확인하지 못했습니다. ' + error.message, true);
      return false;
    }
  }

  function renderMetadataRefresh() {
    if (!root.isConnected) return;
    const displayTitle = String(meta.series_alias || meta.series_name || context.seriesName || '도서 상세')
      .replace(/(?:\s*\[[^\]]*\])+\s*$/u, '').trim() || '도서 상세';
    const titleNode = $('[data-title]');
    if (titleNode.textContent !== displayTitle) titleNode.textContent = displayTitle;
    const originalTitle = String(meta.localized_series || '').trim();
    const originalTitleNode = $('[data-original-title]');
    const showOriginal = Boolean(originalTitle)
      && originalTitle.toLocaleLowerCase() !== displayTitle.toLocaleLowerCase();
    originalTitleNode.hidden = !showOriginal;
    originalTitleNode.textContent = showOriginal ? originalTitle : '';
    renderSummary(meta.summary);
    renderInfo();
    renderAppearance();
    requestAnimationFrame(fitSummary);
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
      }
    } finally {
      loadingDiscovery = false;
      loading.removeAttribute('aria-busy');
      if (!hasExistingResults || discoveryLoaded) {
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
    if (contentKind !== 'book') return fields;
    return fields.flatMap(([key, label]) => key === 'publication_start_date'
      ? [['release_date', '출간일']]
      : key === 'publication_end_date' ? [] : [[key, label]]);
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
      input.maxLength = key === 'link' ? 2000 : key === 'cover_artist' ? 500 : 4000;
      if (key === 'link') input.placeholder = '여러 주소는 쉼표(,)로 구분';
      field.append(input);
      target.append(field);
    }
  }

  try {
    renderHeader();
    root.dataset.ready = 'true';
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
    $('[data-action=metadata-search-close]').addEventListener('click', closeMetadataSearch);
    $('[data-metadata-search-form]').addEventListener('submit', searchMetadata);
    $('[data-action=cancel-edit]').addEventListener('click', () => {
      if (!dirty || window.confirm('저장하지 않은 변경사항을 버릴까요?')) editMode(false);
    });
    $('[data-edit-form]').addEventListener('input', () => { dirty = true; });
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
    document.addEventListener('click', handleDownloadClick, true);
    document.addEventListener('click', stopNavigation, true);
    document.addEventListener('click', closeMenuOnOutsideClick);
    document.addEventListener('keydown', closeMenuOnEscape);
    window.addEventListener('beforeunload', beforeUnload);
    const repositionInfoPanel = () => positionMobileInfoPanel();
    window.addEventListener('resize', repositionInfoPanel, { passive: true });
    const summaryObserver = new ResizeObserver(fitSummary);
    summaryObserver.observe($('.ds-description'));
    const observer = new MutationObserver(() => {
      if (root.isConnected) return;
      document.removeEventListener('click', handleDownloadClick, true);
      document.removeEventListener('click', stopNavigation, true);
      document.removeEventListener('click', closeMenuOnOutsideClick);
      document.removeEventListener('keydown', closeMenuOnEscape);
      window.removeEventListener('beforeunload', beforeUnload);
      window.removeEventListener('resize', repositionInfoPanel);
      observer.disconnect();
      summaryObserver.disconnect();
      stopMetadataRefresh();
      clearTimeout(noticeTimer);
    });
    observer.observe(container.parentNode || document.body, { childList: true, subtree: true });

    bindBookMenu($('.ds-cover'), books[0]);
    // The core now switches to this view only after the bundle is inserted.
    // Render the data already supplied in the bundle synchronously so the
    // first frame is useful, then enrich it with file-level metadata without
    // showing an empty/blank detail page during the extra request.
    renderHeader();
    root.dataset.ready = 'true';
    const detailLoaded = await loadDetailData(false);
    if (!root.isConnected) return;
    if (detailLoaded) {
      renderHeader();
    }
    loadDiscovery();
    startMetadataRefresh();
    loadRating();
  } catch (error) {
    root.dataset.ready = 'true';
    notify('상세 화면을 불러오지 못했습니다. ' + error.message, true);
  }
})();
