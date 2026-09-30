const fs = require('fs'), vm = require('vm'), assert = require('assert');
const code = fs.readFileSync(require('path').join(__dirname, '../detail/script.js'), 'utf8');
const ratingCode = code.slice(code.indexOf('  function parseRating('), code.indexOf('  function applyContentRating('));
const cases = [
  [{ content_rating_level: 0 }, [], 'everyone'],
  [{ content_rating_level: 15 }, [], 'ma15+'],
  [{ age_rating_level: 18 }, [], 'm'],
  [{ content_rating_level: 19 }, [], 'r18'],
  [{ content_rating_level: 20 }, [], 'adult only 18+'],
  [{}, [{ books_lv: 'ma15+' }], 'ma15+'],
  [{ books_lv: '일반' }, [], 'everyone'],
  [{ books_lv: 'r18' }, [], 'r18'],
  [{ books_lv: 'adult only' }, [], 'm'],
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
for (const [meta, expectedLevel, expectedLabel] of [
  [{ books_lv: 'R18+' }, 19, '성인망가'],
  [{ content_rating_level: 19 }, 19, '성인망가'],
  [{ content_rating_level: 20 }, 20, '포르노'],
]) {
  const context = { meta, books: [] };
  vm.createContext(context);
  vm.runInContext(ratingCode, context);
  assert.strictEqual(context.getContentRating().level, expectedLevel);
  assert.strictEqual(context.contentRatingBadge(context.getContentRating()).label, expectedLabel);
}
{
  const context = {
    meta: { books_lv: 'R18+', content_rating_level: 18, content_rating_label: '18세 이용가' },
    books: [],
  };
  vm.createContext(context);
  vm.runInContext(ratingCode, context);
  assert.strictEqual(context.contentRatingBadge(context.getContentRating()).label, '성인망가');
}
console.log(`PASS ${cases.length + 3} displayed/editor rating cases`);
