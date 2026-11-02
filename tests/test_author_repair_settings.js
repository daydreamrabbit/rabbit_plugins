const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');

class Element {
  constructor(tag = '') {
    this.tagName = tag;
    this.children = [];
    this.dataset = {};
    this.events = {};
    this.attributes = {};
    this.textContent = '';
    this.disabled = false;
    this.checked = false;
  }
  append(...children) { this.children.push(...children); }
  replaceChildren() { this.children = []; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(name, fn) { this.events[name] = fn; }
  matches(selector) {
    return selector === '[data-author-repair-select]' && !!this.dataset.authorRepairSelect;
  }
  querySelectorAll(selector) {
    return this.children.flatMap(child => [child, ...child.querySelectorAll('*')])
      .filter(child => selector === '*' || child.matches(selector));
  }
}

const source = fs.readFileSync(path.join(__dirname, '../settings.js'), 'utf8');
const begin = source.indexOf('(function () {\n  const list = root.querySelector(\'[data-author-repair-list]\');');
const end = source.indexOf('\n(function () {\n  const button = root.querySelector(\'.rabbit-optimize-button\');', begin);
assert.ok(begin > 0 && end > begin);
const script = source.slice(begin, end);
const flush = async () => { for (let i = 0; i < 40; i += 1) await Promise.resolve(); };

function setup(handler) {
  const selectors = ['list', 'scan', 'apply', 'select-all', 'type', 'count', 'status'];
  const elements = Object.fromEntries(selectors.map(name => [name, new Element()]));
  elements.type.value = 'general';
  const calls = [], toasts = [];
  const root = { isConnected: true,
    querySelector: selector => elements[selector.replace(/^\[data-author-repair-/, '').replace(/\]$/, '')] };
  vm.runInNewContext(script, {
    root, pluginId: 'rabbit_plugins', document: { createElement: tag => new Element(tag) }, URL,
    window: { showToast: (...args) => toasts.push(args) },
    setTimeout: callback => callback(),
    fetch: async (_url, options) => {
      const request = JSON.parse(options.body);
      calls.push(request);
      const data = await handler(request, calls);
      return { ok: data.success !== false, status: data.success === false ? 409 : 200,
        json: async () => JSON.parse(JSON.stringify(data)) };
    },
  });
  return { elements, calls, toasts };
}

async function main() {
  const items = [
    { book_id: 1, library_id: 11, series_name: '<img onerror=alert(1)>', current_author: 'WRONG ONE',
      proposed_author: '원래 작가', source: 'comicinfo', reason: '내부 글작가 확인', status: 'repairable',
      source_url: 'javascript:alert(1)' },
    { book_id: 2, library_id: 11, series_name: '두 번째 작품', current_author: 'WRONG TWO',
      proposed_author: '다른 작가', source: 'ridi', status: 'repairable', url: 'https://ridibooks.com/books/2' },
    { book_id: 3, library_id: 11, series_name: '확인 필요', current_author: 'UNKNOWN',
      proposed_author: '추정 작가', status: 'unresolved' },
  ];
  let statusCalls = 0;
  const ui = setup(request => {
    if (request.action_id === 'author_repair_status') {
      statusCalls += 1;
      return statusCalls === 1 ? { success: true, status: 'idle' }
        : { success: true, status: 'complete', checked: 8, total: 8, items };
    }
    if (request.action_id === 'author_repair_scan') return { success: true, status: 'running', checked: 0, total: 8, items: [] };
    assert.equal(request.action_id, 'author_repair_apply');
    assert.equal(request.type, 'general');
    return request.context.book_ids[0] === 1
      ? { success: true, updated_books: 6, updated_series: 1, skipped: 0 }
      : { success: false, error: '동시 변경으로 복구 제외' };
  });
  await flush();
  assert.deepEqual(ui.calls.map(call => call.action_id), ['author_repair_status'], 'opening settings must not start a scan');
  await ui.elements.scan.events.click();
  assert.equal(ui.elements.scan.disabled, false);
  assert.equal(ui.elements.list.children.length, 2, 'only actionable repair candidates are listed');
  assert.match(ui.elements.status.textContent, /8\/8개 파일 확인 완료/);
  assert.match(ui.elements.count.textContent, /검토 3건 · 복구 가능 2건/);
  assert.ok(ui.elements.list.querySelectorAll('*').some(node => node.textContent === '<img onerror=alert(1)>'), 'titles are inserted as text');
  assert.ok(!ui.elements.list.querySelectorAll('*').some(node => String(node.href).startsWith('javascript:')));
  ui.elements['select-all'].checked = true;
  ui.elements['select-all'].events.change();
  assert.equal(ui.elements.apply.disabled, false);
  assert.match(ui.elements.count.textContent, /선택 2건/);
  await ui.elements.apply.events.click();
  assert.deepEqual(ui.calls.filter(call => call.action_id === 'author_repair_apply').map(call => call.context.book_ids), [[1], [2]],
    'only repairable selected series are applied, in separate requests');
  assert.match(ui.elements.status.textContent, /1개 작품 · 파일 6개 완료.*실패 1건/);
  assert.equal(ui.elements.apply.disabled, false, 'failed items remain available for retry');
  assert.equal(ui.elements.list.children.length, 1, 'repaired series disappear from the list');
  assert.match(ui.elements.list.children[0].className, /is-repairable/);

  let finishRunning;
  let runningPolls = 0;
  const running = setup(request => {
    assert.equal(request.action_id, 'author_repair_status');
    runningPolls += 1;
    return runningPolls === 1
      ? { success: true, status: 'running', checked: 1, total: 2, items: [items[0]] }
      : new Promise(resolve => { finishRunning = () => resolve({
        success: true, status: 'complete', checked: 2, total: 2, items: [items[0]],
      }); });
  });
  await flush();
  const runningCheckbox = running.elements.list.querySelectorAll('[data-author-repair-select]')[0];
  assert.equal(runningCheckbox.disabled, false, 'a candidate can be selected during a running scan');
  runningCheckbox.checked = true;
  running.elements.list.events.change({ target: runningCheckbox });
  assert.match(running.elements.count.textContent, /선택 1건/);
  assert.equal(running.elements.apply.disabled, true, 'repair waits for scan completion');
  finishRunning();
  await flush();
  assert.equal(running.elements.apply.disabled, false);

  let polls = 0;
  const resumed = setup(request => {
    assert.equal(request.action_id, 'author_repair_status');
    polls += 1;
    return polls === 1 ? { success: true, status: 'running', checked: 1, total: 2, items: [] }
      : { success: true, status: 'interrupted', checked: 1, total: 2, items: [items[2]], warnings: ['서버 재시작'] };
  });
  await flush();
  assert.equal(resumed.calls.length, 2, 'an existing running scan is followed without starting another');
  assert.match(resumed.elements.status.textContent, /찾기가 중단되었습니다.*서버 재시작/);
  assert.equal(resumed.elements.scan.disabled, false);
  assert.equal(resumed.elements.apply.disabled, true);

  const completed = setup(() => ({ success: true, status: 'complete', checked: 3, total: 3,
    items: [
      { ...items[0], status: 'applied' },
      { ...items[1], status: 'unchanged' },
      { ...items[2], status: 'conflict' },
    ], warnings: ['일부 파일은 현재 값을 유지했습니다.'] }));
  await flush();
  assert.equal(completed.elements.list.children.length, 1);
  const completedText = completed.elements.list.querySelectorAll('*').map(node => node.textContent).join(' ');
  assert.match(completedText, /복구 근거가 확인된 글작가가 없습니다/);
  assert.match(completed.elements.count.textContent, /복구 가능 0건/);
  assert.match(completed.elements.status.textContent, /일부 파일은 현재 값을 유지했습니다/);
  assert.equal(completed.elements.apply.disabled, true);

  const warned = setup(request => request.action_id === 'author_repair_apply'
    ? { success: true, updated_books: 1, updated_series: 1, skipped: 1, warnings: ['잠긴 항목은 유지했습니다.'],
      items: [{ ...items[0], status: 'applied' }] }
    : { success: true, status: 'complete', checked: 2, total: 2, items: [items[0]] });
  await flush();
  warned.elements['select-all'].checked = true;
  warned.elements['select-all'].events.change();
  await warned.elements.apply.events.click();
  assert.match(warned.elements.status.textContent, /건너뜀 1건.*잠긴 항목은 유지했습니다/);
  assert.match(warned.elements.list.querySelectorAll('*').map(node => node.textContent).join(' '), /복구 근거가 확인된 글작가가 없습니다/);
  assert.equal(warned.elements.apply.disabled, true);
  console.log('PASS author repair settings: async progress, safe selection, sequential repair, error retry, and interrupted scan resume');
}

main().catch(error => { console.error(error); process.exitCode = 1; });
