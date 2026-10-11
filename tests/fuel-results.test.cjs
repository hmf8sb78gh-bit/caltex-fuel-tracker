const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(require('node:path').join(__dirname, '../index.html'), 'utf8');

// Execute production functions with DOM and cloud boundaries replaced by in-memory fixtures.
function source(name) {
  const match = html.match(new RegExp('^    (?:async )?function ' + name + '\\([^]*?^    }', 'm'));
  assert.ok(match, name);
  return match[0];
}
function fixture() {
  const node = (value = '') => ({ value, textContent: '', style: {}, className: '', checked: false });
  const c = {
    appState: { user: { id: 'test-user' }, prices: {}, fullInterval: {
      open: true, startOdo: 127844, litres: 20, paid: 823.17, baselineV2: true
    }, lastInterval: null, lastFillupResult: null },
    els: Object.fromEntries(['lastOdometer', 'currentOdometer', 'litres', 'fullTankChk', 'intervalLine',
      'resultPay', 'resultDistanceKm', 'resultCostPerKm', 'resultFuelConsumption', 'updatedAtText',
      'resultPrevCost', 'saveOdometerBtn'].map((k) => [k, node()])),
    values: { payable: 204.40, litres: 7.469, pumpPrice: 30 },
    fillupMonthTotal: 413.81, fillupMonthKm: 334, fillupsListCache: [],
    savedOperations: [], profileWrites: [], pending: [], FILLUP_TABLE: 'fillup_records',
    window: { confirm: () => true },
    loadLocalFillups: () => [], pendingOperations() { return c.pending; },
    profileSnapshot(odo, cost) { return { last_odometer: odo, last_consumption: cost,
      full_interval: c.appState.fullInterval, last_interval: c.appState.lastInterval }; },
    async saveQueued(kind, payload) { c.savedOperations.push({ kind, payload }); return c.online !== false; },
    async cloudSaveRecord(odo, cost) { c.profileWrites.push({ odo, cost }); return true; },
    calculateEffectiveValues() { return c.values; }, validateInputs: () => '',
    hkYMD: () => '2026-10-11',
    tweenResultNumber(el, value, fmt) { el.textContent = Number.isFinite(value) ? fmt(value) : '--'; },
    ...Object.fromEntries(['updateCustomFieldsVisibility', 'updateTopFuelSummary', 'updateFuelCompare',
      'updateVoucherHint', 'updateMonthTotalLine', 'updateEntryDot', 'updateFooterTime', 'saveState',
      'scrollToResults', 'playResultPop', 'hideStatus', 'showStatus', 'showToast', 'openAuthModal'].map((k) => [k, () => {}]))
  };
  c.els.lastOdometer.value = '128542'; c.els.currentOdometer.value = '128849';
  c.els.litres.value = '7.469';
  vm.createContext(c);
  ['neat', 'money', 'numberValue', 'getDistanceKm', 'getSingleFillupView', 'getIntervalView',
    'updatePrevCostBadge', 'calculate', 'saveCurrentOdometerAsLast', 'reconcileFromCloud',
    'resolveFillupDistance'].forEach((name) => vm.runInContext(source(name), c));
  return c;
}
function cloud(c, rows, beforeReturn = () => {}) {
  c.sb = { from(table) {
    assert.equal(table, c.FILLUP_TABLE);
    const query = { select() { return query; }, eq(key, uid) {
      assert.equal(key, 'user_id'); assert.equal(uid, 'test-user'); return query;
    }, async order() { beforeReturn(); return { data: rows }; } };
    return query;
  } };
}
const rows = [
  { fill_date: '2026-09-13', odometer: 127844, paid_amount: 0, litres: null },
  { fill_date: '2026-10-01', odometer: 128387, trip_km: 179, paid_amount: 296.75, litres: null },
  { fill_date: '2026-10-03', odometer: 128542, trip_km: 155, paid_amount: 117.06, litres: null },
  { fill_date: '2026-10-11', odometer: 128849, trip_km: 307, paid_amount: 204.40, litres: 7.469 }
];

