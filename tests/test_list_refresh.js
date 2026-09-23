const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const code = fs.readFileSync(require('path').join(__dirname, '../detail/script.js'), 'utf8');
const helperStart = code.indexOf('  function invalidateSeriesList()');
const helperEnd = code.indexOf('\n  const siteNames', helperStart);
assert(helperStart >= 0 && helperEnd > helperStart, 'list invalidation helper must exist');

let invalidations = 0;
const context = {
  window: {
    invalidateBookListAfterScan() { invalidations += 1; },
  },
};
vm.createContext(context);
vm.runInContext(code.slice(helperStart, helperEnd), context);
context.invalidateSeriesList();
assert.strictEqual(invalidations, 1, 'the core list refresh hook must be called');

const refreshStart = code.indexOf('  async function refreshMetadataFromServer()');
const refreshEnd = code.indexOf('\n  function startMetadataRefresh', refreshStart);
const applyStart = code.indexOf('  async function applyMetadataResult(');
const applyEnd = code.indexOf('\n  function renderSeries()', applyStart);
const saveStart = code.indexOf('  async function save(event)');
const saveEnd = code.indexOf('\n  async function loadDetailData', saveStart);

for (const [name, source] of [
  ['background metadata refresh', code.slice(refreshStart, refreshEnd)],
  ['manual metadata apply', code.slice(applyStart, applyEnd)],
  ['detail metadata save', code.slice(saveStart, saveEnd)],
]) {
  assert(source.includes('invalidateSeriesList();'), `${name} must invalidate the series list`);
}

console.log('PASS cover and metadata changes invalidate the mounted series list');
