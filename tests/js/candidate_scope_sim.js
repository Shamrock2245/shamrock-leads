// Proves the drawer passes the selected card's county/state when two bonds
// share a booking number. Synthetic values only; nothing leaves the process.
'use strict';
const fs = require('fs');
const vm = require('vm');
let src = fs.readFileSync(process.argv[2], 'utf8');
// Expose a test-only seeder next to the public API without changing production code paths.
if (!src.includes('__seedDataForTest')) {
  src = src.replace(
    'openDetail: openDetail, openDetailFromCard: openDetailFromCard, closeDetail: closeDetail, navDetail: navDetail,',
    'openDetail: openDetail, openDetailFromCard: openDetailFromCard, closeDetail: closeDetail, navDetail: navDetail,\n' +
    '    __seedDataForTest: function(rows){ _data = rows.slice(); },\n' +
    '    __currentScopeForTest: function(){ return _currentScope; },'
  );
}

function makeEnv() {
  const calls = [];
  const toasts = [];
  const els = {
    ldCandidateList: {
      textContent: '',
      innerHTML: '',
      querySelectorAll() { return []; },
      addEventListener() {},
    },
    prospDetailPanel: { classList: { add() {}, remove() {} } },
    prospDrawerOverlay: { classList: { add() {}, remove() {} } },
    prospDetailTitle: { textContent: '', dataset: {} },
    prospDetailBody: {
      innerHTML: '',
      querySelectorAll() { return []; },
      addEventListener() {},
    },
    prospDrawerPrev: { disabled: false },
    prospDrawerNext: { disabled: false },
    ldPipelineMount: { innerHTML: '' },
  };
  const fetchStub = async (url, opts) => {
    const method = (opts && opts.method) || 'GET';
    const u = String(url);
    calls.push({ url: u, method, body: opts && opts.body ? JSON.parse(opts.body) : null });
    if (u.startsWith('/api/leads/')) {
      return { ok: true, status: 200, headers: { get: () => 'application/json' },
        json: async () => ({ error: 'not_found' }) };
    }
    if (u.includes('/api/contacts/') && method === 'GET') {
      const q = new URL(u, 'http://local');
      return {
        ok: true, status: 200, headers: { get: () => 'application/json' },
        json: async () => ({
          county: q.searchParams.get('county') || '',
          state: q.searchParams.get('state') || '',
          discovery_status: 'complete',
          discovery_rev: 1,
          discovered_contacts: [{
            candidate_id: 'c1', name: 'Sample Contact', review_status: 'unreviewed',
            matched_anchors: ['phone'], confidence: 0.5, source: 'prior_shamrock',
          }],
        }),
      };
    }
    return { ok: true, status: 200, headers: { get: () => 'application/json' },
      json: async () => ({}) };
  };
  const sandbox = {
    window: {
      API_BASE: '',
      SL: { toast: (m, t) => toasts.push({ m, t }) },
      _leadMap: {},
      _isLeeFlorida: () => false,
      slBookingLabel: (b) => b,
    },
    document: {
      getElementById: (id) => els[id] || { innerHTML: '', textContent: '', classList: { add(){}, remove(){} }, dataset: {}, disabled: false, querySelectorAll(){return [];}, addEventListener(){} },
      addEventListener() {},
      querySelector() { return null; },
      querySelectorAll() { return []; },
      body: { classList: { add() {}, remove() {} } },
    },
    fetch: fetchStub,
    setTimeout: (fn) => { if (typeof fn === 'function') try { fn(); } catch (e) {} return 0; },
    clearTimeout() {},
    setInterval: () => 0,
    clearInterval() {},
    console,
    URLSearchParams,
    URL,
    Set,
    Map,
    Promise,
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  };
  sandbox.window.fetch = fetchStub;
  sandbox.SL = sandbox.window.SL;
  sandbox.window.document = sandbox.document;
  vm.createContext(sandbox);
  vm.runInContext(src + '\n;this.SLProspective = window.SLProspective;', sandbox);
  return { P: sandbox.SLProspective, calls, toasts };
}

function fakeCard(bk, county, state) {
  return {
    getAttribute(name) {
      if (name === 'data-bk') return bk;
      if (name === 'data-county') return county;
      if (name === 'data-state') return state;
      return null;
    },
  };
}

function contactGets(calls) {
  return calls.filter((c) => c.method === 'GET' && c.url.includes('/api/contacts/')).map((c) => {
    const q = new URL(c.url, 'http://local');
    return {
      booking: decodeURIComponent(q.pathname.split('/').pop()),
      county: q.searchParams.get('county'),
      state: q.searchParams.get('state'),
    };
  });
}

(async () => {
  const bonds = [
    { booking_number: 'B-100', county: 'Lee', state: 'FL', defendant_name: 'Casey Sample',
      stage: 'contacted', bond_amount: 1000, communication_log: [], lead_score: 1 },
    { booking_number: 'B-100', county: 'Collier', state: 'FL', defendant_name: 'Other Person',
      stage: 'contacted', bond_amount: 2000, communication_log: [], lead_score: 1 },
  ];
  const out = {};

  // Card for Collier (second) — must request Collier, not Lee.
  {
    const env = makeEnv();
    env.P.__seedDataForTest(bonds);
    await env.P.openDetailFromCard(fakeCard('B-100', 'Collier', 'FL'));
    // Allow the async renderDetail/loadCandidates to finish.
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    out.collier_card = {
      gets: contactGets(env.calls),
      scope: env.P.__currentScopeForTest(),
    };
  }

  // Card for Lee (first) — still Lee.
  {
    const env = makeEnv();
    env.P.__seedDataForTest(bonds);
    await env.P.openDetailFromCard(fakeCard('B-100', 'Lee', 'FL'));
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    out.lee_card = {
      gets: contactGets(env.calls),
      scope: env.P.__currentScopeForTest(),
    };
  }

  // Booking-only openDetail without place: falls back to first match (Lee),
  // documented guard so we do not silently change that path.
  {
    const env = makeEnv();
    env.P.__seedDataForTest(bonds);
    await env.P.openDetail('B-100');
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    out.booking_only = {
      gets: contactGets(env.calls),
      scope: env.P.__currentScopeForTest(),
    };
  }

  // Card HTML carries data-county / data-state for the selected record.
  {
    const env = makeEnv();
    env.P.__seedDataForTest(bonds);
    // renderCard is not exported; reconstruct via openDetailFromCard attrs contract.
    out.card_contract = {
      has_from_card: typeof env.P.openDetailFromCard === 'function',
      scope_key: env.P._scopeKey('B-100', 'Collier', 'FL'),
      finds_collier: !!(env.P._findBondByIdentity('B-100', 'Collier', 'FL') &&
        env.P._findBondByIdentity('B-100', 'Collier', 'FL').county === 'Collier'),
      finds_lee: !!(env.P._findBondByIdentity('B-100', 'Lee', 'FL') &&
        env.P._findBondByIdentity('B-100', 'Lee', 'FL').county === 'Lee'),
    };
  }

  console.log(JSON.stringify(out));
})().catch((e) => { console.error(String(e && e.stack || e)); process.exit(1); });
