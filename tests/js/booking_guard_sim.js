// Minimal DOM stubs to exercise sl-booking-guard.js value handling.
const fs = require('fs');
const KEY = 'md_dedupe_v2:' + 'ab'.repeat(32);
function makeCtor(name, tag) {
  const C = function (type) { this.tagName = tag; this._v = ''; this._type = type || ''; this.dataset = {}; this.nodeType = 1; };
  Object.defineProperty(C, 'name', { value: name });
  Object.defineProperty(C.prototype, 'value', { configurable: true, enumerable: true,
    get() { return this._v; }, set(v) { this._v = String(v); } });
  C.prototype.getAttribute = function (a) { return a === 'type' ? this._type : null; };
  C.prototype.setAttribute = function () {};
  return C;
}
global.HTMLInputElement = makeCtor('HTMLInputElement', 'INPUT');
global.HTMLTextAreaElement = makeCtor('HTMLTextAreaElement', 'TEXTAREA');
const listeners = {};
global.document = { readyState: 'complete', body: null, documentElement: null,
  addEventListener(t, f) { listeners[t] = f; } };
global.navigator = {};
global.window = global;
eval(fs.readFileSync(process.argv[2], 'utf8'));
const out = {};
const text = new HTMLInputElement('text');
text.value = KEY;
out.textShown = text._v; out.textRead = text.value; out.textKept = text.dataset.internalKey;
const chk = new HTMLInputElement('checkbox');
chk.value = KEY; out.chkRead = chk.value; out.chkShown = chk._v;
const hidden = new HTMLInputElement('hidden'); hidden.value = KEY; out.hiddenRead = hidden.value;
const mixed = new HTMLTextAreaElement(); mixed.value = 'note ' + KEY + ' end'; out.mixed = mixed.value;
const plain = new HTMLInputElement('text'); plain.value = '2026-123456'; out.plain = plain.value;
// user types into the key field
text._v = '24-0001'; listeners.input && listeners.input({ target: text });
out.afterType = text.value; out.keyCleared = !text.dataset.internalKey;
out.label = window.slBookingLabel(KEY) + '|' + window.slBookingLabel('2026-123456');
console.log(JSON.stringify(out));
