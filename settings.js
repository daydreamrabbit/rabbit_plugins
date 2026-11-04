(function () {
  const savedConfig = (typeof config === 'object' && config) ? config : {};
  ['metadata_auto_enabled', 'metadata_collect_cover', 'metadata_manual_overwrite', 'metadata_overwrite', 'metadata_cover_overwrite', 'support_summary_html', 'series_auto_update_enabled', 'relation_graph_enabled', 'relation_graph_mobile_enabled']
    .forEach(name => {
      const input = root.querySelector(`[name="${name}"]`);
      if (input && Object.prototype.hasOwnProperty.call(savedConfig, name)) input.checked = !!savedConfig[name];
    });
  const deferredInput = root.querySelector('[name="metadata_deferred_minutes"]');
  if (deferredInput) deferredInput.value = savedConfig.metadata_deferred_minutes ?? 0;
  const coverOverwriteInput = root.querySelector('[name="metadata_cover_overwrite"]');
  if (coverOverwriteInput && !Object.prototype.hasOwnProperty.call(savedConfig, 'metadata_cover_overwrite')) {
    coverOverwriteInput.checked = !!savedConfig.metadata_overwrite;
  }
  const sourceList = root.querySelector('#rabbit-metadata-sources');
  const sourceInput = root.querySelector('[name="metadata_sources"]');
  const fieldInput = root.querySelector('[name="metadata_fields"]');
  const fieldVersionInput = root.querySelector('[name="metadata_fields_version"]');
  const conversionGroups = [
    { key: 'metadata_genre_map', list: '#rabbit-metadata-genre-rules', add: '[data-add-conversion="genre"]', from: '들어오는 장르값', to: '저장할 장르값' },
    { key: 'metadata_publisher_map', list: '#rabbit-metadata-publisher-rules', add: '[data-add-conversion="publisher"]', from: '들어오는 출판사값', to: '저장할 출판사값' },
  ];
  const parseConversionMap = value => String(value || '').split(/[\r\n;]+/)
    .map(item => item.trim()).filter(Boolean).map(item => {
      const match = item.match(/^(.+?)\s*(?:=>|->|=)\s*(.+)$/);
      return match ? { from: match[1].trim(), to: match[2].trim() } : null;
    }).filter(Boolean);
  const syncConversionMap = group => {
    const hidden = root.querySelector(`[name="${group.key}"]`);
    const list = root.querySelector(group.list);
    if (!hidden || !list) return;
    hidden.value = [...list.querySelectorAll('[data-conversion-row]')].map(row => {
      const from = row.querySelector('[data-conversion-from]')?.value.trim() || '';
      const to = row.querySelector('[data-conversion-to]')?.value.trim() || '';
      return from && to ? `${from} => ${to}` : '';
    }).filter(Boolean).join('\n');
  };
  const renderConversionGroup = group => {
    const list = root.querySelector(group.list);
    if (!list) return;
    const rules = parseConversionMap(savedConfig[group.key]);
    const addRow = (rule = {}) => {
      const row = document.createElement('div');
      row.className = 'rabbit-conversion-row';
      row.dataset.conversionRow = group.key;
      row.innerHTML = `
        <input class="rabbit-setting-input" type="text" data-conversion-from placeholder="${group.from}">
        <span class="rabbit-conversion-arrow" aria-hidden="true">→</span>
        <input class="rabbit-setting-input" type="text" data-conversion-to placeholder="${group.to}">
        <button type="button" class="rabbit-remove-rule-button" aria-label="변환 규칙 삭제"><i class="fa-solid fa-xmark"></i></button>`;
      row.querySelector('[data-conversion-from]').value = rule.from || '';
      row.querySelector('[data-conversion-to]').value = rule.to || '';
      row.querySelectorAll('input').forEach(input => input.addEventListener('input', () => syncConversionMap(group)));
      row.querySelector('.rabbit-remove-rule-button').addEventListener('click', () => {
        row.remove();
        if (!list.querySelector('[data-conversion-row]')) addRow();
        syncConversionMap(group);
      });
      list.appendChild(row);
    };
    list.replaceChildren();
    (rules.length ? rules : [{}]).forEach(addRow);
    root.querySelector(group.add)?.addEventListener('click', () => { addRow(); syncConversionMap(group); });
    syncConversionMap(group);
  };
  conversionGroups.forEach(renderConversionGroup);
  const kindGroups = [
    { key: 'metadata_cover_kinds', list: '#rabbit-metadata-cover-kinds', setting: '.rabbit-cover-kind-setting', toggle: 'metadata_collect_cover' },
    { key: 'metadata_overwrite_kinds', list: '#rabbit-metadata-overwrite-kinds', setting: '.rabbit-overwrite-kind-setting', toggle: 'metadata_overwrite' },
    { key: 'metadata_cover_overwrite_kinds', list: '#rabbit-metadata-cover-overwrite-kinds', setting: '.rabbit-cover-overwrite-kind-setting', toggle: 'metadata_cover_overwrite' },
  ];
  const kindPattern = /^[a-z][a-z0-9_-]{0,23}$/;
  const syncChoiceState = input => {
    const label = input.closest('label');
    if (label) label.classList.toggle('is-selected', input.checked);
  };
  const configuredKinds = group => String(savedConfig[group.key] || '').split(/[,;|]/)
    .map(item => item.trim().toLowerCase()).filter(item => kindPattern.test(item));
  const hasConfiguredKinds = group => Object.prototype.hasOwnProperty.call(savedConfig, group.key);
  const syncKindGroup = group => {
    const list = root.querySelector(group.list);
    const hidden = root.querySelector(`[name="${group.key}"]`);
    if (!list || !hidden) return;
    const inputs = [...list.querySelectorAll('[data-metadata-kind-input]')];
    hidden.value = inputs.filter(input => input.checked).map(input => input.dataset.metadataKindCode).join(',');
    const all = list.querySelector('[data-metadata-kind-all]');
    if (all) {
      const checked = inputs.filter(input => input.checked).length;
      all.checked = inputs.length > 0 && checked === inputs.length;
      all.indeterminate = checked > 0 && checked < inputs.length;
      syncChoiceState(all);
    }
    inputs.forEach(syncChoiceState);
  };
  const renderKindGroup = (group, kinds) => {
    const list = root.querySelector(group.list);
    if (!list) return;
    const byCode = new Map();
    (Array.isArray(kinds) ? kinds : []).forEach(kind => {
      const code = String(kind?.code || '').trim().toLowerCase();
      if (!kindPattern.test(code)) return;
      byCode.set(code, { code, name: String(kind?.name || code).trim() || code });
    });
    if (!byCode.size) {
      const empty = document.createElement('span');
      empty.className = 'rabbit-setting-loading';
      empty.textContent = '현재 DB에 등록된 자료 유형이 없습니다.';
      list.replaceChildren(empty);
      syncKindGroup(group);
      return;
    }
    const allLabel = document.createElement('label');
    allLabel.className = 'rabbit-choice-label rabbit-choice-all';
    const allInput = document.createElement('input');
    allInput.type = 'checkbox';
    allInput.dataset.metadataKindAll = group.key;
    allInput.addEventListener('change', () => {
      list.querySelectorAll('[data-metadata-kind-input]').forEach(input => { input.checked = allInput.checked; });
      syncKindGroup(group);
    });
    const allText = document.createElement('span');
    allText.textContent = '전체';
    allLabel.append(allInput, allText);
    list.replaceChildren(allLabel);
    let lastIndex = -1;
    [...byCode.values()].forEach((kind, index) => {
      const label = document.createElement('label');
      label.className = 'rabbit-choice-label';
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.dataset.metadataKindInput = group.key;
      input.dataset.metadataKindCode = kind.code;
      input.checked = !hasConfiguredKinds(group) || configuredKinds(group).includes(kind.code);
      input.addEventListener('click', event => {
        const inputs = [...list.querySelectorAll('[data-metadata-kind-input]')];
        const currentIndex = inputs.indexOf(input);
        if (event.shiftKey && lastIndex >= 0) {
          const start = Math.min(lastIndex, currentIndex);
          const end = Math.max(lastIndex, currentIndex);
          inputs.slice(start, end + 1).forEach(item => { item.checked = input.checked; });
        }
        lastIndex = currentIndex;
        syncKindGroup(group);
      });
      const text = document.createElement('span');
      text.textContent = kind.name;
      label.append(input, text);
      list.appendChild(label);
    });
    syncKindGroup(group);
  };
  const syncKindAvailability = () => {
    kindGroups.forEach(group => {
      const toggle = root.querySelector(`[name="${group.toggle}"]`);
      const setting = root.querySelector(group.setting);
      const enabled = !toggle || toggle.checked;
      if (setting) setting.classList.toggle('is-disabled', !enabled);
      const list = root.querySelector(group.list);
      if (list) list.querySelectorAll('input').forEach(input => { input.disabled = !enabled; });
    });
  };
  const loadKinds = async () => {
    const type = encodeURIComponent(window.currentLibraryType || 'general');
    try {
      const response = await fetch(`/api/media/libraries?type=${type}&_=${Date.now()}`, {
        credentials: 'same-origin', cache: 'no-store',
      });
      const data = await response.json();
      if (!response.ok || !data.success) throw new Error(data.error || '속성 목록을 불러오지 못했습니다.');
      const kinds = Array.isArray(data.kinds) ? data.kinds.slice() : [];
      const hasUnspecified = Array.isArray(data.libraries)
        && data.libraries.some(library => String(library?.content_kind || '').trim().toLowerCase() === 'unspecified');
      if (hasUnspecified && !kinds.some(kind => String(kind?.code || '').trim().toLowerCase() === 'unspecified')) {
        kinds.push({ code: 'unspecified', name: '미지정' });
      }
      kindGroups.forEach(group => renderKindGroup(group, kinds));
      syncKindAvailability();
    } catch (error) {
      console.warn(`[${pluginId}] 라이브러리 속성 목록을 불러오지 못했습니다.`, error);
      kindGroups.forEach(group => renderKindGroup(group, []));
      syncKindAvailability();
    }
  };
  loadKinds();
  kindGroups.forEach(group => root.querySelector(`[name="${group.toggle}"]`)?.addEventListener('change', syncKindAvailability));
  syncKindAvailability();
  if (sourceList && sourceInput && fieldInput) {
    const sourceLabels = {
      series_db: '데이터베이스', ridi: '리디', naver: '네이버시리즈', naver_webtoon: '네이버웹툰', kyobo: '교보문고', yes24: '예스24', kakaopage: '카카오페이지', kakao_webtoon: '카카오웹툰', munpia: '문피아', novelpia: '노벨피아',
    };
    const sourceKeys = Object.keys(sourceLabels);
    const sourceGroups = [
      { id: 'naver', sources: ['naver', 'naver_webtoon'] },
      { id: 'kakao', sources: ['kakaopage', 'kakao_webtoon'] },
    ];
    const sourceGroupFor = key => sourceGroups.find(group => group.sources.includes(key));
    const readList = (value, fallback) => {
      const values = String(value || '').split(/[,;|]/).map(item => item.trim().toLowerCase())
        .filter(item => fallback.includes(item));
      return [...new Set(values.concat(fallback))];
    };
    const normalizeSourceGroups = values => {
      let normalized = values.slice();
      sourceGroups.forEach(group => {
        const members = group.sources.filter(key => normalized.includes(key));
        if (!members.length) return;
        const index = Math.min(...members.map(key => normalized.indexOf(key)));
        normalized = normalized.filter(key => !group.sources.includes(key));
        normalized.splice(index, 0, ...members);
      });
      return normalized;
    };
    const moveSource = (movedKey, targetKey) => {
      if (!sources.includes(movedKey) || !sources.includes(targetKey)) return false;
      if (!movedKey || movedKey === targetKey) return false;
      const movedGroup = sourceGroupFor(movedKey);
      const targetGroup = sourceGroupFor(targetKey);
      if (movedGroup && movedGroup === targetGroup) return false;
      const movedBlock = movedGroup
        ? movedGroup.sources.filter(key => sources.includes(key)) : [movedKey];
      const targetBlock = targetGroup
        ? targetGroup.sources.filter(key => sources.includes(key)) : [targetKey];
      const movedAt = Math.min(...movedBlock.map(key => sources.indexOf(key)));
      const targetAt = Math.min(...targetBlock.map(key => sources.indexOf(key)));
      const movingDown = movedAt < targetAt;
      const remaining = sources.filter(key => !movedBlock.includes(key));
      const targetIndexes = targetBlock
        .map(key => remaining.indexOf(key)).filter(index => index >= 0);
      if (!targetIndexes.length) return false;
      const insertAt = Math.min(...targetIndexes) + (movingDown ? targetBlock.length : 0);
      remaining.splice(insertAt, 0, ...movedBlock);
      sources = remaining;
      sources = normalizeSourceGroups(sources);
      return true;
    };
    const sourceUnits = () => {
      const renderedGroups = new Set();
      return sources.reduce((units, key) => {
        const group = sourceGroupFor(key);
        if (!group) {
          units.push({ id: key, sources: [key] });
        } else if (!renderedGroups.has(group.id)) {
          renderedGroups.add(group.id);
          units.push({ id: group.id, sources: group.sources.filter(source => sources.includes(source)) });
        }
        return units;
      }, []);
    };
    const configuredSources = String(savedConfig.metadata_sources || '').split(/[,;|]/)
      .map(item => item.trim().toLowerCase()).filter(item => sourceKeys.includes(item));
    const selectedSources = new Set(Object.prototype.hasOwnProperty.call(savedConfig, 'metadata_sources')
      ? configuredSources : sourceKeys);
    let sources = normalizeSourceGroups(readList(configuredSources.join(','), sourceKeys));
    if (!sources.length) sources = sourceKeys.slice();
    const configuredFields = String(savedConfig.metadata_fields || '').split(/[,;|]/)
      .map(item => item.trim()).filter(Boolean);
    const savedFieldVersion = Number(savedConfig.metadata_fields_version) || 0;
    const sync = () => {
      sourceInput.value = sources.filter(key => sourceList.querySelector(`input[data-source="${key}"]`)?.checked)
        .join(',');
      fieldInput.value = [...root.querySelectorAll('[data-metadata-field]:checked')]
        .map(input => input.dataset.metadataField).join(',');
      if (fieldVersionInput) fieldVersionInput.value = '1';
      sourceList.querySelectorAll('.rabbit-metadata-source-row').forEach(row => {
        const input = row.querySelector('[data-source]');
        row.classList.toggle('is-selected', !!input?.checked);
      });
      sourceList.querySelectorAll('.rabbit-metadata-source-group').forEach(group => {
        group.classList.toggle('is-selected', [...group.querySelectorAll('[data-source]')]
          .some(input => input.checked));
      });
      root.querySelectorAll('[data-metadata-field]').forEach(input => syncChoiceState(input));
    };
    const createSourceRow = key => {
      const row = document.createElement('div');
      row.className = 'rabbit-metadata-source-row';
      row.dataset.sourceEntry = key;
      const label = document.createElement('label');
      label.innerHTML = `<input type="checkbox" data-source="${key}"><span>${sourceLabels[key]}</span>`;
      label.querySelector('input').checked = selectedSources.has(key);
      label.querySelector('input').addEventListener('change', event => {
        if (event.target.checked) selectedSources.add(key);
        else selectedSources.delete(key);
        sync();
      });
      row.append(label);
      return row;
    };
    const render = () => {
      sourceList.replaceChildren();
      sourceUnits().forEach(unit => {
        const grouped = unit.sources.length > 1;
        const block = grouped ? document.createElement('div') : createSourceRow(unit.sources[0]);
        if (grouped) {
          block.className = `rabbit-metadata-source-group is-${unit.id}-source-group`;
          const items = document.createElement('div');
          items.className = 'rabbit-metadata-source-group-items';
          unit.sources.forEach(key => items.append(createSourceRow(key)));
          block.append(items);
        }
        block.dataset.source = unit.sources[0];
        block.dataset.sources = unit.sources.join(',');
        const grip = document.createElement('span');
        grip.className = 'rabbit-metadata-source-grip';
        grip.textContent = '⠿';
        grip.draggable = true;
        grip.addEventListener('dragstart', event => {
          block.classList.add('is-dragging');
          event.dataTransfer.setData('text/plain', unit.sources[0]);
          event.dataTransfer.effectAllowed = 'move';
        });
        grip.addEventListener('dragend', () => block.classList.remove('is-dragging'));
        block.insertBefore(grip, block.children[0] || null);
        block.addEventListener('dragover', event => event.preventDefault());
        block.addEventListener('drop', event => {
          event.preventDefault();
          const movedKey = event.dataTransfer.getData('text/plain');
          if (!moveSource(movedKey, unit.sources[0])) return;
          render();
          sync();
        });
        sourceList.append(block);
      });
      sync();
    };
    sourceInput.value = sources.join(',');
    render();
    root.querySelectorAll('[data-metadata-field]').forEach(input => {
      input.checked = !configuredFields.length || configuredFields.includes(input.dataset.metadataField)
        || (input.dataset.metadataField === 'translator' && savedFieldVersion < 1);
      input.addEventListener('change', sync);
      syncChoiceState(input);
    });
    sync();
  }
})();

