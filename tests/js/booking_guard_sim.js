// Minimal DOM stubs exercising sl-booking-guard.js value/form handling.
// "Native" submission reads the element's internal value through the
// original accessor (as a browser does), never the page's JS getter.
'use strict';
const fs = require('fs');
const KEY = 'md_dedupe_v2:' + 'ab'.repeat(32);
const NATIVE = {};
function makeCtor(name, tag) {
  const C = function (type, attrs) {
    this.tagName = tag; this._v = ''; this.dataset = {}; this.nodeType = 1; this.form = null;
    this._attrs = Object.assign({}, attrs || {});
    if (type) this._attrs.type = type;
  };
  Object.defineProperty(C, 'name', { value: name });
  const desc = { configurable: true, enumerable: true, get() { return this._v; }, set(v) { this._v = String(v); } };
  Object.defineProperty(C.prototype, 'value', desc);
  NATIVE[tag] = desc;
  C.prototype.getAttribute = function (a) { return a in this._attrs ? this._attrs[a] : null; };
  C.prototype.hasAttribute = function (a) { return a in this._attrs; };
  C.prototype.setAttribute = function (a, v) { this._attrs[a] = String(v); };
  C.prototype.removeAttribute = function (a) { delete this._attrs[a]; };
  C.prototype.select = function () { this._selected = true; };
  return C;
}
global.HTMLInputElement = makeCtor('HTMLInputElement', 'INPUT');
global.HTMLTextAreaElement = makeCtor('HTMLTextAreaElement', 'TEXTAREA');
const listeners = {};
const head = { children: [], appendChild(n) { this.children.push(n); } };
global.document = {
  readyState: 'complete', body: null, documentElement: null, head,
  createElement(tag) { return { tagName: tag, id: '', textContent: '' }; },
  getElementById(id) { return head.children.find((n) => n.id === id) || null; },
  addEventListener(t, f) { (listeners[t] = listeners[t] || []).push(f); },
};
global.navigator = {};
global.window = global;
eval(fs.readFileSync(process.argv[2], 'utf8'));

function fire(type, ev) { (listeners[type] || []).forEach((f) => f(ev)); }
function makeForm(els) { const f = { elements: els }; els.forEach((e) => { e.form = f; }); return f; }
// Browser-style submission: submit event (capture), entry list from internal
// values of named controls, then the formdata event.
function nativeSubmit(form) {
  fire('submit', { target: form });
  const entries = new Map();
  form.elements.forEach((el) => {
    const n = el.getAttribute('name');
    if (!n) return;
    const t = (el.getAttribute('type') || '').toLowerCase();
    if ((t === 'checkbox' || t === 'radio') && !el.checked) return;
    entries.set(n, NATIVE[el.tagName].get.call(el));
  });
  const fd = { get: (k) => (entries.has(k) ? entries.get(k) : null), set: (k, v) => entries.set(k, String(v)) };
  fire('formdata', { target: form, formData: fd });
  return Object.fromEntries(entries);
}

const out = {};
// 1. Form-bound text input on an MD record: value untouched, only masked.
const bk = new HTMLInputElement('text', { name: 'booking_number' });
const note = new HTMLTextAreaElement(null, { name: 'notes' });
const chk = new HTMLInputElement('checkbox', { name: 'bulk', class: 'bulk-exon-chk' });
const radio = new HTMLInputElement('radio', { name: 'pick' });
const hidden = new HTMLInputElement('hidden', { name: 'rid' });
const form = makeForm([bk, note, chk, radio, hidden]);
bk.value = KEY; note.value = 'n'; chk.value = KEY; chk.checked = true; radio.value = KEY; radio.checked = true; hidden.value = KEY;
out.formBoundRaw = bk._v;
out.formBoundRead = bk.value;
out.formBoundMasked = bk.hasAttribute('data-sl-masked');
out.posted = nativeSubmit(form);
fire('focusin', { target: bk }); out.selectedOnFocus = !!bk._selected;
// 2. App blanked a named field and kept the key in data-internal-key (Record Bond pattern).
const rb = new HTMLInputElement('text', { name: 'booking_number' });
const rbForm = makeForm([rb]);
rb.value = ''; rb.dataset.internalKey = KEY;
out.restoredPost = nativeSubmit(rbForm);
// 2b. formdata listener alone (e.g. new FormData(form) in app code).
const fdOnly = new Map([['booking_number', '']]);
fire('formdata', { target: rbForm, formData: { get: (k) => fdOnly.get(k), set: (k, v) => fdOnly.set(k, v) } });
out.formdataOnly = fdOnly.get('booking_number');
// 3. Name-only input (no form owner) also counts as form-bound.
const named = new HTMLInputElement('text', { name: 'q' }); named.value = KEY;
out.namedRaw = named._v; out.namedMasked = named.hasAttribute('data-sl-masked');
// 4. Free-standing display input (no form, no name): shown blank, read returns key.
const text = new HTMLInputElement('text');
text.value = KEY;
out.textShown = text._v; out.textRead = text.value; out.textKept = text.dataset.internalKey;
// 5. Checkbox/radio/hidden values untouched (P1).
out.chkRead = chk.value; out.chkShown = chk._v; out.radioRead = radio.value; out.hiddenRead = hidden.value;
out.chkMasked = chk.hasAttribute('data-sl-masked');
const mixed = new HTMLTextAreaElement(); mixed.value = 'note ' + KEY + ' end'; out.mixed = mixed.value;
const plain = new HTMLInputElement('text'); plain.value = '2026-123456'; out.plain = plain.value;
const plainForm = new HTMLInputElement('text', { name: 'booking_number' }); plainForm.value = '2026-123456';
out.plainFormMasked = plainForm.hasAttribute('data-sl-masked');
// 6. User replaces the key in a form-bound field: unmasked, posts typed value.
bk._v = '24-0001'; fire('input', { target: bk });
out.afterTypeMasked = bk.hasAttribute('data-sl-masked'); out.afterTypePost = nativeSubmit(form).booking_number;
text._v = '24-0002'; fire('input', { target: text });
out.afterType = text.value; out.keyCleared = !text.dataset.internalKey;
out.label = window.slBookingLabel(KEY) + '|' + window.slBookingLabel('2026-123456');
out.css = head.children.some((n) => n.id === 'sl-booking-mask-css');
console.log(JSON.stringify(out));
