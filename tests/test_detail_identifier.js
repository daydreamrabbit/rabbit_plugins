const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const path = require('path');

const code = fs.readFileSync(path.join(__dirname, '../detail/script.js'), 'utf8');
const start = code.indexOf('  function detailIdentifierFact(');
const end = code.indexOf('\n  function completedVolumeCoverageStatus(', start);
assert(start >= 0 && end > start, 'identifier display helper must exist');

const context = {};
vm.createContext(context);
vm.runInContext(code.slice(start, end), context);
const row = (metadata) => JSON.parse(JSON.stringify(context.detailIdentifierFact(metadata)));

assert.deepStrictEqual(row({isbn: '', uci: 'UCI-123'}), ['UCI', 'UCI-123']);
assert.deepStrictEqual(row({isbn: '0000000000000', uci: 'UCI-123'}), ['UCI', 'UCI-123']);
assert.deepStrictEqual(row({isbn: 'ISBN-13: 9781234567890', uci: 'UCI-123'}),
  ['ISBN / Web ID', 'ISBN-13: 9781234567890']);
assert.deepStrictEqual(row({isbn: '—', UCI: 'UCI-456'}), ['UCI', 'UCI-456']);
assert.deepStrictEqual(row({isbn: '', web_id: 'WEB-1'}), ['ISBN / Web ID', 'WEB-1']);
assert.deepStrictEqual(row({isbn: '', uci: ''}), ['ISBN / Web ID', '—']);

console.log('PASS identifier shows UCI only when ISBN is absent or a placeholder');