(['general', 'adult']).forEach(function (libraryType) {
  const adult = libraryType === 'adult';
  const sectionKey = adult ? 'home_adult_library_sections' : 'home_library_sections';
  const list = root.querySelector(adult ? '#rabbit-home-adult-library-sections' : '#rabbit-home-library-sections');
  const addButton = root.querySelector(adult ? '#rabbit-add-home-adult-library' : '#rabbit-add-home-library');
  const saved = root.querySelector('[name="' + sectionKey + '"]');
  if (!list || !addButton || !saved) return;

  let libraries = [];
  let draggedIndex = null;
  let sections = config[sectionKey] || [];
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

  function moveHandle(index, row) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'rabbit-home-library-grip';
    button.textContent = '⠿';
    button.title = '끌어서 순서 이동';
    button.setAttribute('aria-label', `${libraryName(index)} 이동`);
    button.draggable = true;
    button.addEventListener('dragstart', event => {
      draggedIndex = index;
      row.classList.add('is-dragging');
      event.dataTransfer.effectAllowed = 'move';
      event.dataTransfer.setData('text/plain', String(index));
    });
    button.addEventListener('dragend', () => {
      draggedIndex = null;
      list.querySelectorAll('.is-dragging, .is-drop-target').forEach(item => {
        item.classList.remove('is-dragging', 'is-drop-target');
      });
    });
    return button;
  }

  function libraryName(index) {
    return libraries.find(library => String(library.id) === sections[index]?.library_id)?.name || '라이브러리';
  }

  function render() {
    list.replaceChildren();
    if (!libraries.length) {
      const empty = document.createElement('span');
      empty.className = 'rabbit-home-library-empty';
      empty.textContent = `사용 가능한 ${adult ? '성인' : '일반'} 도서 라이브러리가 없습니다.`;
      list.appendChild(empty);
      addButton.disabled = true;
      syncSavedValue();
      return;
    }

    sections.forEach((section, index) => {
      const row = document.createElement('div');
      row.className = 'rabbit-home-library-row';
      row.addEventListener('dragover', event => {
        if (draggedIndex === null || draggedIndex === index) return;
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
        row.classList.add('is-drop-target');
      });
      row.addEventListener('dragleave', event => {
        if (!row.contains(event.relatedTarget)) row.classList.remove('is-drop-target');
      });
      row.addEventListener('drop', event => {
        event.preventDefault();
        if (draggedIndex === null || draggedIndex === index) return;
        const [section] = sections.splice(draggedIndex, 1);
        sections.splice(index, 0, section);
        draggedIndex = null;
        render();
      });

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

      row.append(
        librarySelect,
        countSelect,
        moveHandle(index, row),
        removeButton
      );
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

  fetch('/api/media/libraries?type=' + libraryType + '&_=' + Date.now(), { cache: 'no-store' })
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
});

(function () {
  const list = root.querySelector('[data-hold-list]');
  if (!list) return;

  const refreshButton = root.querySelector('[data-hold-refresh]');
  const selectAll = root.querySelector('[data-hold-select-all]');
  const applySelectedButton = root.querySelector('[data-hold-apply-selected]');
  const countNode = root.querySelector('[data-hold-count]');
  const statusNode = root.querySelector('[data-hold-status]');
  const providerLabels = {
    series_db: 'Series.db', ridi: '리디', naver: '네이버시리즈',
    naver_webtoon: '네이버웹툰', kyobo: '교보문고', yes24: '예스24',
    kakaopage: '카카오페이지', kakao_webtoon: '카카오웹툰',
    munpia: '문피아', novelpia: '노벨피아',
  };
  let holds = [];
  let busy = false;
  let loadError = '';

  const setStatus = (message, kind = '') => {
    if (!statusNode) return;
    statusNode.textContent = message;
    statusNode.className = `rabbit-metadata-hold-status${kind ? ` is-${kind}` : ''}`;
  };
  const notify = (message, kind = 'success') => {
    if (typeof window.showToast === 'function') window.showToast(message, kind);
  };
  const postAction = async (actionId, type = 'general', context = {}) => {
    const response = await fetch('/api/media/context-menu/book/plugins/action', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type, plugin_id: pluginId, action_id: actionId, context }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.success) throw new Error(data.error || `요청에 실패했습니다. (HTTP ${response.status})`);
    return data;
  };
  const makeText = (tag, className, text) => {
    const element = document.createElement(tag);
    if (className) element.className = className;
    element.textContent = String(text || '');
    return element;
  };
  const selectedHolds = () => {
    const selected = new Set([...list.querySelectorAll('[data-hold-select]:checked')]
      .map(input => input.dataset.holdSelect));
    return holds.filter(hold => selected.has(`${hold.db_type}:${hold.book_id}`));
  };
  const syncSelection = () => {
    const selected = selectedHolds().length;
    if (countNode) countNode.textContent = `보류 ${holds.length}건 · 선택 ${selected}건`;
    if (applySelectedButton) applySelectedButton.disabled = busy || selected === 0;
    if (selectAll) {
      selectAll.disabled = busy || holds.length === 0;
      selectAll.checked = holds.length > 0 && selected === holds.length;
      selectAll.indeterminate = selected > 0 && selected < holds.length;
    }
    if (refreshButton) refreshButton.disabled = busy;
  };
  const safeExternalUrl = value => {
    try {
      const url = new URL(String(value || ''), window.location.href);
      return ['http:', 'https:'].includes(url.protocol) ? url.href : '';
    } catch (_) {
      return '';
    }
  };
  const renderHolds = () => {
    list.replaceChildren();
    if (!holds.length) {
      list.append(makeText('p', 'rabbit-metadata-hold-empty', loadError
        ? '보류 목록을 불러오지 못했습니다. 새로고침을 눌러 다시 시도해 주세요.'
        : '현재 자동 적용이 보류된 작품이 없습니다.'));
      syncSelection();
      return;
    }
    holds.forEach((hold, index) => {
      const identity = `${hold.db_type}:${hold.book_id}`;
      const card = document.createElement('article');
      card.className = 'rabbit-metadata-hold-card';
      card.dataset.holdIdentity = identity;

      const head = document.createElement('div');
      head.className = 'rabbit-metadata-hold-card-head';
      const selectLabel = document.createElement('label');
      selectLabel.className = 'rabbit-choice-label';
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.dataset.holdSelect = identity;
      checkbox.setAttribute('aria-label', `${hold.series_name} 선택`);
      selectLabel.append(checkbox);
      const titleWrap = document.createElement('div');
      titleWrap.className = 'rabbit-metadata-hold-title-wrap';
      titleWrap.append(makeText('strong', 'rabbit-metadata-hold-title', hold.series_name));
      const dbLabel = hold.db_type === 'adult' ? '성인 서재' : '일반 서재';
      const libraryLabel = hold.library_name || `라이브러리 ${hold.library_id}`;
      const checkedAt = hold.checked_at ? new Date(hold.checked_at).toLocaleString('ko-KR') : '';
      titleWrap.append(makeText('span', 'rabbit-metadata-hold-subtitle',
        `${dbLabel} · ${libraryLabel}${hold.book_count ? ` · 파일 ${hold.book_count}개` : ''}${checkedAt ? ` · 보류 ${checkedAt}` : ''}`));
      if (hold.auto_status) titleWrap.append(makeText('span', 'rabbit-metadata-hold-subtitle', hold.auto_status));
      if (hold.auto_last_message) titleWrap.append(makeText('span', 'rabbit-metadata-hold-subtitle', `최근 처리: ${hold.auto_last_message}`));
      head.append(selectLabel, titleWrap);
      card.append(head);

      const candidates = Array.isArray(hold.candidates) ? hold.candidates : [];
      const details = document.createElement('details');
      details.className = 'rabbit-metadata-hold-candidates';
      const summary = makeText('summary', '', `후보 확인 (${candidates.length}개 제공처)`);
      details.append(summary);
      const candidateList = document.createElement('div');
      candidateList.className = 'rabbit-metadata-hold-candidate-list';
      candidates.forEach(candidate => {
        const item = document.createElement('div');
        item.className = 'rabbit-metadata-hold-candidate';
        item.append(makeText('span', 'rabbit-metadata-hold-source', providerLabels[candidate.source] || candidate.source));
        item.append(makeText('span', 'rabbit-metadata-hold-candidate-title', candidate.title));
        item.append(makeText('span', 'rabbit-metadata-hold-candidate-author', `작가: ${candidate.author}`));
        const href = safeExternalUrl(candidate.url);
        if (href) {
          const link = document.createElement('a');
          link.href = href;
          link.target = '_blank';
          link.rel = 'noopener noreferrer';
          link.textContent = '제공처에서 열기';
          item.append(link);
        }
        candidateList.append(item);
      });
      if (!candidates.length) candidateList.append(makeText('p', 'rabbit-setting-loading', '저장된 후보가 없습니다.'));
      details.append(candidateList);
      card.append(details);

      const actions = document.createElement('div');
      actions.className = 'rabbit-metadata-hold-card-actions';
      const applyButton = document.createElement('button');
      applyButton.type = 'button';
      applyButton.className = 'rabbit-hold-action-button';
      applyButton.dataset.holdApply = String(index);
      applyButton.textContent = '동일 작품 적용';
      applyButton.disabled = busy;
      actions.append(applyButton);
      card.append(actions);
      list.append(card);
    });
    syncSelection();
  };
  const refreshHolds = async ({ preserveStatus = false } = {}) => {
    if (busy) return;
    if (!preserveStatus) setStatus('보류 목록을 불러오는 중입니다.');
    if (refreshButton) refreshButton.disabled = true;
    try {
      const data = await postAction('metadata_list_holds');
      holds = Array.isArray(data.holds) ? data.holds : [];
      loadError = '';
      renderHolds();
      if (!preserveStatus) {
        const warning = Array.isArray(data.warnings) && data.warnings.length ? ` ${data.warnings.join(' ')}` : '';
        setStatus(holds.length ? `보류 작품 ${holds.length}건을 불러왔습니다.${warning}` : `보류 작품이 없습니다.${warning}`,
          warning ? 'error' : '');
      }
      return true;
    } catch (error) {
      holds = [];
      loadError = error.message || '보류 목록을 불러오지 못했습니다.';
      renderHolds();
      setStatus(loadError, 'error');
      return false;
    } finally {
      syncSelection();
    }
  };
  const applyHolds = async (items, oneButton = null) => {
    if (busy || !items.length) return;
    busy = true;
    if (oneButton) {
      oneButton.disabled = true;
      oneButton.textContent = '검증 후 적용 중...';
    }
    renderHolds();
    let applied = 0;
    const failures = [];
    for (let index = 0; index < items.length; index += 1) {
      const hold = items[index];
      setStatus(`${index + 1}/${items.length} 처리 중: ${hold.series_name}`);
      try {
        await postAction('metadata_confirm_same_work', hold.db_type, { book_id: hold.book_id });
        applied += 1;
      } catch (error) {
        failures.push(`${hold.series_name}: ${error.message || '적용 실패'}`);
      }
    }
    busy = false;
    const refreshed = await refreshHolds({ preserveStatus: true });
    let message = failures.length
      ? `${applied}/${items.length}건 적용 완료, ${failures.length}건 보류 유지. ${failures.slice(0, 3).join(' · ')}`
      : `${applied}건을 동일 작품으로 적용했습니다.`;
    if (!refreshed) message += ' 적용 후 보류 목록을 새로고침하지 못했습니다.';
    setStatus(message, failures.length ? (applied ? '' : 'error') : (refreshed ? 'success' : 'error'));
    if (applied) notify(`${applied}건의 보류 작품 메타데이터를 적용했습니다.`, 'success');
    if (failures.length) notify(`${failures.length}건은 적용되지 않았습니다. 보류 목록을 확인해 주세요.`, 'error');
    syncSelection();
  };

  refreshButton?.addEventListener('click', () => refreshHolds());
  selectAll?.addEventListener('change', () => {
    list.querySelectorAll('[data-hold-select]').forEach(input => { input.checked = selectAll.checked; });
    syncSelection();
  });
  list.addEventListener('change', event => {
    if (event.target.matches('[data-hold-select]')) syncSelection();
  });
  list.addEventListener('click', event => {
    const button = event.target.closest('[data-hold-apply]');
    if (!button || busy) return;
    const hold = holds[Number(button.dataset.holdApply)];
    if (hold) applyHolds([hold], button);
  });
  applySelectedButton?.addEventListener('click', () => applyHolds(selectedHolds()));
  refreshHolds();
})();

(function () {
  const list = root.querySelector('[data-author-repair-list]');
  if (!list) return;
  const scanButton = root.querySelector('[data-author-repair-scan]');
  const applyButton = root.querySelector('[data-author-repair-apply]');
  const selectAll = root.querySelector('[data-author-repair-select-all]');
  const typeSelect = root.querySelector('[data-author-repair-type]');
  const countNode = root.querySelector('[data-author-repair-count]');
  const statusNode = root.querySelector('[data-author-repair-status]');
  const providerLabels = { ridi: '리디', naver: '네이버시리즈', yes24: '예스24',
    kyobo: '교보문고', series_db: 'Series.db', comicinfo: 'ComicInfo', embedded: '내부 메타데이터' };
  let items = [];
  let selected = new Set();
  let busy = false;
  let scanning = false;
  let scanned = false;
  const identity = item => `${item.db_type || typeSelect.value}:${item.book_id}`;
  const repairable = item => item.status === 'repairable' && !!String(item.proposed_author || '').trim();
  const makeText = (tag, className, text) => {
    const element = document.createElement(tag);
    element.className = className;
    element.textContent = String(text || '');
    return element;
  };
  const setStatus = (message, kind = '') => {
    statusNode.textContent = message;
    statusNode.className = `rabbit-author-repair-status${kind ? ` is-${kind}` : ''}`;
  };
  const postAction = async (actionId, type, context) => {
    const response = await fetch('/api/media/context-menu/book/plugins/action', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type, plugin_id: pluginId, action_id: actionId, context }),
    });
    const data = await response.json().catch(() => null);
    if (!response.ok || !data || data.success !== true) {
      throw new Error(data?.error || `요청에 실패했습니다. (HTTP ${response.status})`);
    }
    return data;
  };
  const syncControls = () => {
    const candidates = items.filter(repairable);
    const count = candidates.filter(item => selected.has(identity(item))).length;
    const selectionBusy = busy && !scanning;
    scanButton.disabled = busy;
    typeSelect.disabled = busy;
    applyButton.disabled = busy || count === 0;
    selectAll.disabled = selectionBusy || candidates.length === 0;
    selectAll.checked = candidates.length > 0 && count === candidates.length;
    selectAll.indeterminate = count > 0 && count < candidates.length;
    countNode.textContent = scanned
      ? `검토 ${items.length}건 · 복구 가능 ${candidates.length}건 · 선택 ${count}건`
      : '복구 대상을 찾아주세요.';
    const byIdentity = new Map(items.map(item => [identity(item), item]));
    list.querySelectorAll('[data-author-repair-select]').forEach(input => {
      const item = byIdentity.get(input.dataset.authorRepairSelect);
      input.disabled = selectionBusy || !item || !repairable(item);
      input.checked = selected.has(input.dataset.authorRepairSelect);
    });
    list.setAttribute('aria-busy', String(busy));
  };
  const render = () => {
    list.replaceChildren();
    const candidates = items.filter(repairable);
    if (!candidates.length) {
      list.append(makeText('p', 'rabbit-metadata-hold-empty', scanned
        ? '복구 근거가 확인된 글작가가 없습니다. 나머지 항목은 변경하지 않았습니다.'
        : '찾기를 누르면 현재 글작가와 복구할 글작가를 비교할 수 있습니다.'));
    }
    candidates.forEach(item => {
      const completed = ['applied', 'repaired'].includes(item.status);
      const state = completed ? 'repaired' : repairable(item) ? 'repairable' : 'unresolved';
      const card = makeText('article', `rabbit-author-repair-card is-${state}`, '');
      const head = makeText('div', 'rabbit-metadata-hold-card-head', '');
      const label = makeText('label', 'rabbit-choice-label', '');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.dataset.authorRepairSelect = identity(item);
      checkbox.setAttribute('aria-label', `${item.series_name} 글작가 복구 선택`);
      label.append(checkbox);
      const title = makeText('div', 'rabbit-metadata-hold-title-wrap', '');
      title.append(makeText('strong', 'rabbit-metadata-hold-title', item.series_name));
      title.append(makeText('span', 'rabbit-metadata-hold-subtitle',
        `${item.library_name || `라이브러리 ${item.library_id}`}${item.book_count ? ` · 파일 ${item.book_count}개` : ''}`));
      head.append(label, title);
      const comparison = makeText('div', 'rabbit-author-repair-comparison', '');
      [[completed ? '복구 전 글작가' : '현재 글작가', item.current_author || '미지정', ''],
        [completed ? '복구한 글작가' : '복구할 글작가', item.proposed_author || '확인 필요', ' rabbit-author-repair-proposed']]
        .forEach(([caption, author, extra], index) => {
          if (index) comparison.append(makeText('span', 'rabbit-conversion-arrow', '→'));
          const credit = makeText('div', `rabbit-author-repair-credit${extra}`, '');
          credit.append(makeText('span', '', caption), makeText('strong', '', author));
          comparison.append(credit);
        });
      const evidence = makeText('div', 'rabbit-author-repair-evidence', '');
      evidence.append(makeText('span', '', `근거: ${providerLabels[item.source] || item.source || '확인 중'}`));
      if (item.reason) evidence.append(makeText('span', '', item.reason));
      try {
        const url = new URL(item.source_url || item.url || '');
        if (['https:', 'http:'].includes(url.protocol)) {
          const link = makeText('a', '', '근거 정보 열기');
          link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer';
          evidence.append(link);
        }
      } catch (_) { /* An evidence link is optional. */ }
      card.append(head, comparison, evidence);
      const outcome = {
        applied: '글작가 복구 완료', repaired: '글작가 복구 완료', unchanged: '변경할 글작가 없음',
        conflict: '현재 값이 달라져 복구에서 제외', read_failed: '내부 메타데이터를 읽지 못해 복구에서 제외',
      }[item.status] || '근거 확인 필요 · 자동 복구에서 제외';
      if (item.message || !repairable(item)) card.append(makeText('span', 'rabbit-author-repair-outcome',
        item.message || outcome));
      list.append(card);
    });
    syncControls();
  };
  const receiveScan = data => {
    items = Array.isArray(data.items) ? data.items : [];
    scanning = data.status === 'running';
    selected = new Set([...selected].filter(key => items.some(item => repairable(item) && identity(item) === key)));
    scanned = data.status !== 'idle';
    const checked = Number(data.checked || 0);
    const total = Number(data.total || 0);
    const warnings = Array.isArray(data.warnings) ? data.warnings.join(' ') : '';
    const progress = total ? `${checked}/${total}` : String(checked);
    setStatus(`${progress}개 파일 확인${data.status === 'running' ? ' 중' : ' 완료'} · ${items.filter(repairable).length}개 작품 복구 가능.`
      + `${data.status === 'running' ? ' 선택은 가능하며, 복구는 찾기가 끝나면 실행할 수 있습니다.' : ''}`
      + `${data.status === 'interrupted' ? ' 찾기가 중단되었습니다. 다시 찾기를 눌러 확인해 주세요.' : ''}${warnings ? ` ${warnings}` : ''}`,
      warnings || data.status === 'interrupted' ? 'error' : '');
    render();
  };
  const followScan = async data => {
    receiveScan(data);
    while (data.status === 'running' && root.isConnected !== false) {
      await new Promise(resolve => setTimeout(resolve, 1200));
      if (root.isConnected === false) break;
      data = await postAction('author_repair_status', typeSelect.value, {});
      receiveScan(data);
    }
  };
  const scan = async () => {
    if (busy) return;
    busy = true;
    setStatus('저장된 정보에서 잘못 변경된 글작가를 찾고 있습니다.');
    syncControls();
    try {
      const data = await postAction('author_repair_scan', typeSelect.value, {});
      selected = new Set();
      await followScan(data);
    } catch (error) {
      setStatus(error.message || '복구 대상을 찾지 못했습니다.', 'error');
    } finally {
      busy = false;
      render();
    }
  };
  const apply = async () => {
    if (busy) return;
    const targets = items.filter(item => repairable(item) && selected.has(identity(item)));
    if (!targets.length) return;
    busy = true;
    syncControls();
    let updatedBooks = 0, updatedSeries = 0, skipped = 0;
    const failures = [];
    const warnings = [];
    for (let index = 0; index < targets.length; index += 1) {
      const item = targets[index];
      setStatus(`${index + 1}/${targets.length} 글작가 복구 중: ${item.series_name}`);
      try {
        const data = await postAction('author_repair_apply', item.db_type || typeSelect.value,
          { book_ids: [item.book_id] });
        const books = Number(data.updated_books || 0);
        updatedBooks += books;
        updatedSeries += Number(data.updated_series || (books ? 1 : 0));
        skipped += Number(data.skipped || 0);
        const itemWarnings = Array.isArray(data.warnings) ? data.warnings.filter(Boolean) : [];
        warnings.push(...itemWarnings);
        const originalIdentity = identity(item);
        const refreshed = Array.isArray(data.items) && data.items.find(value =>
          String(value.library_id) === String(item.library_id) && value.series_name === item.series_name
          && (value.db_type || typeSelect.value) === (item.db_type || typeSelect.value));
        if (refreshed) Object.assign(item, refreshed);
        else item.status = books ? 'applied' : 'conflict';
        item.message = (books ? item.status === 'repairable'
          ? `파일 ${books}개 복구 완료 · 남은 파일 복구 가능`
          : `글작가 복구 완료 · 파일 ${books}개`
          : item.status === 'applied' ? '이미 복구된 글작가 정보를 확인했습니다.'
            : '현재 값이 달라졌거나 복구 근거를 확인할 수 없어 건너뛰었습니다.')
          + (itemWarnings.length ? ` · ${itemWarnings.join(' ')}` : '');
        selected.delete(originalIdentity);
      } catch (error) {
        item.message = error.message || '복구 실패';
        failures.push(`${item.series_name}: ${item.message}`);
      }
      render();
    }
    busy = false;
    render();
    const message = `글작가 복구 ${updatedSeries}개 작품 · 파일 ${updatedBooks}개 완료`
      + `${skipped ? ` · 건너뜀 ${skipped}건` : ''}${failures.length ? ` · 실패 ${failures.length}건. ${failures.slice(0, 3).join(' · ')}` : ''}`
      + `${warnings.length ? ` · ${[...new Set(warnings)].slice(0, 3).join(' · ')}` : ''}`;
    const resultKind = failures.length || warnings.length ? 'error' : 'success';
    setStatus(message, resultKind);
    if (typeof window.showToast === 'function') window.showToast(message, resultKind);
  };
  scanButton.addEventListener('click', scan);
  applyButton.addEventListener('click', apply);
  selectAll.addEventListener('change', () => {
    selected = new Set(selectAll.checked ? items.filter(repairable).map(identity) : []);
    syncControls();
  });
  list.addEventListener('change', event => {
    const input = event.target;
    if (!input.matches('[data-author-repair-select]') || input.disabled) return;
    if (input.checked) selected.add(input.dataset.authorRepairSelect);
    else selected.delete(input.dataset.authorRepairSelect);
    syncControls();
  });
  typeSelect.addEventListener('change', () => {
    items = []; selected = new Set(); scanned = false;
    setStatus(''); render(); loadSavedStatus();
  });
  const loadSavedStatus = async () => {
    if (busy) return;
    busy = true;
    syncControls();
    try {
      const data = await postAction('author_repair_status', typeSelect.value, {});
      if (data.status && data.status !== 'idle') await followScan(data);
    } catch (error) {
      setStatus(error.message || '이전 복구 대상 목록을 불러오지 못했습니다.', 'error');
    } finally {
      busy = false;
      render();
    }
  };
  syncControls();
  loadSavedStatus();
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
      const body = await response.text();
      let data;
      try {
        data = JSON.parse(body);
      } catch (_) {
        const contentType = response.headers.get('content-type') || '알 수 없는 형식';
        const redirected = response.redirected ? `, 최종 주소: ${response.url}` : '';
        if ([502, 503, 504].includes(response.status)) {
          throw new Error(`HTTP ${response.status}: 서버나 프록시가 JSON 대신 ${contentType} 응답을 반환했습니다${redirected}. 큰 DB 처리 중 응답 시간이 초과됐을 수 있습니다. 작업이 계속 실행 중일 수 있으니 서버 상태를 확인한 뒤 다시 실행해 주세요.`);
        }
        throw new Error(`최적화 API가 JSON 대신 ${contentType} 응답을 반환했습니다 (HTTP ${response.status}${redirected}). 네트워크 탭의 응답 내용을 확인해 주세요.`);
      }
      if (!response.ok || !data.success) {
        const target = data.database_path ? ` 대상 경로: ${data.database_path}` : '';
        const backup = data.backup_path ? ` 백업: ${data.backup_path}` : '';
        throw new Error((data.error || 'Series.db 최적화에 실패했습니다.') + target + backup);
      }
      status.textContent = data.message || 'Series.db 최적화를 완료했습니다.';
      if (data.database_path) status.textContent += ` 대상: ${data.database_path}`;
      if (data.backup_path) status.textContent += ` 자동 백업: ${data.backup_path}`;
    } catch (error) {
      status.textContent = error.message || 'Series.db 최적화에 실패했습니다.';
    } finally {
      button.disabled = false;
      button.textContent = 'Series.db 최적화';
    }
  });
})();

