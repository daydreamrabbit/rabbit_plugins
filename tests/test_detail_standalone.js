const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const code = fs.readFileSync(require('path').join(__dirname, '../detail/script.js'), 'utf8');
const helper = code.slice(code.indexOf('  function isStandalone()'), code.indexOf('  function renderInfo()'));
for (const [volume, count, format, chapters, expected] of [
  [1, 1, 'Special', false, true],
  [100000, 1, 'Special', false, true],
  [100000, 2, 'Special', false, false],
  [100000, 1, '', false, false],
  [2, 1, 'Special', false, false],
  [100000, 1, 'Special', true, false],
]) {
  const ctx = {meta: {comicinfo_volume: volume, comicinfo_count: count, comicinfo_format: format}, hasChapterMetadata: () => chapters};
  vm.createContext(ctx);
  assert.equal(vm.runInContext(helper + '\nisStandalone()', ctx), expected);
}
assert.ok(code.includes("const status = standalone ? '단편' : incomplete"));
assert.ok(code.includes("if (contentKind === 'book' || standalone)"));
console.log('PASS standalone detection: normal/sentinel volumes, multi-volume, missing format, chapters');
