const fs = require('fs');
const path = require('path');
const assert = require('assert');

const detail = fs.readFileSync(path.join(__dirname, '../detail/script.js'), 'utf8');
const settings = fs.readFileSync(path.join(__dirname, '../settings.html'), 'utf8');
const settingsCode = fs.readFileSync(path.join(__dirname, '../settings.js'), 'utf8');

assert.ok(detail.includes("['translator', '번역가']"), 'translator must be editable');
assert.ok(detail.includes("if (meta.translator && meta.translator !== '-') rows.push(['번역가', meta.translator])"),
  'translator must be shown in detail information');
assert.ok(detail.includes("manualEmptyFields.has('translator')"),
  'cleared translator must not be restored from embedded metadata');
assert.ok(detail.includes("key === 'translator' ? data.comicinfo?.translator"),
  'a newly stored translator must clear the old empty-field marker');
assert.ok(detail.includes('translator_saved'), 'save response must control the editor value');
assert.match(settings, /data-metadata-field="translator" checked> 번역가/,
  'translator must be selected by default in the field selector');
assert.match(settings, /name="metadata_fields_version"/,
  'the settings form must mark its migrated field list');
assert.ok(settingsCode.includes("savedFieldVersion < 1"),
  'legacy saved settings must enable translator collection once');

console.log('PASS translator detail display, editing, clear preservation, and legacy setting migration');
