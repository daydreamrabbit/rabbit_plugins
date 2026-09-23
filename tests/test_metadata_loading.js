const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const code = fs.readFileSync(require('path').join(__dirname, '../detail/script.js'), 'utf8');
const renderCode = code.slice(
  code.indexOf('  function renderMetadataResults('),
  code.indexOf('  function openMetadataSearch('),
);

const results = {
  children: [],
  replaceChildren() { this.children = []; },
  append(child) { this.children.push(child); },
};
const context = {
  $: () => results,
  node: (_tag, className, text) => ({ className, text }),
};
vm.createContext(context);
vm.runInContext(renderCode, context);

context.renderMetadataResults([], false);
assert.deepStrictEqual(results.children, []);
context.renderMetadataResults([], true);
assert.strictEqual(results.children.length, 1);
assert.strictEqual(results.children[0].className, 'ds-metadata-empty');
console.log('PASS metadata empty message is hidden while a search is loading');
