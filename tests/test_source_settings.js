const fs = require('fs'), vm = require('vm'), assert = require('assert');

class Element {
  constructor() {
    this.children = [];
    this.dataset = {};
    this.events = {};
    this.className = '';
    this.classList = {
      toggle: (name, enabled) => {
        const classes = new Set(this.className.split(/\s+/).filter(Boolean));
        if (enabled) classes.add(name); else classes.delete(name);
        this.className = [...classes].join(' ');
      },
      add: (...names) => {
        const classes = new Set(this.className.split(/\s+/).filter(Boolean));
        names.forEach(name => classes.add(name));
        this.className = [...classes].join(' ');
      },
      remove: (...names) => {
        const remove = new Set(names);
        this.className = this.className.split(/\s+/).filter(name => name && !remove.has(name)).join(' ');
      },
    };
  }
  set innerHTML(value) {
    if (value.includes('<input')) {
      const input = new Element();
      input.dataset.source = value.match(/data-source="([^"]+)"/)[1];
      this.children = [input];
    }
  }
  append(child) { this.children.push(child); }
  insertBefore(child, before) {
    const index = before ? this.children.indexOf(before) : -1;
    if (index < 0) this.children.push(child); else this.children.splice(index, 0, child);
  }
  replaceChildren() { this.children = []; }
  addEventListener(name, fn) { this.events[name] = fn; }
  querySelector(selector) { return this.querySelectorAll(selector)[0]; }
  querySelectorAll(selector) {
    const all = this.children.flatMap(child => [child, ...child.querySelectorAll('*')]);
    if (selector === '*') return all;
    return all.filter(element => {
      if (selector.startsWith('.')) return element.className.split(/\s+/).includes(selector.slice(1));
      if (selector === '[data-source]') return !!element.dataset.source;
      if (selector === 'input') return !!element.dataset.source && !element.className;
      const source = selector.match(/^input\[data-source="([^"]+)"\]$/);
      return !!source && element.dataset.source === source[1] && !element.className;
    });
  }
}

const code = fs.readFileSync(require('path').join(__dirname, '../settings.js'), 'utf8');
const block = code.slice(code.indexOf('  if (sourceList && sourceInput && fieldInput)'), code.indexOf('\n})();'));

for (const initial of ['ridi', '']) {
  const sourceList = new Element(), sourceInput = {}, fieldInput = {};
  vm.runInNewContext(block, {
    sourceList, sourceInput, fieldInput, savedConfig: { metadata_sources: initial },
    root: { querySelectorAll: () => [] }, syncChoiceState() {},
    document: { createElement: () => new Element() },
  });

  const input = key => sourceList.querySelector(`input[data-source="${key}"]`);
  const blockFor = key => sourceList.children.find(item =>
    String(item.dataset.sources || item.dataset.source || '').split(',').includes(key));
  const order = () => sourceList.children.flatMap(item =>
    String(item.dataset.sources || item.dataset.source || '').split(',').filter(Boolean));
  const drag = (movedKey, targetKey) => {
    const moved = blockFor(movedKey), target = blockFor(targetKey);
    const grip = moved.querySelector('.rabbit-metadata-source-grip');
    let payload = '';
    const dataTransfer = {
      setData(_type, value) { payload = value; },
      getData() { return payload; },
      effectAllowed: '',
    };
    grip.events.dragstart({ dataTransfer });
    target.events.drop({ preventDefault() {}, dataTransfer });
  };

  assert.ok(input('naver'));
  assert.ok(input('naver_webtoon'));
  assert.equal(blockFor('naver'), blockFor('naver_webtoon'));
  assert.equal(blockFor('naver').querySelectorAll('.rabbit-metadata-source-grip').length, 1);
  assert.ok(input('kakaopage'));
  assert.ok(input('yes24'));
  assert.ok(input('kakao_webtoon'));
  assert.equal(blockFor('kakaopage'), blockFor('kakao_webtoon'));
  assert.equal(blockFor('kakaopage').querySelectorAll('.rabbit-metadata-source-grip').length, 1);

  const toggle = (key, checked) => {
    const element = input(key);
    element.checked = checked;
    element.events.change({ target: element });
  };
  toggle('munpia', true);
  toggle('ridi', false);
  const firstUnit = sourceList.children[0].dataset.source;
  const secondUnit = sourceList.children[1].dataset.source;
  drag(firstUnit, secondUnit);
  assert.equal(sourceList.children[0].dataset.source, secondUnit);
  assert.equal(sourceList.children[1].dataset.source, firstUnit);
  for (let count = 0; count < 3; count += 1) {
    drag('munpia', order()[0]);
    assert.equal(input('munpia').checked, true);
    assert.equal(input('ridi').checked, false);
    assert.equal(sourceInput.value, 'munpia');
  }

  const reopenedList = new Element(), reopenedValue = {};
  vm.runInNewContext(block, {
    sourceList: reopenedList, sourceInput: reopenedValue, fieldInput: {},
    savedConfig: { metadata_sources: sourceInput.value },
    root: { querySelectorAll: () => [] }, syncChoiceState() {},
    document: { createElement: () => new Element() },
  });
  assert.equal(reopenedValue.value, 'munpia');
  assert.equal(reopenedList.children[0].dataset.source, 'munpia');
  assert.equal(reopenedList.querySelector('input[data-source="munpia"]').checked, true);

  drag('naver_webtoon', order()[0]);
  assert.deepEqual(order().slice(0, 2), ['naver', 'naver_webtoon']);
  drag('kyobo', 'naver_webtoon');
  const naverIndex = order().indexOf('naver');
  assert.equal(order()[naverIndex - 1], 'kyobo');
  assert.equal(order()[naverIndex + 1], 'naver_webtoon');

  drag('kakao_webtoon', order()[0]);
  assert.deepEqual(order().slice(0, 2), ['kakaopage', 'kakao_webtoon']);
  drag('ridi', 'kakao_webtoon');
  const kakaoIndex = order().indexOf('kakaopage');
  assert.equal(order()[kakaoIndex - 1], 'ridi');
  assert.equal(order()[kakaoIndex + 1], 'kakao_webtoon');

  toggle('munpia', false);
  drag('ridi', order()[0]);
  assert.equal(sourceInput.value, '');
  console.log('PASS grouped handles, block reorder, and selection persistence; initial=' + JSON.stringify(initial));
}