test('all inline scripts parse', () => {
  for (const match of html.matchAll(/<script\b[^>]*>([^]*?)<\/script>/g)) new vm.Script(match[1]);
});
test('October 11 uses 307 km and HK$0.67/km, separate from 1005 km cumulative', () => {
  const c = fixture(); c.calculate();
  assert.equal(c.els.resultDistanceKm.textContent, '307 km');
  assert.equal(c.els.resultCostPerKm.textContent, 'HK$0.67 / km');
  assert.match(c.els.intervalLine.textContent, /1005 km/);
  assert.equal(c.els.resultFuelConsumption.textContent, '暫未能可靠計算');
});
test('save retains same trip and payment, and October totals stay 641 km / HK$618.21', async () => {
  const c = fixture(); c.calculate(); await c.saveCurrentOdometerAsLast();
  assert.equal(c.els.currentOdometer.value, '');
  assert.equal(c.els.resultDistanceKm.textContent, '307 km');
  assert.equal(c.els.resultPay.textContent, 'HK$204.4');
  assert.equal(c.els.resultCostPerKm.textContent, 'HK$0.67 / km');
  assert.equal(c.fillupMonthKm, 641); assert.equal(c.fillupMonthTotal, 618.21);
  const p = c.savedOperations[0].payload;
  assert.equal(p.fillup.trip_km, 307); assert.equal(p.fillup.litres, 7.469);
  assert.equal(p.profile.last_consumption, 204.4 / 307);
  assert.equal(c.getIntervalView(c.values).totalP, 1027.57); // no double count after save
});
test('pending offline save retains the same single result', async () => {
  const c = fixture(); c.online = false; await c.saveCurrentOdometerAsLast();
  assert.equal(c.els.resultDistanceKm.textContent, '307 km');
  assert.equal(c.savedOperations.length, 1);
});
test('next trip uses new odometer difference instead of saved snapshot', async () => {
  const c = fixture(); await c.saveCurrentOdometerAsLast();
  c.els.currentOdometer.value = '128949'; c.values.payable = 150; c.calculate();
  assert.equal(c.els.resultDistanceKm.textContent, '100 km');
  assert.equal(c.els.resultCostPerKm.textContent, 'HK$1.5 / km');
});
test('blank or zero baseline never creates lifetime distance', () => {
  const c = fixture(); c.els.lastOdometer.value = ''; c.calculate();
  assert.equal(c.els.resultDistanceKm.textContent, '--');
  c.els.lastOdometer.value = '0'; assert.ok(Number.isNaN(c.getDistanceKm()));
});
test('blank current with no saved single never falls back to interval distance', () => {
  const c = fixture(); c.els.currentOdometer.value = ''; c.calculate();
  assert.equal(c.els.resultDistanceKm.textContent, '--');
  assert.equal(c.els.resultCostPerKm.textContent, '--');
});
test('repeated or decreasing odometer cannot save', async () => {
  const c = fixture(); c.els.currentOdometer.value = '128542'; await c.saveCurrentOdometerAsLast();
  c.els.currentOdometer.value = '128500'; await c.saveCurrentOdometerAsLast();
  assert.equal(c.savedOperations.length, 0);
});
test('first full baseline excludes its own purchase from consumption', async () => {
  const c = fixture(); c.els.lastOdometer.value = ''; c.els.fullTankChk.checked = true;
  c.appState.fullInterval = { open: false };
  await c.saveCurrentOdometerAsLast();
  assert.equal(c.savedOperations.length, 0); assert.equal(c.profileWrites.length, 1);
  assert.equal(c.appState.fullInterval.litres, 0); assert.equal(c.appState.fullInterval.startFull, true);
});
test('complete two-full interval uses 600/40 and remains visible after closing', async () => {
  const c = fixture(); c.appState.fullInterval = { open: true, startOdo: 128249, litres: 20,
    paid: 400, startFull: true, volumeComplete: true };
  c.values.litres = 20; c.els.fullTankChk.checked = true;
  c.calculate(); assert.equal(c.els.resultFuelConsumption.textContent, '15 km / L');
  await c.saveCurrentOdometerAsLast();
  assert.equal(c.els.resultFuelConsumption.textContent, '15 km / L');
  assert.equal(c.appState.fullInterval.litres, 0); assert.equal(c.appState.fullInterval.paid, 0);
  assert.equal(c.els.resultDistanceKm.textContent, '307 km');
});
test('partial interval, missing litres, estimates, and non-full starting point cannot produce measured economy', () => {
  for (const extra of [{}, { startFull: true, volumeComplete: false },
    { startFull: true, volumeComplete: true, estimated: true }, { startFull: false, volumeComplete: true }]) {
    const c = fixture(); Object.assign(c.appState.fullInterval, extra); c.els.fullTankChk.checked = true;
    c.calculate(); assert.equal(c.els.resultFuelConsumption.textContent, '暫未能可靠計算');
  }
});
test('relogin reads latest single trip without rewriting cloud data or guessing missing litres', async () => {
  const c = fixture(); c.els.currentOdometer.value = ''; cloud(c, rows);
  const original = JSON.stringify(rows); await c.reconcileFromCloud();
  assert.equal(c.els.resultDistanceKm.textContent, '307 km');
  assert.equal(c.els.resultCostPerKm.textContent, 'HK$0.67 / km');
  assert.equal(c.appState.fullInterval.volumeComplete, false);
  assert.equal(c.appState.fullInterval.litres, 20); assert.equal(JSON.stringify(rows), original);
  assert.equal(c.profileWrites.length, 0); assert.equal(c.savedOperations.length, 0);
});
test('switching accounts during read discards the old response', async () => {
  const c = fixture(); cloud(c, rows, () => { c.appState.user = { id: 'another-user' }; });
  await c.reconcileFromCloud(); assert.equal(c.appState.lastFillupResult, null);
});
test('pending fillup prevents stale cloud reconciliation', async () => {
  const c = fixture(); c.pending = [{ kind: 'fillup' }];
  c.sb = { from() { throw new Error('should not query stale cloud'); } };
  await c.reconcileFromCloud(); assert.equal(c.appState.lastFillupResult, null);
});
test('baseline saved without a fillup row remains valid across relogin', async () => {
  const c = fixture(); c.appState.fullInterval = { open: true, startOdo: 128249, startFull: true,
    volumeComplete: true, litres: 20, paid: 400 };
  cloud(c, [{ fill_date: '2026-10-03', odometer: 128542, litres: 20, paid_amount: 400, full_tank: false }]);
  await c.reconcileFromCloud(); assert.equal(c.appState.fullInterval.volumeComplete, true);
  c.els.currentOdometer.value = '128849'; c.els.fullTankChk.checked = true; c.values.litres = 20;
  assert.equal(c.getIntervalView(c.values).kmL, 15);
});
test('month distance calculation still resolves supplied October records', () => {
  const c = fixture(); let prev = 128208;
  const total = rows.slice(1).reduce((sum, row) => { const km = c.resolveFillupDistance(row, prev).km; prev = row.odometer; return sum + km; }, 0);
  assert.equal(total, 641);
});
