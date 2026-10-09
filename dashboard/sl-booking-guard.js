/* Booking-number display guard (2026-10-09, core/booking_identity.py).
 *
 * Miami-Dade publishes no booking number; its records are keyed on an
 * internal natural key (md_dedupe_v2:<sha256>). That key stays the record's
 * routing id in API data, but it is NEVER printed:
 *   - slBookingLabel(v): the booking number to print ("" for an internal key)
 *     — the JS twin of public_booking_number / booking_number_display.
 *   - slRedactKeys(text): text with any internal key removed (exports, toasts).
 *   - A MutationObserver blanks any key that still reaches page text and
 *     titles/placeholders; text-entry controls (text/search/tel/email/url,
 *     textarea) show it blank even on programmatic `.value =` while `.value`
 *     reads still return the routing id. Checkbox/radio/hidden values are
 *     routing data and never touched. Notifications/dialogs/clipboard too.
 * Load this before every other dashboard script on every page.
 */
(function () {
  'use strict';
  var KEY_RE = /md_dedupe_v\d+:[0-9a-f]{16,64}/g;
  var MARK = 'md_dedupe_v';

  function redact(text) {
    if (text === null || text === undefined) return text;
    var s = String(text);
    return s.indexOf(MARK) === -1 ? s : s.replace(KEY_RE, '');
  }
  function label(v) {
    if (v === null || v === undefined) return '';
    var s = String(v);
    return s.indexOf(MARK) !== -1 && /md_dedupe_v\d+:[0-9a-f]{16,}/.test(s) ? '' : s;
  }
  window.slRedactKeys = redact;
  window.slBookingLabel = label;

  var ATTRS = ['title', 'placeholder', 'aria-label', 'alt'];
  var KEY_ONLY_RE = /^\s*md_dedupe_v\d+:[0-9a-f]{16,64}\s*$/;
  // Only controls whose value is printed on screen. Checkbox/radio/hidden/
  // button values are routing data (e.g. Bulk Exonerate) and are never touched.
  var TEXT_TYPES = { '': 1, text: 1, search: 1, tel: 1, email: 1, url: 1 };
  function isTextEntry(el) {
    if (!el || el.nodeType !== 1) return false;
    if (el.tagName === 'TEXTAREA') return true;
    if (el.tagName !== 'INPUT') return false;
    return TEXT_TYPES[(el.getAttribute('type') || '').toLowerCase()] === 1;
  }

  // Programmatic `el.value = key` (no mutation event) on a text control:
  // the field shows blank, the key is kept in data-internal-key, and reading
  // `.value` while the field is still blank returns the key, so every
  // operational consumer keeps the routing id. Typing clears the hidden key.
  var nativeValue = {};
  function patchValue(Ctor) {
    if (typeof Ctor !== 'function' || !Ctor.prototype) return;
    var d = Object.getOwnPropertyDescriptor(Ctor.prototype, 'value');
    if (!d || !d.get || !d.set) return;
    nativeValue[Ctor.name || 'x'] = d;
    Object.defineProperty(Ctor.prototype, 'value', {
      configurable: true,
      enumerable: d.enumerable,
      get: function () {
        var v = d.get.call(this);
        if (v === '' && this.dataset && this.dataset.internalKey && isTextEntry(this)) return this.dataset.internalKey;
        return v;
      },
      set: function (v) {
        if (isTextEntry(this) && v !== null && v !== undefined && String(v).indexOf(MARK) !== -1) {
          var s = String(v);
          if (KEY_ONLY_RE.test(s)) {
            this.dataset.internalKey = s.trim();
            d.set.call(this, '');
          } else {
            d.set.call(this, redact(s));
          }
          return;
        }
        d.set.call(this, v);
      },
    });
  }
  if (typeof HTMLInputElement !== 'undefined') patchValue(HTMLInputElement);
  if (typeof HTMLTextAreaElement !== 'undefined') patchValue(HTMLTextAreaElement);
  function rawValue(el) {
    var d = nativeValue[el.tagName === 'TEXTAREA' ? 'HTMLTextAreaElement' : 'HTMLInputElement'];
    return d ? d.get.call(el) : el.value;
  }

  function scrubElement(el) {
    if (!el || el.nodeType !== 1) return;
    for (var i = 0; i < ATTRS.length; i++) {
      var a = el.getAttribute && el.getAttribute(ATTRS[i]);
      if (a && a.indexOf(MARK) !== -1) el.setAttribute(ATTRS[i], redact(a));
    }
    if (isTextEntry(el)) {
      var raw = rawValue(el);
      // Markup-rendered value="key": route through the patched setter.
      if (raw && raw.indexOf(MARK) !== -1) el.value = raw;
    }
  }
  function scrubTree(root) {
    if (!root) return;
    if (root.nodeType === 3) {
      if (root.nodeValue && root.nodeValue.indexOf(MARK) !== -1) root.nodeValue = redact(root.nodeValue);
      return;
    }
    if (root.nodeType !== 1 && root.nodeType !== 9 && root.nodeType !== 11) return;
    if (root.nodeType === 1) scrubElement(root);
    var doc = root.ownerDocument || root;
    if (!doc.createTreeWalker) return;
    var walker = doc.createTreeWalker(root, 1 | 4, null);
    var n;
    while ((n = walker.nextNode())) {
      if (n.nodeType === 3) {
        if (n.nodeValue && n.nodeValue.indexOf(MARK) !== -1) n.nodeValue = redact(n.nodeValue);
      } else {
        scrubElement(n);
      }
    }
  }
  window.slScrubBookingKeys = scrubTree;

  function start() {
    scrubTree(document.body || document.documentElement);
    document.addEventListener('focusin', function (e) { scrubElement(e.target); }, true);
    document.addEventListener('input', function (e) {
      var t = e.target;
      if (isTextEntry(t) && t.dataset && t.dataset.internalKey && rawValue(t) !== '') delete t.dataset.internalKey;
    }, true);
    if (typeof MutationObserver === 'undefined') return;
    new MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        var r = records[i];
        if (r.type === 'characterData') scrubTree(r.target);
        else if (r.type === 'attributes') scrubElement(r.target);
        else for (var j = 0; j < r.addedNodes.length; j++) scrubTree(r.addedNodes[j]);
      }
    }).observe(document.documentElement, {
      childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS,
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();

  // Desktop notifications and alert/confirm/prompt text are not DOM text.
  if (typeof window.Notification === 'function') {
    var N = window.Notification;
    var Wrapped = function (title, opts) {
      opts = opts ? Object.assign({}, opts) : opts;
      if (opts && opts.body) opts.body = redact(opts.body);
      return new N(redact(title), opts);
    };
    Wrapped.prototype = N.prototype;
    try {
      Object.defineProperty(Wrapped, 'permission', { get: function () { return N.permission; } });
    } catch (e) { /* ignore */ }
    Wrapped.requestPermission = function () { return N.requestPermission.apply(N, arguments); };
    window.Notification = Wrapped;
  }
  ['alert', 'confirm', 'prompt'].forEach(function (fn) {
    var orig = window[fn];
    if (typeof orig !== 'function') return;
    window[fn] = function (msg) {
      var args = Array.prototype.slice.call(arguments);
      args[0] = redact(msg);
      return orig.apply(window, args);
    };
  });
  // Clipboard copies of a key print it too.
  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    var cw = navigator.clipboard.writeText.bind(navigator.clipboard);
    navigator.clipboard.writeText = function (t) { return cw(redact(t)); };
  }
})();
