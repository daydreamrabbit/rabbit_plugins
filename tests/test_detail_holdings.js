const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const code = fs.readFileSync(require('path').join(__dirname, '../detail/script.js'), 'utf8');
const render = code.slice(code.indexOf('  function renderInfo()'), code.indexOf('  function renderMetadataSources()'));
class Element {
  constructor() { this.children = []; this.dataset = {}; this.textContent = ''; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
}
function facts(options = {}) {
  const elements = {};
  const context = {
    detailDataReady: true, meta: {comicinfo_count: 2, comicinfo_volume: 2,
      publication_available_volume_count: 1, publication_final_volume_found: true,
      holdings_coverage: {holdings_status: 'missing', holdings_label: '1권 누락'}, ...options.meta},
    books: options.books || [{volume: 2, file_format: 'cbz'}],
    media: !!options.media, type: options.type || 'general', contentKind: 'manga',
    $: selector => elements[selector] ||= new Element(),
    node: (tag, cls, text) => Object.assign(new Element(), {textContent: text || ''}),
    document: {createElement: () => new Element(), createTextNode: text => ({textContent: text})},
    getContentRating: () => null, chapterOnlyMode: () => !!options.chapters,
    groupedVolumeMode: () => false, volumeGroups: () => [], uniqueChapterNumbers: () => [],
    chapterCoverage: () => ({present: 0, missing: false}), chapterTargetCount: () => 0,
    hasChapterMetadata: () => !!options.chapters, isStandalone: () => !!options.standalone,
    completedVolumeCoverageStatus: () => '', publicationDate: () => '',
    detailIdentifierFact: () => ['ISBN / Web ID', '—'], split: () => [], renderTerms: () => {},
    seriesSiteLinks: () => [], fileFormatBadge: format => ({textContent: format}),
    volumeNumber: book => Number(book.volume), numberText: String,
  };
  const original = JSON.stringify(context.meta);
  vm.runInNewContext(render + '\nrenderInfo();', context);
  assert.equal(JSON.stringify(context.meta), original, 'render must not mutate metadata');
  return Object.fromEntries(elements['[data-facts]'].children.map(row => [row.dataset.factLabel, row.children[1].textContent]));
}
const owned = facts();
assert.equal(owned['보유 권수'], '1');
assert(!('보유 권번호' in owned));
assert(!('보유 상태' in owned));
assert.equal(owned['연재상태'], '1권 누락');
assert.equal(facts({meta: {holdings_coverage: {holdings_status: 'available'}}})['연재상태'], '알 수 없음');
assert(!('총권수' in owned));
assert.equal(facts({meta: {publication_status_label: '완결', holdings_coverage: {holdings_status: 'available'}}})['연재상태'], '완결');
assert.equal(facts({meta: {publication_status_label: '연재', comicinfo_count: 0, holdings_coverage: {holdings_status: 'available'}}, books: [{volume: 31}]})['연재상태'], '연재');
assert(!('보유 상태' in facts({standalone: true, books: [{volume: 100000}]})));
assert.equal(facts({standalone: true})['연재상태'], '단편');
assert.equal(facts({media: true, type: 'audiobook'})['트랙 수'], '2');
assert.equal(facts({media: true, type: 'video'})['에피소드 수'], '2');
assert(!('보유 권번호' in facts({chapters: true})));
console.log('PASS 8 holdings/publication/standalone/media/chapter render cases; metadata unchanged');
