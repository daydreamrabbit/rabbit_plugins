const fs = require('fs');
const vm = require('vm');
const assert = require('assert');

const code = fs.readFileSync(require('path').join(__dirname, '../detail/script.js'), 'utf8');
const helperStart = code.indexOf('  function hasStoredSummary(');
const helperEnd = code.indexOf('\n  function renderAppearance()', helperStart);
assert(helperStart >= 0 && helperEnd > helperStart, 'stored summary helper must exist');

const context = {};
vm.createContext(context);
vm.runInContext(code.slice(helperStart, helperEnd), context);
assert.strictEqual(context.hasStoredSummary('외부 메타데이터 소개'), true);
assert.strictEqual(context.hasStoredSummary('등록된 설명이 없습니다.'), false);
assert.strictEqual(context.hasStoredSummary(''), false);

const loadStart = code.indexOf('  async function loadDetailData(');
const loadEnd = code.indexOf('\n  function renderMetadataRefresh()', loadStart);
const loadCode = code.slice(loadStart, loadEnd);
assert(loadCode.includes('storedGenres.length\n        ? storedGenres'), 'stored genre must win over per-file metadata');
assert(loadCode.includes('storedTags.length\n        ? storedTags'), 'stored tags must win over per-file metadata');
assert(loadCode.includes('!hasStoredSummary(meta.summary)'), 'ComicInfo summary must only fill a missing DB summary');
assert(loadCode.includes('meta.localized_series = meta.localized_series || data.localized_series'), 'stored localized title must win');
assert(loadCode.includes("['0', '1', '2'].includes(storedPublicationStatus)"), 'stored publication status must win');

console.log('PASS stored metadata remains authoritative over Kavita and ComicInfo fallbacks');
