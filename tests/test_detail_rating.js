const fs = require('fs'), vm = require('vm'), assert = require('assert');
const code = fs.readFileSync(require('path').join(__dirname, '../detail/script.js'), 'utf8');
const ratingCode = code.slice(code.indexOf('  function parseRating('), code.indexOf('  function applyContentRating('));
const cases = [
  [{ content_rating_level: 0 }, [], 'everyone'],
  [{ content_rating_level: 15 }, [], 'ma15+'],
  [{ age_rating_level: 18 }, [], 'm'],
  [{}, [{ books_lv: 'ma15+' }], 'ma15+'],
  [{ books_lv: '일반' }, [], 'everyone'],
  [{ books_lv: 'r18' }, [], 'r18'],
  [{ books_lv: 'adult only 18+' }, [], 'adult only 18+'],
  [{ books_lv: 'everyone' }, [{ books_lv: 'ma15+' }], 'ma15+'],
  [{}, [], ''],
  [{ books_lv: 'unknown-custom' }, [], 'unknown-custom'],
];
for (const [meta, books, expected] of cases) {
  const context = { meta, books };
  vm.createContext(context);
  vm.runInContext(ratingCode, context);
  assert.strictEqual(context.editContentRatingValue(), expected);
}
console.log(`PASS ${cases.length} displayed/editor rating cases`);
