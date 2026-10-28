// Loads sl-tracking.js with minimal stubs and prints the JSON body the
// Discover Contacts action would POST. Synthetic values only.
'use strict';
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
const sandbox = {
  window: { API_BASE: '' },
  document: { getElementById() { return null; }, addEventListener() {} },
  console,
};
vm.createContext(sandbox);
vm.runInContext(src + '\n;this.SLTracking = SLTracking;', sandbox);
const T = sandbox.SLTracking;
const out = {
  explicit: T._discoverBody('B-100', 'Casey Sample', 'Lee', 'FL'),
  blank: T._discoverBody('B-100', 'Casey Sample', '', ''),
};
console.log(JSON.stringify(out));
