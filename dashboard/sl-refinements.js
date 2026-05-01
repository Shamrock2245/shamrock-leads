/**
 * sl-refinements.js — Antigravity Refinement Checklist (Frontend Gaps)
 * Covers:
 *   R1. crNextDue locale-aware date formatting + auto-collapse details after scan
 *   R2. has_indemnitor checkbox: tooltip + tab-switch persistence via sessionStorage
 *   R3. openIndemInDefendants: special-char safe name/phone, hover style, focus
 *   R4. Mobile filter bar: horizontal scroll + touch-target fix for Defendants tab
 *
 * Loaded after sl-active-bonds-ext.js and sl-features.js.
 * All patches are additive — no existing functions are removed.
 */
(function () {
  'use strict';

  /* ══════════════════════════════════════════════════════════════════════════
   * R1 — crNextDue locale-aware formatting + auto-collapse scan details
   * ══════════════════════════════════════════════════════════════════════════ */

  /**
   * Patch loadReminderStatus to use locale-aware date/time formatting
   * and add a "Last scanned" timestamp below the crNextDue element.
   */
  function _patchLoadReminderStatus() {
    // We monkey-patch by wrapping the SSE/status update that writes to crNextDue.
    // The original function writes to #crNextDue; we observe it and reformat.
    var _origInnerHTML = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
    if (!_origInnerHTML) return;

    // Simpler approach: override the crNextDue element's innerHTML setter once DOM is ready
    var _patched = false;
    function _applyNextDuePatch() {
      if (_patched) return;
      var el = document.getElementById('crNextDue');
      if (!el) return;
      _patched = true;

      // Intercept writes to crNextDue and reformat any ISO date strings found
      var _desc = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
      Object.defineProperty(el, 'innerHTML', {
        get: function () { return _desc.get.call(this); },
        set: function (val) {
          // Replace any raw ISO date-like strings with locale-formatted versions
          var formatted = val.replace(
            /(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)/g,
            function (iso) {
              try {
                var d = new Date(iso);
                if (isNaN(d)) return iso;
                return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }) +
                  ' at ' + d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' });
              } catch (e) { return iso; }
            }
          );
          _desc.set.call(this, formatted);
          // Update last-scan timestamp
          _updateLastScanTime();
        },
        configurable: true
      });
    }

    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', _applyNextDuePatch);
    } else {
      _applyNextDuePatch();
    }
  }

  /** Insert / update a "Last scanned: X" line below crNextDue */
  function _updateLastScanTime() {
    var parent = document.getElementById('crNextDue');
    if (!parent) return;
    var ts = document.getElementById('crLastScanTs');
    if (!ts) {
      ts = document.createElement('div');
      ts.id = 'crLastScanTs';
      ts.style.cssText = 'font-size:10px;color:var(--muted);margin-top:2px;padding:0 16px 4px';
      parent.insertAdjacentElement('afterend', ts);
    }
    var now = new Date();
    ts.textContent = 'Last scanned: ' + now.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', second: '2-digit' });
  }

  /**
   * Auto-collapse the scan details <details> element 8 seconds after a scan
   * completes, so the panel doesn't stay expanded indefinitely.
   */
  function _patchAutoCollapseScanDetails() {
    // Observe #crScanResult for DOM mutations (new <details> elements)
    var target = document.getElementById('crScanResult');
    if (!target) return;
    var observer = new MutationObserver(function (mutations) {
      mutations.forEach(function (m) {
        if (m.type === 'childList') {
          var details = target.querySelector('details');
          if (details && !details.dataset.autoCollapseSet) {
            details.dataset.autoCollapseSet = '1';
            setTimeout(function () {
              if (details.open) {
                details.open = false;
              }
            }, 8000); // auto-collapse after 8 seconds
          }
        }
      });
    });
    observer.observe(target, { childList: true, subtree: true });
  }

  /* ══════════════════════════════════════════════════════════════════════════
   * R2 — has_indemnitor checkbox: tooltip + sessionStorage tab-switch persist
   * ══════════════════════════════════════════════════════════════════════════ */

  function _patchHasIndemnitorCheckbox() {
    var cb = document.getElementById('defHasIndemnitor');
    if (!cb) return;

    // Add tooltip
    var label = cb.closest('label');
    if (label && !label.title) {
      label.title = 'Show only defendants who have at least one indemnitor on file';
    }

    // Restore persisted state from sessionStorage
    var stored = sessionStorage.getItem('sl_hasIndem');
    if (stored === 'true' && !cb.checked) {
      cb.checked = true;
      // Trigger the loadDefendants call if SL is available
      if (window.SL && typeof SL.loadDefendants === 'function') {
        SL.loadDefendants();
      }
    }

    // Persist state on change
    cb.addEventListener('change', function () {
      sessionStorage.setItem('sl_hasIndem', cb.checked ? 'true' : 'false');
    });

    // When navigating away from Defendants tab and back, restore state
    document.addEventListener('click', function (e) {
      var btn = e.target.closest('[data-tab]');
      if (btn && btn.dataset.tab === 'tabDefendants') {
        var s = sessionStorage.getItem('sl_hasIndem');
        if (s === 'true' && !cb.checked) {
          cb.checked = true;
        } else if (s === 'false' && cb.checked) {
          cb.checked = false;
        }
      }
    });
  }

  /* ══════════════════════════════════════════════════════════════════════════
   * R3 — openIndemInDefendants: special-char safe + hover style + focus
   * ══════════════════════════════════════════════════════════════════════════ */

  /**
   * Overwrite the openIndemInDefendants function on SLActiveBonds with a
   * version that:
   *   - Strips/escapes special chars from name before setting search value
   *   - Adds a brief visual highlight to the search input after navigation
   *   - Focuses the search input so the user can immediately refine
   */
  function _patchOpenIndemInDefendants() {
    if (!window.SLActiveBonds) return;

    window.SLActiveBonds.openIndemInDefendants = function (indemnitorName, indemnitorPhone) {
      // Sanitize: remove characters that could break querySelector or confuse search
      var safeName = (indemnitorName || '').replace(/[<>"'`\\]/g, '').trim();
      var safePhone = (indemnitorPhone || '').replace(/[^0-9+\-(). ]/g, '').trim();
      var searchVal = safePhone || safeName;

      var defBtn = document.querySelector('[data-tab="tabDefendants"]');
      if (!defBtn) {
        if (window.toast) toast('Defendants tab not found', 'error');
        return;
      }
      defBtn.click();

      setTimeout(function () {
        var searchEl = document.getElementById('defSearch') || document.getElementById('defendantSearch');
        if (searchEl) {
          searchEl.value = searchVal;
          // Visual highlight
          searchEl.style.transition = 'box-shadow 0.3s';
          searchEl.style.boxShadow = '0 0 0 3px rgba(0, 210, 106, 0.45)';
          setTimeout(function () { searchEl.style.boxShadow = ''; }, 1800);
          // Focus
          searchEl.focus();
          // Dispatch input event to trigger search
          searchEl.dispatchEvent(new Event('input', { bubbles: true }));
        }
      }, 320);
    };
  }

  /* ══════════════════════════════════════════════════════════════════════════
   * R4 — Mobile filter bar: horizontal scroll + touch-target fix
   * ══════════════════════════════════════════════════════════════════════════ */

  function _patchMobileFilterBar() {
    // Inject CSS for mobile filter bar horizontal scroll and touch targets
    var style = document.createElement('style');
    style.id = 'sl-refinements-mobile';
    style.textContent = [
      /* Defendants filter bar — horizontal scroll on small screens */
      '@media (max-width: 768px) {',
      '  .def-filters {',
      '    display: flex !important;',
      '    flex-wrap: nowrap !important;',
      '    overflow-x: auto !important;',
      '    -webkit-overflow-scrolling: touch !important;',
      '    gap: 6px !important;',
      '    padding-bottom: 6px !important;',
      '    scrollbar-width: none !important;',
      '  }',
      '  .def-filters::-webkit-scrollbar { display: none !important; }',
      '  .def-filters button, .def-filters label, .def-filters select {',
      '    flex-shrink: 0 !important;',
      '    min-height: 40px !important;',
      '    min-width: 44px !important;',
      '    touch-action: manipulation !important;',
      '    -webkit-tap-highlight-color: transparent !important;',
      '  }',
      /* Active Bonds toolbar — same treatment */
      '  .ab-toolbar {',
      '    display: flex !important;',
      '    flex-wrap: nowrap !important;',
      '    overflow-x: auto !important;',
      '    -webkit-overflow-scrolling: touch !important;',
      '    gap: 6px !important;',
      '    padding-bottom: 4px !important;',
      '    scrollbar-width: none !important;',
      '  }',
      '  .ab-toolbar::-webkit-scrollbar { display: none !important; }',
      '  .ab-toolbar button, .ab-toolbar select {',
      '    flex-shrink: 0 !important;',
      '    min-height: 40px !important;',
      '    touch-action: manipulation !important;',
      '    -webkit-tap-highlight-color: transparent !important;',
      '  }',
      /* crNextDue last-scan timestamp */
      '  #crLastScanTs { padding: 0 8px 4px !important; }',
      '}',
      /* Indemnitor cross-link button hover style */
      '.indem-crosslink-btn {',
      '  background: none;',
      '  border: none;',
      '  color: var(--accent, #00d26a);',
      '  cursor: pointer;',
      '  font-size: 11px;',
      '  padding: 2px 6px;',
      '  border-radius: 4px;',
      '  transition: background 0.15s, color 0.15s;',
      '  touch-action: manipulation;',
      '  -webkit-tap-highlight-color: transparent;',
      '}',
      '.indem-crosslink-btn:hover, .indem-crosslink-btn:focus {',
      '  background: rgba(0, 210, 106, 0.12);',
      '  color: var(--accent, #00d26a);',
      '  outline: none;',
      '}',
    ].join('\n');
    document.head.appendChild(style);
  }

  /* ══════════════════════════════════════════════════════════════════════════
   * R5 — Bulk Exonerate modal: send X-Admin-Token header from sessionStorage
   * ══════════════════════════════════════════════════════════════════════════ */

  /**
   * Patch submitBulkExonerate on SLActiveBonds to include X-Admin-Token header
   * if a PIN was stored in sessionStorage (set by pin_auth.py on login).
   */
  function _patchBulkExonerateAuth() {
    if (!window.SLActiveBonds || typeof SLActiveBonds.submitBulkExonerate !== 'function') return;
    var _orig = SLActiveBonds.submitBulkExonerate;
    SLActiveBonds.submitBulkExonerate = async function () {
      // Temporarily patch fetch to inject the header for this one call
      var _origFetch = window.fetch;
      window.fetch = function (url, opts) {
        if (typeof url === 'string' && url.includes('bulk-exonerate')) {
          opts = opts || {};
          opts.headers = Object.assign({}, opts.headers || {});
          var pin = sessionStorage.getItem('sl_admin_token') || '';
          if (pin) opts.headers['X-Admin-Token'] = pin;
        }
        return _origFetch.apply(this, arguments);
      };
      try {
        return await _orig.apply(this, arguments);
      } finally {
        window.fetch = _origFetch;
      }
    };
  }

  /* ══════════════════════════════════════════════════════════════════════════
   * Init — run all patches after DOM is ready
   * ══════════════════════════════════════════════════════════════════════════ */

  function _init() {
    _patchLoadReminderStatus();
    _patchAutoCollapseScanDetails();
    _patchHasIndemnitorCheckbox();
    _patchOpenIndemInDefendants();
    _patchMobileFilterBar();
    _patchBulkExonerateAuth();
    console.log('[sl-refinements] ✅ All refinement patches applied');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _init);
  } else {
    // DOM already ready — defer one tick to let other scripts finish
    setTimeout(_init, 0);
  }

})();
