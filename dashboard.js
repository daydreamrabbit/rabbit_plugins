(function (pluginId, shadowRoot, items) {
  const tabs = shadowRoot.querySelector('[data-role="library-tabs"]');
  const row = shadowRoot.querySelector('[data-role="book-row"]');
  const previous = shadowRoot.querySelector('[data-scroll="left"]');
  const next = shadowRoot.querySelector('[data-scroll="right"]');
  if (!tabs || !row || !previous || !next) return;

  const libraries = [];
  const librariesById = new Map();
  if (Array.isArray(items)) {
    items.forEach(item => {
      if (!item) return;
      if ((item.item_type === 'metric' || item.metric) && item.library_id != null) {
        const library = {
          library_id: item.library_id,
          name: item.metric || item.title || '라이브러리',
          books: [],
        };
        libraries.push(library);
        librariesById.set(String(item.library_id), library);
      } else if (item.library_id != null) {
        librariesById.get(String(item.library_id))?.books.push(item);
      }
    });
  }
  let activeIndex = 0;

  function updateNavigation() {
    previous.disabled = row.scrollLeft <= 1;
    next.disabled = row.scrollLeft + row.clientWidth >= row.scrollWidth - 1;
  }

  function fallbackCover(book, title) {
    const params = new URLSearchParams({
      title: title || '제목 없음',
      format: String(book.file_format || 'text'),
      seed: String(book.id || title || ''),
    });
    return `/covers/fallback?${params.toString()}`;
  }

  function coverUrl(book, title) {
    const fallback = fallbackCover(book, title);
    const cover = String(book.cover_image || book.cover || '').trim();
    if (!cover) return fallback;
    if (/^https?:\/\//i.test(cover) || cover.startsWith('/api/')) return cover;

    let path = cover.replace(/^[\\/]+/, '');
    if (path.toLowerCase().startsWith('covers/')) path = path.slice(7);
    const filename = path.split(/[\\/]/).pop();
    return path && !/[\\/]$/.test(cover) && filename && filename.includes('.')
      ? `/covers/${path}`
      : fallback;
  }

  function createCard(book, library) {
    const title = String(book.series_alias || book.series_name || book.title || '제목 없음');
    const seriesName = String(book.series_name || book.title || title);
    const libraryId = String(book.library_id == null ? library.library_id : book.library_id);
    const bookId = String(book.id || book.book_id || '');
    const card = document.createElement('article');
    card.className = 'book-card';
    card.tabIndex = 0;
    card.setAttribute('aria-label', `작품 상세 보기: ${title}`);
    card.dataset.seriesName = seriesName;
    card.dataset.libraryId = libraryId;
    card.dataset.bookId = bookId;
    card.dataset.bookTitle = String(book.title || title);
    card.dataset.displayTitle = String(book.series_alias || seriesName || title);
    card.dataset.fileFormat = String(book.file_format || '');
    card.dataset.totalPages = String(book.total_pages || 0);

    const cover = document.createElement('div');
    cover.className = 'book-card-cover';
    const overlay = document.createElement('div');
    overlay.className = 'book-card-overlay';
    const image = document.createElement('img');
    image.src = coverUrl(book, title);
    image.alt = title;
    image.loading = 'lazy';
    image.decoding = 'async';
    image.addEventListener('load', () => image.classList.add('is-loaded'), { once: true });
    image.addEventListener('error', () => {
      const fallback = fallbackCover(book, title);
      if (image.getAttribute('src') !== fallback) image.src = fallback;
      else image.removeAttribute('src');
    });

    const readButton = document.createElement('button');
    readButton.className = 'btn-resume-series';
    readButton.type = 'button';
    readButton.dataset.role = 'open-reader';
    readButton.title = '바로읽기';
    readButton.setAttribute('aria-label', `${title} 바로읽기`);
    readButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 7v14M12 7C9.8 5.4 6.7 5 3.5 5.7v13c3.2-.7 6.3-.3 8.5 1.3M12 7c2.2-1.6 5.3-2 8.5-1.3v13c-3.2-.7-6.3-.3-8.5 1.3"/></svg>';

    const info = document.createElement('div');
    info.className = 'book-card-info';
    const heading = document.createElement('h4');
    heading.className = 'book-card-title';
    heading.title = title;
    heading.textContent = title;

    cover.append(overlay, image, readButton);
    info.appendChild(heading);
    card.append(cover, info);
    return card;
  }

  function selectLibrary(index, focusTab = false) {
    activeIndex = index;
    const library = libraries[index];
    tabs.querySelectorAll('[role="tab"]').forEach((tab, tabIndex) => {
      const selected = tabIndex === index;
      tab.setAttribute('aria-selected', selected ? 'true' : 'false');
      tab.tabIndex = selected ? 0 : -1;
      if (selected && focusTab) tab.focus();
    });

    row.replaceChildren();
    row.scrollLeft = 0;
    row.setAttribute('aria-labelledby', `rabbit-library-tab-${index}`);
    const books = Array.isArray(library.books) ? library.books : [];
    if (!books.length) {
      const empty = document.createElement('p');
      empty.className = 'rabbit-recent-books__empty';
      empty.textContent = '이 라이브러리에 표시할 신규 도서가 없습니다.';
      row.appendChild(empty);
    } else {
      books.forEach(book => row.appendChild(createCard(book, library)));
    }
    updateNavigation();
  }

  if (!libraries.length) {
    const empty = document.createElement('p');
    empty.className = 'rabbit-recent-books__empty';
    empty.textContent = 'Rabbit Plugins 설정에서 표시할 라이브러리와 작품 수를 추가해 주세요.';
    row.appendChild(empty);
    previous.disabled = true;
    next.disabled = true;
    return;
  }

  libraries.forEach((library, index) => {
    const tab = document.createElement('button');
    tab.type = 'button';
    tab.className = 'rabbit-recent-books__tab';
    tab.id = `rabbit-library-tab-${index}`;
    tab.setAttribute('role', 'tab');
    tab.setAttribute('aria-controls', 'rabbit-library-books');
    tab.setAttribute('aria-selected', index === 0 ? 'true' : 'false');
    tab.tabIndex = index === 0 ? 0 : -1;
    tab.dataset.libraryIndex = String(index);

    const name = document.createElement('span');
    name.textContent = String(library.name || `라이브러리 ${index + 1}`);
    const count = document.createElement('span');
    count.className = 'rabbit-recent-books__count';
    count.textContent = String(Array.isArray(library.books) ? library.books.length : 0);
    tab.append(name, count);
    tabs.appendChild(tab);
  });

  row.id = 'rabbit-library-books';
  selectLibrary(0);
  row.addEventListener('scroll', updateNavigation, { passive: true });

  shadowRoot.addEventListener('click', event => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;

    const tab = target.closest('[data-library-index]');
    if (tab) {
      selectLibrary(Number(tab.dataset.libraryIndex));
      return;
    }

    const nav = target.closest('[data-scroll]');
    if (nav) {
      const direction = nav.dataset.scroll === 'left' ? -1 : 1;
      row.scrollBy({ left: direction * row.clientWidth * 0.7, behavior: 'smooth' });
      return;
    }

    const card = target.closest('.book-card');
    if (!card) return;

    const readButton = target.closest('[data-role="open-reader"]');
    if (readButton) {
      event.preventDefault();
      event.stopPropagation();
      if (typeof window.openReader === 'function' && card.dataset.bookId) {
        window.openReader(
          card.dataset.bookId,
          card.dataset.fileFormat,
          card.dataset.bookTitle,
          0,
          Number(card.dataset.totalPages) || 0
        );
      } else if (typeof window.openBookDetail === 'function') {
        window.openBookDetail(event, card.dataset.seriesName, card.dataset.libraryId, card.dataset.bookId, card.dataset.displayTitle);
      }
      return;
    }

    if (typeof window.openBookDetail === 'function') {
      window.openBookDetail(event, card.dataset.seriesName, card.dataset.libraryId, card.dataset.bookId, card.dataset.displayTitle);
    }
  });

  shadowRoot.addEventListener('keydown', event => {
    const target = event.target instanceof Element ? event.target : null;
    if (!target) return;

    const tab = target.closest('[data-library-index]');
    if (tab && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) {
      event.preventDefault();
      const offset = event.key === 'ArrowLeft' ? -1 : 1;
      selectLibrary((activeIndex + offset + libraries.length) % libraries.length, true);
      return;
    }

    const card = target.closest('.book-card');
    if (card && target === card && (event.key === 'Enter' || event.key === ' ')) {
      event.preventDefault();
      if (typeof window.openBookDetail === 'function') {
        window.openBookDetail(event, card.dataset.seriesName, card.dataset.libraryId, card.dataset.bookId, card.dataset.displayTitle);
      }
    }
  });
})(pluginId, shadowRoot, items);
