// Loads sl-tracking.js with minimal stubs and prints the JSON body the
// Discover Contacts action would POST. Synthetic values only.
'use strict';
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
const calls = [];
const sandbox = {
  window: { API_BASE: '' },
  document: {
    getElementById(id) {
      if (id === 'trkDetailPanel' || id === 'trkDetailBody' || id === 'trkDetailTitle') {
        return { style: {}, innerHTML: '', textContent: '', scrollIntoView() {} };
      }
      return null;
    },
    addEventListener() {},
  },
  fetch: async (url) => {
    calls.push(String(url));
    return { ok: true, status: 200, json: async () => ({ defendant_name: 'GA Card', county: 'Lee', state: 'GA', status: 'active' }) };
  },
  URLSearchParams,
  console,
};
vm.createContext(sandbox);
vm.runInContext(src + '\n;this.SLTracking = SLTracking;', sandbox);
const T = sandbox.SLTracking;
(async () => {
  await T.openDetail('B-100', 'Lee', 'GA');
  const out = {
    explicit: T._discoverBody('B-100', 'Casey Sample', 'Lee', 'FL'),
    blank: T._discoverBody('B-100', 'Casey Sample', '', ''),
    detail_call: T._detailCall('B-100', 'Lee', 'GA'),
    history_url: calls[0] || '',
  };
  console.log(JSON.stringify(out));
})().catch((err) => { console.error(err); process.exit(1); });
