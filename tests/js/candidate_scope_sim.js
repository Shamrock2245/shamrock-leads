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

function makeEnv(envOpts) {
  envOpts = envOpts || {};
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
  const fetchStub = async (url, reqOpts) => {
    const method = (reqOpts && reqOpts.method) || 'GET';
    const u = String(url);
    calls.push({ url: u, method, body: reqOpts && reqOpts.body ? JSON.parse(reqOpts.body) : null });
    if (u.startsWith('/api/leads/')) {
      return { ok: true, status: 200, headers: { get: () => 'application/json' },
        json: async () => ({ error: 'not_found' }) };
    }
    if (u.includes('/api/prospective-bonds')) {
      return { ok: true, status: 200, headers: { get: () => 'application/json' },
        json: async () => ({ bonds: envOpts.searchBonds || [] }) };
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
  return { P: sandbox.SLProspective, calls, toasts, els };
}

async function flush() {
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
}

function snap(env) {
  return {
    gets: contactGets(env.calls),
    scope: env.P.__currentScopeForTest(),
    title: env.els.prospDetailTitle.textContent,
    showed_legacy_name: env.els.prospDetailBody.innerHTML.indexOf('Legacy Sample') >= 0
      || env.els.prospDetailTitle.textContent.indexOf('Legacy Sample') >= 0,
    showed_georgia_name: env.els.prospDetailBody.innerHTML.indexOf('Georgia Sample') >= 0
      || env.els.prospDetailTitle.textContent.indexOf('Georgia Sample') >= 0,
  };
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

  // Codex 11/7 P2: a blank state is legacy Florida only for an FL-registry
  // county. It must not satisfy an explicit other state (Lee, GA).
  function bond(row) {
    return JSON.parse(JSON.stringify(row));
  }
  const legacyLee = {
    booking_number: 'B-100', county: 'Lee', state: '', defendant_name: 'Legacy Sample',
    stage: 'contacted', bond_amount: 1000, communication_log: [], lead_score: 1,
  };
  const georgiaLee = {
    booking_number: 'B-100', county: 'Lee', state: 'GA', defendant_name: 'Georgia Sample',
    stage: 'contacted', bond_amount: 2000, communication_log: [], lead_score: 1,
  };
  const chathamBlank = {
    booking_number: 'B-200', county: 'Chatham', state: '', defendant_name: 'Legacy Sample',
    stage: 'contacted', bond_amount: 1000, communication_log: [], lead_score: 1,
  };

  {
    const env = makeEnv();
    env.P.__seedDataForTest([bond(legacyLee), bond(georgiaLee), bond(chathamBlank)]);
    const leeFl = env.P._findBondByIdentity('B-100', 'Lee', 'FL');
    const leeGa = env.P._findBondByIdentity('B-100', 'Lee', 'GA');
    const chathamGa = env.P._findBondByIdentity('B-200', 'Chatham', 'GA');
    const chathamFl = env.P._findBondByIdentity('B-200', 'Chatham', 'FL');
    out.blank_state = {
      lee_fl_name: leeFl && leeFl.defendant_name,
      lee_ga_name: leeGa && leeGa.defendant_name,
      chatham_ga: chathamGa && chathamGa.county,
      chatham_fl: chathamFl && chathamFl.county,
    };
  }

  // Explicit Lee, GA card must not open the state-less Lee row or use its name.
  {
    const env = makeEnv({ searchBonds: [bond(legacyLee)] });
    env.P.__seedDataForTest([bond(legacyLee)]);
    await env.P.openDetailFromCard(fakeCard('B-100', 'Lee', 'GA'));
    await flush();
    out.lee_ga_against_blank = snap(env);
  }

  // The same state-less Lee row still resolves for an explicit Florida scope.
  {
    const env = makeEnv();
    env.P.__seedDataForTest([bond(legacyLee)]);
    await env.P.openDetailFromCard(fakeCard('B-100', 'Lee', 'FL'));
    await flush();
    out.lee_fl_legacy = snap(env);
  }

  // When a real Lee, GA row exists, that row is the one opened.
  {
    const env = makeEnv();
    env.P.__seedDataForTest([bond(legacyLee), bond(georgiaLee)]);
    await env.P.openDetailFromCard(fakeCard('B-100', 'Lee', 'GA'));
    await flush();
    out.lee_ga_explicit = snap(env);
  }

  // Not in the pipeline list: a search result with a blank state must not
  // satisfy an explicit Lee, GA scope.
  {
    const env = makeEnv({ searchBonds: [bond(legacyLee)] });
    env.P.__seedDataForTest([]);
    await env.P.openDetailFromCard(fakeCard('B-100', 'Lee', 'GA'));
    await flush();
    out.lee_ga_search_only = snap(env);
  }

  console.log(JSON.stringify(out));
})().catch((e) => { console.error(String(e && e.stack || e)); process.exit(1); });