(function () {
  const form = root.closest('form.plugin-config-form');
  if (!form) return;

  form.addEventListener('submit', async event => {
    event.preventDefault();
    event.stopImmediatePropagation();

    const button = form.querySelector('button[type="submit"]');
    const status = root.querySelector('.rabbit-save-status');
    const configData = {};
    form.querySelectorAll('input, select, textarea').forEach(input => {
      if (!input.name) return;
      configData[input.name] = input.type === 'checkbox'
        ? !!input.checked
        : String(input.value ?? '').trim();
    });

    try {
      if (button) {
        button.disabled = true;
        button.textContent = '저장 중...';
      }
      if (status) {
        status.textContent = '설정을 저장하는 중입니다.';
        status.className = 'rabbit-save-status is-saving';
      }
      const response = await fetch('/api/media/metadata/plugins/save-config', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ type: 'general', plugin_id: pluginId, config: configData }),
      });
      const result = await response.json();
      if (!response.ok || !result.success) throw new Error(result.error || '설정 저장에 실패했습니다.');
      if (status) {
        status.textContent = result.message || '설정이 저장되었습니다.';
        status.className = 'rabbit-save-status is-success';
      }
      if (typeof window.showToast === 'function') {
        window.showToast(result.message || 'Rabbit Plugins 설정을 저장했습니다.', 'success');
      } else {
        alert(result.message || 'Rabbit Plugins 설정을 저장했습니다.');
      }
    } catch (error) {
      const message = error.message || 'Rabbit Plugins 설정을 저장하지 못했습니다.';
      if (status) {
        status.textContent = message;
        status.className = 'rabbit-save-status is-error';
      }
      if (typeof window.showToast === 'function') window.showToast(message, 'error');
      else alert(message);
    } finally {
      if (button) {
        button.disabled = false;
        button.innerHTML = '<i class="fa-regular fa-floppy-disk"></i> 설정 저장';
      }
    }
  }, true);
})();

