const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const code = fs.readFileSync(require('path').join(__dirname, '../detail/script.js'), 'utf8');
const start = code.indexOf('  function seriesVolumeSortNumber(');
const end = code.indexOf('  function chapterNumber(', start);
assert.ok(start >= 0 && end > start, 'series volume comparator should be present');

const context = {
  fileDisplayLabel: (book) => book.label || '',
  volumeNumber: (book) => Number(book.volume || book.document_volume_index || 0),
};
vm.createContext(context);
vm.runInContext(code.slice(start, end), context);

const books = [
  { id: 1, label: '4.5권', volume: 4 },
  { id: 2, label: '4권', volume: 4 },
  { id: 3, label: '5권', volume: 5 },
];
const ordered = books
  .map((book, index) => ({ book, index }))
  .sort((a, b) => context.compareSeriesVolumeBooks(a.book, b.book) || a.index - b.index)
  .map(({ book }) => book.id);

assert.deepStrictEqual(ordered, [2, 1, 3], '4권 must precede 4.5권 and 5권');
assert.ok(code.includes('const seriesBooks = media ? books : books')
  && code.includes('.sort((a, b) => compareSeriesVolumeBooks(a.book, b.book) || a.index - b.index)'),
  'the series carousel should apply the volume comparator');
assert.ok(code.includes('const volume = seriesVolumeSortNumber(book);'),
  'grouped volume cards should also distinguish decimal volume labels');
console.log('PASS series browse sorts decimal volumes numerically (4권, 4.5권, 5권)');
