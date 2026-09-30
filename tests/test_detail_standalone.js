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
assert.ok(code.includes("const listenButtonElement = $('[data-action=listen]')"));
assert.ok(code.includes("listenButton(book, 'ds-book-listen'"));
assert.ok(code.includes("listenButton(listenTarget, 'ds-volume-listen'"));
const coverageStart = code.indexOf('  function completedVolumeCoverageStatus(');
const coverageEnd = code.indexOf('\n  function renderInfo()', coverageStart);
assert(coverageStart >= 0 && coverageEnd > coverageStart, 'completed volume status helper must exist');
const coverageContext = {};
vm.createContext(coverageContext);
vm.runInContext(code.slice(coverageStart, coverageEnd), coverageContext);
assert.equal(coverageContext.completedVolumeCoverageStatus('완결',
  {known: true, present: 1, total: 3, missing: 2}), '누락 (1/3권)');
assert.equal(coverageContext.completedVolumeCoverageStatus('완결',
  {known: true, present: 3, total: 3, missing: 0}), '');
assert.equal(coverageContext.completedVolumeCoverageStatus('연재',
  {known: true, present: 1, total: 3, missing: 2}), '');
console.log('PASS standalone detection and TTS entry points: action row, series covers, and volume covers');