(function () {
  const statusNode = root.querySelector('[data-series-refresh-status]');
  if (!statusNode) return;
  const pathInput = root.querySelector('[name="series_db_path"]');
  let lastPath = (typeof config === 'object' && config?.series_db_path) || '';
  const poll = async () => {
    while (root.isConnected !== false) {
      try {
        const response = await fetch('/api/media/context-menu/book/plugins/action', {
          method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ type: 'general', plugin_id: pluginId, action_id: 'series_refresh_status', context: {} }),
        });
        const data = await response.json();
        if (!response.ok || data.success !== true) throw new Error(data.error || '갱신 상태를 불러오지 못했습니다.');
        const state = data.state || {};
        const checked = state.checked_at || state.updated_at;
        const checkedText = checked ? ' · 마지막 확인: ' + new Date(checked * 1000).toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) : '';
        statusNode.textContent = (data.enabled ? data.schedule : '자동 갱신 꺼짐')
          + ' · ' + (state.message || '첫 확인을 기다리고 있습니다.') + checkedText;
        if (pathInput && pathInput.value === lastPath && document.activeElement !== pathInput) pathInput.value = data.active_path;
        lastPath = data.active_path;
      } catch (error) {
        statusNode.textContent = error.message || '갱신 상태를 불러오지 못했습니다.';
      }
      await new Promise(resolve => setTimeout(resolve, 5000));
    }
  };
  poll();
})();
