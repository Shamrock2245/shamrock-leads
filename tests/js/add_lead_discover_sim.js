// Loads sl-prospective.js with minimal stubs, runs Add Lead and the candidate
// review action against canned responses, and prints what was POSTed and what
// staff were told. Synthetic values only; nothing leaves the process.
'use strict';
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');

function makeEnv(fields, routes) {
  const calls = [];
  const toasts = [];
  const els = {};
  Object.keys(fields).forEach((id) => {
    const v = fields[id];
    els[id] = typeof v === 'boolean'
      ? { checked: v, value: '', disabled: false, textContent: '' }
      : { value: v, checked: false, disabled: false, textContent: '' };
  });
  const fetchStub = async (url, opts) => {
    const method = (opts && opts.method) || 'GET';
    calls.push({ url: String(url), method, body: opts && opts.body ? JSON.parse(opts.body) : null });
    for (const [prefix, resp] of routes) {
      if (String(url).startsWith(prefix) && (resp.method || 'POST') === method) {
        return { ok: resp.status < 400, status: resp.status, json: async () => resp.json };
      }
    }
    return { ok: true, status: 200, json: async () => ({ success: true, data: [] }) };
  };
  const sandbox = {
    window: { API_BASE: '', SL: { toast: (m, t) => toasts.push({ m, t }) } },
    document: {
      getElementById: (id) => els[id] || null,
      addEventListener() {},
      querySelector() { return null; },
      querySelectorAll() { return []; },
    },
    fetch: fetchStub,
    setTimeout: () => 0,
    clearTimeout: () => {},
    setInterval: () => 0,
    console,
    URLSearchParams,
    Set,
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  };
  sandbox.window.fetch = fetchStub;
  sandbox.SL = sandbox.window.SL;
  vm.createContext(sandbox);
  vm.runInContext(src + '\n;this.SLProspective = window.SLProspective;', sandbox);
  return { P: sandbox.SLProspective, calls, toasts };
}

const ADD_OK = ['/api/prospective-bonds', { status: 200, json: { success: true } }];

async function addLead(county, state, discoverResp) {
  const fields = { alDefName: 'Casey Sample', alBooking: 'B-100', alCounty: county, alContactDiscover: true };
  if (state !== undefined) fields.alState = state;
  const routes = [['/api/contacts/discover', discoverResp || { status: 200, json: { contacts: [] } }], ADD_OK];
  const env = makeEnv(fields, routes);
  await env.P.submitAddLead();
  const discover = env.calls.filter((c) => c.url === '/api/contacts/discover');
  return { discover: discover.map((c) => c.body), toasts: env.toasts };
}

(async () => {
  const out = {};
  out.fl_county = await addLead('Lee');
  out.fl_county_suffix = await addLead('Lee County');
  out.form_state = await addLead('Fulton', 'ga');
  out.non_fl_county = await addLead('Fulton');
  out.no_county = await addLead('');
  out.conflict = await addLead('Lee', undefined, { status: 409, json: { error: 'county_and_state_required' } });
  out.legacy = await addLead('Lee', undefined, { status: 409, json: { error: 'legacy_contacts_ambiguous' } });
  out.server_error = await addLead('Lee', undefined, { status: 500, json: { error: 'discovery_failed' } });

  const env = makeEnv({}, [['/api/contacts/B-100/candidates/', { status: 409, json: { error: 'stale_revision' } }]]);
  await env.P.reviewCandidate('B-100', 'cand-1', 'approve', 'Lee', 'FL', 3);
  out.review_stale = {
    posts: env.calls.filter((c) => c.method === 'POST').map((c) => ({ url: c.url, body: c.body })),
    toasts: env.toasts,
  };
  out.fl_registry = Array.from(env.P._flRegistryCounties);
  console.log(JSON.stringify(out));
})().catch((e) => { console.error(String(e && e.stack || e)); process.exit(1); });
