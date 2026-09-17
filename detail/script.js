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
    side_story: '외전', alternative: '리메이크', adaptation: '각색', other: '기타',
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
  let discoveryLoaded = false;
  let loadingDiscovery = false;
  let loadingCollections = false;
  let noticeTimer;
  let fields = [
    ['series_alias', '표시 제목'],
    ['author', '작가'],
    ['cover_artist', '그림작가'],
    ['publisher', '출판사'],
    ['publication_start_date', '연재시작일'],
    ['publication_end_date', '연재종료일'],
    ['isbn', 'ISBN / Web ID'],
    ['genre', '장르'],
    ['tags', '태그'],
    ['link', '관련 링크'],
  ];

  const title = (book) => book.title_alias || book.title || '제목 없음';
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

  function split(value) {
    return [...new Set(String(value || '').split(/[,;|\n]/).map((part) => part.trim()).filter((part) => part && part !== '-'))];
  }

  function excludedTerms(value) {
    return new Set(String(value || '').split(',').map((term) => term.trim().toLocaleLowerCase()).filter(Boolean));
  }

  function fileFormatBadge(value) {
    const format = String(value || '').trim().toUpperCase();
    if (!format) return null;
    const kind = ['CBZ', 'EPUB', 'PDF', 'ZIP'].includes(format) ? format.toLowerCase() : 'other';
    return node('span', 'ds-file-format ds-file-format-' + kind, format);
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
      return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
    } catch {
      return '';
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

  function siteInfo(link) {
    const url = new URL(link);
    const host = url.hostname.replace(/^www\./i, '').toLocaleLowerCase();
    const domain = Object.keys(siteNames).find((name) => host === name || host.endsWith('.' + name));
    return { label: siteNames[domain] || host, favicon: url.origin + '/favicon.ico' };
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
    const source = String(value || '');
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
    const response = await fetch(url, {
      credentials: 'same-origin',
      cache: 'no-store',
      signal: AbortSignal.timeout(20000),
      ...options,
    });
    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error('서버 응답을 읽지 못했습니다. 로그인 상태를 확인해 주세요.');
    }
    if (!response.ok || !data.success) throw new Error(data.error || data.message || '요청 실패 (' + response.status + ')');
    return data;
  }

  function apiUrl(mode) {
    const bookId = media ? meta.id : books[0]?.id;
    const params = new URLSearchParams({ type, book_id: String(bookId || ''), mode, limit: '18' });
    return '/api/media/dashboard/widgets/' + encodeURIComponent(pluginId) + '/data?' + params;
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
    window.openReader(Number(book.id), book.file_format, title(book), num(book.pages_read), num(book.total_pages));
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

    build(normalizeRating(num(meta.score) / 20), false);
    if (type !== 'general') return;

    const ratingContext = {
      series_name: meta.series_name || context.seriesName || '',
      author: meta.author || '',
      isbn: meta.isbn || '',
    };
    try {
      api = await import('/static/js/api.js');
      data = await api.fetchRatingWidget(type, ratingContext);
      if (!root.isConnected || !data.success) return;
      build(normalizeRating(data.my_rating), true);
    } catch {
      // 활성 제공자가 없거나 연결에 실패하면 코어 점수 별표를 유지합니다.
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
    const label = linked ? book.display_name || book.series_name || title(book) : title(book);
    button.setAttribute('aria-label', label + (linked ? ' 상세 보기' : media ? ' 재생' : ' 읽기'));
    const art = image(linked ? book.cover : book.cover_image, label);

    if (!linked) {
      bindBookMenu(art, book);
      art.append(node('span', 'ds-book-number', String(index + 1).padStart(2, '0')));
      const overlay = node('span', 'ds-book-overlay');
      overlay.setAttribute('aria-hidden', 'true');
      overlay.append(icon(media ? 'play' : 'book-open'));
      art.append(overlay);
    }

    button.append(art, node('span', 'ds-book-title', label));
    if (linked) {
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
      const subtitle = node('span', 'ds-book-subtitle');
      const format = fileFormatBadge(book.file_format);
      if (format) subtitle.append(format, document.createTextNode(' · '));
      subtitle.append(document.createTextNode(state));
      button.append(subtitle);
      const percent = progress(book);
      const progressDisplay = node('span', 'ds-book-progress');
      const bar = document.createElement('progress');
      bar.max = 100;
      bar.value = percent;
      bar.setAttribute('aria-label', label + ' 진행률');
      progressDisplay.append(bar, node('small', 'ds-book-progress-value', percent + '%'));
      button.append(progressDisplay);
      button.addEventListener('click', () => read(book));
    }

    if (relationLabel) card.append(node('span', 'ds-relation-label', relationLabel));
    card.append(button);
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
    const totalVolumes = count > 0 ? count : books.length;
    const countLabel = type === 'audiobook' ? '트랙 수' : type === 'video' ? '에피소드 수' : '총권수';
    const rows = [];
    if (books.length < 2) rows.push(['파일포맷', formats.join(' / ') || '—']);
    rows.push([countLabel, String(totalVolumes)]);
    const chapters = Number(meta.chapter_count ?? meta.chapters_count ?? meta.total_chapters ?? 0);
    if (Number.isFinite(chapters) && chapters > 0) rows.push(['회차 수', String(chapters)]);
    if (meta.author && meta.author !== '-') rows.push(['글작가', meta.author]);
    const artist = meta.artist || meta.cover_artist;
    if (artist && artist !== '-') rows.push(['그림작가', artist]);
    if (meta.publisher && meta.publisher !== '-') rows.push(['출판사', meta.publisher]);
    if (!media) {
      const status = count === 1 && volume === 1 && comicFormat === 'special'
        ? '단편'
        : count > 0
          ? (books.length >= count ? '완결' : '누락 (' + books.length + '/' + count + '권)')
          : volume > 0 ? '연재' : meta.publication_status_label || '알 수 없음';
      rows.push(['연재상태', status]);
      rows.push(['연재시작일', meta.publication_dates?.start || '—']);
      rows.push(['연재종료일', meta.publication_dates?.end || '—']);
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
      const term = node('dt', '', label);
      const detail = document.createElement('dd');
      if (label === '파일포맷' && formats.length) {
        formats.forEach((format, index) => {
          if (index) detail.append(document.createTextNode(' / '));
          detail.append(fileFormatBadge(format));
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

  function renderSeries() {
    const section = $('[data-series-section]');
    const list = $('[data-series-books]');
    section.hidden = books.length < 2;
    const label = type === 'audiobook' ? '트랙' : type === 'video' ? '에피소드' : '권';
    $('[data-series-description]').textContent = books.length + label + ' · 표지를 누르면 ' + (media ? '재생' : '읽기') + '를 시작합니다.';
    list.replaceChildren(...books.map((book, index) => bookCard(book, index)));
  }

  function renderRelations(relations) {
    const section = $('[data-relations-section]');
    const target = $('[data-relations]');
    const cards = [];
    for (const [type, label] of Object.entries(relationLabels)) {
      const items = Array.isArray(relations?.[type]) ? relations[type] : [];
      if (!items.length) continue;
      cards.push(...items.map((book, index) => bookCard(book, index, true, label)));
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
    const displayTitle = meta.series_alias || meta.series_name || context.seriesName || '도서 상세';
    $('[data-title]').textContent = displayTitle;
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

    const done = books.filter(completed).length;
    const inProgress = books.filter(reading).length;
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
    $('[data-reading-caption]').textContent = media
      ? books.length + unit + ' 중 ' + done + unit + ' 완료'
      : books.length + '권 중 ' + done + '권 완독';

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
      const link = node('button', 'ds-text-button', title(next));
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
    form.reset();
    for (const [key] of fields) {
      if (!form.elements[key]) continue;
      const value = key === 'link'
        ? split(meta[key]).join(', ')
        : key === 'publication_start_date'
          ? meta.publication_dates?.start || ''
          : key === 'publication_end_date'
            ? meta.publication_dates?.end || ''
            : meta[key] || '';
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
                publication_start_date: String(data.get('publication_start_date') || '').trim(),
                publication_end_date: String(data.get('publication_end_date') || '').trim(),
              },
            }),
          });
          coverArtistSaved = result.cover_artist_saved !== false;
          detailFieldsWarning = (result.warnings || []).join(' ');
        } catch (error) {
          detailFieldsError = error.message;
        }
      }
      for (const [key] of fields) {
        if (key === 'publication_start_date' || key === 'publication_end_date') continue;
        if (key !== 'cover_artist' || (!detailFieldsError && coverArtistSaved)) meta[key] = String(data.get(key) || '');
      }
      if (!detailFieldsError && (type === 'general' || type === 'adult')) {
        meta.publication_dates = {
          start: String(data.get('publication_start_date') || meta.publication_dates?.start || ''),
          end: String(data.get('publication_end_date') || meta.publication_dates?.end || ''),
        };
      }
      meta.summary = String(data.get('summary') || '');
      meta.metadata_locked = type === 'audiobook' ? 0 : 1;
      dirty = false;
      discoveryLoaded = false;

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
      saving = false;
      editMode(false);
      renderHeader();
      loadDiscovery();
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

  async function loadDetailData() {
    if (!books.length) return;
    try {
      const data = await request(apiUrl('files'));
      if (!root.isConnected) return;
      summaryHtmlEnabled = data.support_summary_html === true;
      excludedGenres = excludedTerms(data.exclude_genres);
      excludedTags = excludedTerms(data.exclude_tags);
      const currentBooks = new Map(books.map((book) => [Number(book.id), book]));
      for (const file of data.files || []) {
        const book = currentBooks.get(Number(file.id));
        if (!book) continue;
        book.pages_read = file.pages_read;
        book.is_completed = file.is_completed;
        book.last_read_at = file.last_read_at;
      }
      applyContentRating(data.files || []);
      const comicinfo = data.comicinfo || {};
      meta.localized_series = data.localized_series || comicinfo.localized_series || meta.localized_series || '';
      if (summaryHtmlEnabled && String(comicinfo.summary || '').trim()) {
        meta.summary = comicinfo.summary;
      }
      meta.artist = meta.cover_artist && meta.cover_artist !== '-'
        ? meta.cover_artist
        : comicinfo.artist || '';
      meta.comicinfo_format = comicinfo.format || '';
      meta.comicinfo_count = comicinfo.count;
      meta.comicinfo_volume = comicinfo.volume;
      meta.publication_dates = data.publication_dates || {};
      canEdit = type !== 'video' && data.can_edit === true;
      editScope = data.edit_scope;
      libraryName = data.library_name || '';
      libraryId = data.library_id ?? context.libraryId ?? null;
      renderHeader();
    } catch (error) {
      if (root.isConnected) notify('추가 상세 정보를 확인하지 못했습니다. ' + error.message, true);
    }
  }

  async function loadDiscovery() {
    if (discoveryLoaded || loadingDiscovery) return;
    const section = $('[data-recommendation-section]');
    const target = $('[data-recommendations]');
    if (!books.length) {
      renderRelations({});
      renderRecommendations([]);
      return;
    }
    loadingDiscovery = true;
    section.hidden = false;
    target.setAttribute('aria-busy', 'true');
    target.replaceChildren(node('p', 'ds-empty', '추천항목을 확인하고 있습니다.'));
    try {
      const data = await request(apiUrl('discovery'));
      if (!root.isConnected) return;
      renderRelations(data.relations || {});
      renderRecommendations(data.recommendations || []);
      discoveryLoaded = true;
    } catch (error) {
      target.replaceChildren(node('p', 'ds-empty', '추천항목을 불러오지 못했습니다. ' + error.message));
      section.hidden = false;
    } finally {
      loadingDiscovery = false;
      target.removeAttribute('aria-busy');
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

  function editFields() {
    const target = $('[data-edit-fields]');
    target.replaceChildren();
    for (const [key, label] of fields) {
      const field = node('label', 'ds-field', label);
      const input = document.createElement('input');
      input.name = key;
      input.type = key === 'publication_start_date' || key === 'publication_end_date' ? 'date' : 'text';
      input.maxLength = key === 'link' ? 2000 : key === 'cover_artist' ? 500 : 4000;
      if (key === 'link') input.placeholder = '여러 주소는 쉼표(,)로 구분';
      field.append(input);
      target.append(field);
    }
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
    $('[data-action=edit]').addEventListener('click', () => { if (canEdit) editMode(true); });
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
    document.addEventListener('click', stopNavigation, true);
    document.addEventListener('click', closeMenuOnOutsideClick);
    document.addEventListener('keydown', closeMenuOnEscape);
    window.addEventListener('beforeunload', beforeUnload);
    const summaryObserver = new ResizeObserver(fitSummary);
    summaryObserver.observe($('.ds-description'));
    const observer = new MutationObserver(() => {
      if (root.isConnected) return;
      document.removeEventListener('click', stopNavigation, true);
      document.removeEventListener('click', closeMenuOnOutsideClick);
      document.removeEventListener('keydown', closeMenuOnEscape);
      window.removeEventListener('beforeunload', beforeUnload);
      observer.disconnect();
      summaryObserver.disconnect();
      clearTimeout(noticeTimer);
    });
    observer.observe(container.parentNode || document.body, { childList: true, subtree: true });

    bindBookMenu($('.ds-cover'), books[0]);
    renderHeader();
    root.dataset.ready = 'true';
    loadDiscovery();
    await loadDetailData();
    loadRating();
  } catch (error) {
    notify('상세 화면을 불러오지 못했습니다. ' + error.message, true);
  }
})();
