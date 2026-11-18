const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const code = fs.readFileSync(require('path').join(__dirname, '../detail/script.js'), 'utf8');
const labelStart = code.indexOf('  const title = (book) => {');
const labelEnd = code.indexOf('  const num = ', labelStart);
const start = code.indexOf('  function metadataNumber(');
const end = code.indexOf('  function chapterCoverage(', start);
assert.ok(labelStart >= 0 && labelEnd > labelStart, 'book label helpers should be present');
assert.ok(start >= 0 && end > start, 'series volume comparator should be present');

const context = {
  books: [],
  media: false,
  chapterNumber: (book) => Number(book.number || 0),
};
vm.createContext(context);
vm.runInContext(code.slice(labelStart, labelEnd) + code.slice(start, end)
  + '\nthis.fileDisplayLabel = fileDisplayLabel;', context);

const novel = {
  title: '소설 주술회전 01권 (E000003568417) (리디)',
  file_path: '/books/소설 주술회전 01권 (E000003568417) (리디).epub',
  metadata_title: '떠나가는 여름과 돌아오는 가을',
};
assert.strictEqual(context.fileDisplayLabel(novel), '떠나가는 여름과 돌아오는 가을',
  'embedded Title must take precedence over the filename and E-code');
assert.strictEqual(context.fileDisplayLabel({ ...novel, title_alias: '사용자 제목' }), '사용자 제목',
  'an explicit user title must take precedence over embedded Title');
assert.strictEqual(context.fileDisplayLabel({ ...novel, title_alias: ' ', metadata_title: '  ' }),
  '01권 (E000003568417)', 'files without Title keep the filename fallback');
assert.strictEqual(context.fileDisplayLabel({ ...novel, metadata_title: '작품 1권: 여름' }), '작품 1권: 여름',
  'Title must not be truncated to its volume number');
assert.strictEqual(context.fileDisplayLabel(novel, 0, { unit: 'volume' }), '01권 (E000003568417)',
  'chapter groups must retain their volume label rather than their first chapter Title');
assert.strictEqual(context.fileDisplayLabel({
  title: '작품 02권 (완결)', file_path: '/books/작품 02권 (완결) (리디).cbz',
}), '02권 (완결)', 'filename-only edition and completion labels must remain intact');

const books = [
  { id: 1, file_path: '/books/작품 4.5권.epub', metadata_title: '외전', volume: 4 },
  { id: 2, file_path: '/books/작품 4권.epub', metadata_title: '본편', volume: 4 },
  { id: 3, file_path: '/books/작품 5권.epub', metadata_title: '다음 이야기', volume: 5 },
];
const ordered = books
  .map((book, index) => ({ book, index }))
  .sort((a, b) => context.compareSeriesVolumeBooks(a.book, b.book) || a.index - b.index)
  .map(({ book }) => book.id);

assert.deepStrictEqual(ordered, [2, 1, 3], '4권 must precede 4.5권 and 5권');

const zeroVolume = {
  id: 4, file_path: '/books/작품 00권.epub', document_volume_index: null,
};
const oneVolume = {
  id: 5, file_path: '/books/작품 01권.epub', document_volume_index: 1,
};
const unknownVolume = { id: 6, file_path: '/books/작품 외전.epub' };
const twelveKingdomsZero = {
  id: 7, file_path: '/books/십이국기 00권：마성의 아이.epub', document_volume_index: 1,
};
assert.strictEqual(context.seriesVolumeSortNumber(twelveKingdomsZero), 0,
  'the explicit 00권 filename takes priority over a conflicting stored index');
assert.strictEqual(context.volumeNumber({ volume: 0, document_volume_index: 1 }), 0,
  'a stored numeric zero is not replaced by a positive fallback');
assert.strictEqual(context.volumeNumber({}), null, 'missing volume metadata stays unknown');
assert.strictEqual(context.seriesVolumeSortNumber(zeroVolume), 0,
  '00권 from the filename is an explicit volume zero');
assert.strictEqual(context.seriesCardNumber(zeroVolume, 0), '00',
  'an explicit zero volume keeps its 00 card number');
const zeroOrder = [oneVolume, zeroVolume, unknownVolume]
  .map((book, index) => ({ book, index }))
  .sort((a, b) => context.compareSeriesVolumeBooks(a.book, b.book) || a.index - b.index)
  .map(({ book }) => book.id);
assert.deepStrictEqual(zeroOrder, [4, 5, 6],
  'volume zero sorts before positive volumes and unknown-volume books');
context.books = [oneVolume, unknownVolume, zeroVolume];
const grouped = context.volumeGroups().map((group) => [group.volume, group.books.map((book) => book.id)]);
assert.strictEqual(JSON.stringify(grouped), JSON.stringify([[0, [4]], [1, [5]], [null, [6]]]),
  'zero-volume groups sort first and unknown groups stay last');
assert.ok(code.includes('const seriesBooks = media ? books : books')
  && code.includes('.sort((a, b) => compareSeriesVolumeBooks(a.book, b.book) || a.index - b.index)'),
  'the series carousel should apply the volume comparator');
assert.ok(code.includes('const volume = seriesVolumeSortNumber(book);'),
  'grouped volume cards should also distinguish decimal volume labels');
console.log('PASS labels, aliases, decimal order and explicit volume zero');
