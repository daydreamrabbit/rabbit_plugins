const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');
const code = fs.readFileSync(path.join(__dirname, '../detail/script.js'), 'utf8');
const start = code.indexOf('  function renderInfo() {');
const end = code.indexOf('    const formats =', start);
const guard = code.slice(start, end) + '\n}';
for (const failed of [false, true]) {
  const facts = {children: [], replaceChildren(...items) {this.children = items;}, append(item) {this.children.push(item);}};
  const elements = {'[data-facts]': facts, '[data-tags-block]': {}, '[data-genres-block]': {}, '[data-site-links]': {}};
  const ctx = {detailDataReady: false, detailDataFailed: failed,
    $: key => elements[key], node: (tag, cls, text) => ({tag, text, addEventListener() {}})};
  vm.createContext(ctx);vm.runInContext(guard+'\nrenderInfo();',ctx);
  assert.equal(elements['[data-tags-block]'].hidden,true);
  assert.equal(elements['[data-genres-block]'].hidden,true);
  assert.match(facts.children[0].text, failed ? /못했습니다/ : /불러오는 중/);
  if (failed) assert.equal(facts.children[1].text,'다시 시도');
}
assert.equal((code.match(/loadRating\(\);/g)||[]).length,1);
assert.ok(code.indexOf('loadRating();') < code.indexOf('const detailLoaded = detailDataReady || await loadDetailData(false)'));
assert.ok(code.includes('const data = preparedData || await request'));
assert.ok(code.indexOf('loadDetailData(false, context.initialDetailData);') < code.indexOf("root.dataset.ready = 'true'"));
assert.ok(code.includes("createElementNS('http://www.w3.org/2000/svg', 'svg')"));
console.log('PASS initial metadata gating, failure retry, independent SVG rating initialization');
