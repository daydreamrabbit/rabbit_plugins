(function () {
  const list = root.querySelector('#rabbit-home-library-sections');
  const addButton = root.querySelector('#rabbit-add-home-library');
  const saved = root.querySelector('[name="home_library_sections"]');
  if (!list || !addButton || !saved) return;

  let libraries = [];
  let sections = config.home_library_sections || [];
  if (typeof sections === 'string') {
    try { sections = JSON.parse(sections); } catch (e) { sections = []; }
  }
  if (!Array.isArray(sections)) sections = [];
  sections = sections.map(section => ({
    library_id: String(section && section.library_id || ''),
    limit: Math.min(20, Math.max(1, Number.parseInt(section && section.limit, 10) || 5)),
  })).filter(section => section.library_id);
  syncSavedValue();

  function syncSavedValue() {
    saved.value = JSON.stringify(sections.map(section => ({
      library_id: Number(section.library_id),
      limit: section.limit,
    })));
  }

  function render() {
    list.replaceChildren();
    if (!libraries.length) {
      const empty = document.createElement('span');
      empty.className = 'rabbit-home-library-empty';
      empty.textContent = '사용 가능한 일반 도서 라이브러리가 없습니다.';
      list.appendChild(empty);
      addButton.disabled = true;
      syncSavedValue();
      return;
    }

    sections.forEach((section, index) => {
      const row = document.createElement('div');
      row.className = 'rabbit-home-library-row';

      const librarySelect = document.createElement('select');
      librarySelect.setAttribute('aria-label', '라이브러리 선택');
      libraries.filter(library => !sections.some((other, otherIndex) =>
        otherIndex !== index && other.library_id === String(library.id)
      )).forEach(library => {
        const option = document.createElement('option');
        option.value = String(library.id);
        option.textContent = library.name;
        librarySelect.appendChild(option);
      });
      librarySelect.value = section.library_id;
      librarySelect.addEventListener('change', () => {
        section.library_id = librarySelect.value;
        render();
      });

      const countSelect = document.createElement('select');
      countSelect.setAttribute('aria-label', '표시할 작품 수');
      for (let count = 1; count <= 20; count += 1) {
        const option = document.createElement('option');
        option.value = String(count);
        option.textContent = `${count}개 표시`;
        countSelect.appendChild(option);
      }
      countSelect.value = String(section.limit);
      countSelect.addEventListener('change', () => {
        section.limit = Number(countSelect.value);
        syncSavedValue();
      });

      const removeButton = document.createElement('button');
      removeButton.type = 'button';
      removeButton.className = 'rabbit-home-library-remove';
      removeButton.textContent = '삭제';
      removeButton.addEventListener('click', () => {
        sections.splice(index, 1);
        render();
      });

      row.append(librarySelect, countSelect, removeButton);
      list.appendChild(row);
    });

    const used = new Set(sections.map(section => section.library_id));
    addButton.disabled = libraries.every(library => used.has(String(library.id)));
    syncSavedValue();
  }

  addButton.addEventListener('click', () => {
    const library = libraries.find(item => !sections.some(section => section.library_id === String(item.id)));
    if (!library) return;
    sections.push({ library_id: String(library.id), limit: 5 });
    render();
  });

  fetch('/api/media/libraries?type=general&_=' + Date.now(), { cache: 'no-store' })
    .then(response => response.json())
    .then(data => {
      if (!data.success || !Array.isArray(data.libraries)) throw new Error('라이브러리 응답이 올바르지 않습니다.');
      libraries = data.libraries.filter(library => library && library.id != null);
      const available = new Set(libraries.map(library => String(library.id)));
      sections = sections.filter((section, index) => available.has(section.library_id) &&
        sections.findIndex(other => other.library_id === section.library_id) === index
      );
      render();
    })
    .catch(error => {
      console.error(`[${pluginId}] 라이브러리 설정을 불러오지 못했습니다.`, error);
      list.textContent = '라이브러리 목록을 불러오지 못했습니다.';
      addButton.disabled = true;
    });
})();

(function () {
  const button = root.querySelector('.rabbit-optimize-button');
  const status = root.querySelector('.rabbit-optimize-status');
  if (!button || !status) return;

  button.addEventListener('click', async () => {
    if (button.disabled) return;
    button.disabled = true;
    button.textContent = '최적화 중...';
    status.textContent = 'Series.db 구조를 확인하고 있습니다.';

    try {
      const response = await fetch('/api/media/context-menu/book/plugins/action', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          type: 'general',
          plugin_id: pluginId,
          action_id: 'optimize_series_db',
          context: {},
        }),
      });
      const data = await response.json();
      if (!response.ok || !data.success) throw new Error(data.error || 'Series.db 최적화에 실패했습니다.');
      status.textContent = data.message || 'Series.db 최적화를 완료했습니다.';
      if (data.backup_path) status.textContent += ` 자동 백업: ${data.backup_path}`;
    } catch (error) {
      status.textContent = error.message || 'Series.db 최적화에 실패했습니다.';
    } finally {
      button.disabled = false;
      button.textContent = 'Series.db 최적화';
    }
  });
})();
