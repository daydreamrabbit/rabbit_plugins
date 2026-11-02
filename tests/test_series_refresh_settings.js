const fs = require('fs'), path = require('path'), vm = require('vm'), assert = require('assert');
const source = fs.readFileSync(path.join(__dirname, '../settings.js'), 'utf8');
const start = source.indexOf("(function () {\n  const statusNode = root.querySelector('[data-series-refresh-status]');");
assert.ok(start > 0);
const script = source.slice(start);

async function run(value, data) {
  const status = { textContent: '' }, input = { value };
  const root = { isConnected: true, querySelector: selector => selector.includes('status') ? status : input };
  vm.runInNewContext(script, {
    root, pluginId: 'rabbit_plugins', config: { series_db_path: '/old.sqlite' },
    document: { activeElement: null }, Date,
    fetch: async (_url, options) => {
      assert.equal(JSON.parse(options.body).action_id, 'series_refresh_status');
      return { ok: true, json: async () => data };
    },
    setTimeout: resolve => { root.isConnected = false; resolve(); },
  });
  for (let i = 0; i < 10; i++) await Promise.resolve();
  return { status, input };
}

(async () => {
  const data = { success: true, enabled: true, schedule: '매일 12:30 (한국 시간)',
    active_path: '/series.2026.11.03.sqlite', state: { message: '갱신 완료', checked_at: 1793641194 } };
  const updated = await run('/old.sqlite', data);
  assert.equal(updated.input.value, data.active_path);
  assert.match(updated.status.textContent, /12:30.*갱신 완료.*마지막 확인/);
  const manual = await run('/custom-unsaved.sqlite', { ...data, enabled: false });
  assert.equal(manual.input.value, '/custom-unsaved.sqlite', 'polling preserves unsaved path edits');
  assert.match(manual.status.textContent, /자동 갱신 꺼짐/);
  console.log('PASS automatic Series refresh status, date path update, and unsaved path preservation');
})().catch(error => { console.error(error); process.exitCode = 1; });
