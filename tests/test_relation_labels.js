const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const source = fs.readFileSync(path.join(__dirname, '../detail/script.js'), 'utf8');
const labelStart = source.indexOf('  const relationLabels = {');
const labelEnd = source.indexOf('  let type =', labelStart);
const renderStart = source.indexOf('  function renderRelations(');
const renderEnd = source.indexOf('  function renderRecommendations(items) {', renderStart);
assert.ok(labelStart > 0 && labelEnd > labelStart && renderStart > 0 && renderEnd > renderStart);

const section = { hidden: true };
const target = { cards: [], replaceChildren(...cards) { this.cards = cards; } };
const context = {
  Set,
  canEdit: false,
  renderRelationGraph() {},
  $: selector => selector === '[data-relations-section]' ? section : target,
  bookCard: (book, _index, _related, label) => ({ name: book.series_name, label }),
};
vm.createContext(context);
vm.runInContext(source.slice(labelStart, labelEnd) + source.slice(renderStart, renderEnd)
  + '\nthis.renderRelations = renderRelations; this.editable = editableRelationTypes;', context);

const relations = {
  original: [{ library_id: 1, series_name: '원작' }],
  parody: [{ library_id: 1, series_name: '패러디 작품' }],
  contains: [{ library_id: 1, series_name: '수록 작품' }],
  other: [{ library_id: 1, series_name: '기타 작품' }],
};
context.renderRelations(relations);
assert.deepEqual(target.cards.map(card => card.label), ['원작', '패러디', '수록작', '기타']);
assert.equal(section.hidden, false);
assert.equal(context.editable.has('parody'), false, 'automatic-only types stay out of manual editor');
assert.equal(context.editable.has('original'), true);
console.log('PASS distinct automatic relationship labels and manual editor limits');
