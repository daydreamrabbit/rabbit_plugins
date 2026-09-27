const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const path = require('path');

class Element {
  constructor() {
    this.children = [];
    this.events = {};
    this.attributes = {};
    this.dataset = {};
    this.scrollLeft = 0;
    this.clientWidth = 200;
    this.scrollWidth = 400;
    this.isConnected = true;
    this.classList = { add() {}, remove() {} };
  }
  addEventListener(name, callback) { this.events[name] = callback; }
  setAttribute(name, value) { this.attributes[name] = value; }
  getAttribute(name) { return this.attributes[name]; }
  append(...children) { this.children.push(...children); }
  appendChild(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = [...children]; }
  querySelectorAll(selector) {
    return selector === '[role="tab"]'
      ? this.children.filter(child => child.getAttribute('role') === 'tab') : [];
  }
  closest(selector) {
    if (selector === '[data-library-index]' && this.dataset.libraryIndex != null) return this;
    return null;
  }
  focus() {}
}

const tabs = new Element();
const row = new Element();
const previous = new Element();
const next = new Element();
const coreRow = new Element();
coreRow.__dashboardReady = true;
coreRow.__dashboardSignature = 'general:old';
const card = new Element();
card.dataset.limit = '20';
card.parentElement = new Element();
const host = new Element();
host.closest = selector => selector === '[data-widget-kind="plugin"]' ? card : null;
const shadowRoot = {
  host,
  events: {},
  querySelector(selector) {
    return {
      '[data-role="library-tabs"]': tabs,
      '[data-role="book-row"]': row,
      '[data-scroll="left"]': previous,
      '[data-scroll="right"]': next,
    }[selector];
  },
  addEventListener(name, callback) { this.events[name] = callback; },
};
const observers = [];
class MutationObserver {
  constructor(callback) { this.callback = callback; observers.push(this); }
  observe(target) { this.target = target; }
  disconnect() { this.disconnected = true; }
}
const oldItems = [
  { item_type: 'metric', library_id: 1, metric: '첫째' },
  { id: 1, library_id: 1, series_name: '기존 첫째' },
  { item_type: 'metric', library_id: 2, metric: '둘째' },
  { id: 2, library_id: 2, series_name: '기존 둘째' },
];
const newItems = [
  oldItems[0], oldItems[1], oldItems[2],
  { id: 3, library_id: 2, series_name: '새로 추가한 둘째' },
  oldItems[3],
];
const timers = [];
const requests = [];
const context = {
  pluginId: 'rabbit_plugins', shadowRoot, items: oldItems,
  Element, MutationObserver, AbortController, URLSearchParams,
  document: {
    createElement: () => new Element(),
    getElementById: id => id === 'dashboard-new-row' ? coreRow : null,
    querySelector: () => null,
  },
  window: { scrollX: 0, scrollY: 0 },
  requestAnimationFrame: callback => callback(),
  setTimeout(callback) { timers.push(callback); return timers.length; },
  clearTimeout() {},
  fetch: async (url, options) => {
    requests.push({ url, options });
    return { ok: true, json: async () => ({ success: true, items: newItems }) };
  },
  console,
};

(async () => {
  const dashboardCode = fs.readFileSync(path.join(__dirname, '../dashboard.js'), 'utf8')
    .replace("import('/static/js/series_cover_ratio.js')", 'Promise.resolve({ bindSeriesCoverRatio() {} })');
  vm.runInNewContext(dashboardCode, context);
  shadowRoot.events.click({ target: tabs.children[1] });
  row.scrollLeft = 47;
  coreRow.__dashboardSignature = 'general:new';
  observers.find(observer => observer.target === coreRow).callback();
  assert.strictEqual(timers.length, 1);
  timers.shift()();
  await new Promise(resolve => setImmediate(resolve));
  assert.strictEqual(requests.length, 1);
  assert.match(requests[0].url, /type=general&limit=20/);
  assert.strictEqual(tabs.children[1].getAttribute('aria-selected'), 'true');
  assert.strictEqual(tabs.children[1].children[1].textContent, '2');
  assert.strictEqual(row.children[0].dataset.bookId, '3');
  assert.strictEqual(row.scrollLeft, 47);
  observers.find(observer => observer.target === coreRow).callback();
  assert.strictEqual(timers.length, 0);
  console.log('PASS new scan refreshes widget without page reload and preserves selected library');
})().catch(error => { console.error(error); process.exitCode = 1; });
