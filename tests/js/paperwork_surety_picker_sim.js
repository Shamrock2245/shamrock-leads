// DOM stubs exercising the Super CRM packet-screen surety picker in sl-paperwork.js.
// Usage: node paperwork_surety_picker_sim.js <path to sl-paperwork.js>
'use strict';
const fs = require('fs');

function makeEl(id) {
  return {
    id, style: {}, value: '', checked: false, disabled: false, readOnly: false,
    innerHTML: '', textContent: '', title: '',
    classList: { add() {}, remove() {} },
    setAttribute() {}, removeAttribute() {},
  };
}
function makeSelect(id, values) {
  const sel = makeEl(id);
  sel.options = values.map((v) => ({ value: v, textContent: v.toUpperCase() }));
  let current = values[0];
  Object.defineProperty(sel, 'value', {
    get() { return current; },
    // Like a browser: a value with no matching option selects nothing ('').
    set(v) { current = sel.options.some((o) => o.value === v) ? v : ''; },
  });
  Object.defineProperty(sel, 'firstChild', { get() { return sel.options[0] || null; } });
  sel.insertBefore = (opt, ref) => {
    const i = ref ? sel.options.indexOf(ref) : sel.options.length;
    sel.options.splice(i < 0 ? sel.options.length : i, 0, opt);
  };
  sel.removeChild = (opt) => { sel.options.splice(sel.options.indexOf(opt), 1); };
  return sel;
}

let els;
function resetDom() {
  els = { pwApSurety: makeSelect('pwApSurety', ['osi', 'palmetto']) };
}
global.document = {
  getElementById(id) { if (!els[id]) els[id] = makeEl(id); return els[id]; },
  createElement() { return { value: '', textContent: '' }; },
  addEventListener() {},
};
global.window = global;
const calls = [];
let nextResponse = null;
global.fetch = async (url, opts) => {
  calls.push({ url, body: opts && opts.body ? JSON.parse(opts.body) : null });
  const r = nextResponse;
  return { ok: r.status < 400, status: r.status, json: async () => r.body };
};

eval(fs.readFileSync(process.argv[2], 'utf8'));
const P = window.SLPaperwork;

function load(ctx) {
  resetDom();
  calls.length = 0;
  P._adaptiveContext = ctx;
  const data = { context: ctx, hydration: { hydration_score: 100 }, providers: { docuseal: true } };
  P._lastContextData = data;
  P._fillFormFromContext(ctx, {});
  P._setAdaptiveAuthoritativeFieldsLocked(true);
  P._renderDocuSealReadiness(data);
  document.getElementById('pwApStaffApproval').checked = true;
  P._setAdaptiveFinalizeEnabled();
}
function snap() {
  const sel = els.pwApSurety;
  return {
    options: sel.options.map((o) => o.value),
    value: sel.value,
    pickerDisabled: sel.disabled,
    sendDisabled: els.pwApFinalizeBtn.disabled,
    ctxSurety: P._adaptiveContext.surety_id || '',
    calls: calls.map((c) => ({ url: c.url, body: c.body })),
  };
}
const chain = { match_status: 'validated', bond_case_id: 'BC-T1', poa_number: 'TEST-POA', defendant: {}, indemnitor: {} };

(async () => {
  const out = {};

  load({ ...chain, packet_id: 'PKT-AGENT1', surety_id: '' });
  out.unconfirmed = snap();
  await P.finalizeAdaptivePacket();
  out.finalizeBlocked = calls.length === 0;
  await P.onSuretyPicked('');
  out.blankPickNoCall = calls.length === 0;
  nextResponse = { status: 200, body: { success: true, surety_id: 'palmetto', changed: true } };
  await P.onSuretyPicked('palmetto');
  out.confirmed = snap();
  P._casePacketKeys = ['test_doc'];
  calls.length = 0;
  nextResponse = { status: 500, body: { success: false, error: 'stub_stop' } };
  try { await P.finalizeAdaptivePacket(); } catch (e) { /* stub response */ }
  const fin = calls.find((c) => c.url === '/api/paperwork/packet/finalize');
  out.finalizeSurety = fin ? fin.body.surety_id : null;

  load({ ...chain, packet_id: 'PKT-AGENT2', surety_id: '' });
  nextResponse = { status: 422, body: { success: false, error: 'surety_unsupported', message: 'Unknown or inactive surety.' } };
  await P.onSuretyPicked('acme');
  out.rejected = snap();

  load({ ...chain, packet_id: 'PKT-WALKIN', surety_id: 'osi' });
  out.walkin = snap();
  await P.onSuretyPicked('palmetto');
  out.walkinAfterPick = snap();

  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stderr.write(String(e && e.stack || e)); process.exit(1); });
