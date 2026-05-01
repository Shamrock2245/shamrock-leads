/* ShamrockLeads — Bond Lifecycle Timeline Panel
 * Opens from any tab via SLLifecycle.open(bookingNumber)
 * Shows the complete bond journey: Arrest → Contact → Negotiate → Paperwork → Bond → Court → Discharge
 * ─────────────────────────────────────────────────────────────────────────── */
const SLLifecycle = (() => {
  'use strict';

  let _currentBooking = null;
  let _sectionStates = {};  // track collapsed sections

  // ── DOM refs ──────────────────────────────────────────────────────────────
  const $ = id => document.getElementById(id);

  function _panel()   { return $('lifecyclePanel'); }
  function _overlay() { return $('lifecyclePanelOverlay'); }

  // ── Public: open panel for a booking number ───────────────────────────────
  async function open(bookingNumber, opts = {}) {
    if (!bookingNumber) return;
    _currentBooking = bookingNumber;

    const panel = _panel();
    const overlay = _overlay();
    if (!panel) { _injectDOM(); }

    // Show panel immediately with skeleton
    _panel().classList.add('open');
    _overlay().classList.add('open');
    document.body.style.overflow = 'hidden';

    _renderSkeleton(opts.defendantName || bookingNumber);
    await _loadData(bookingNumber);
  }

  // ── Public: close panel ───────────────────────────────────────────────────
  function close() {
    const panel = _panel();
    const overlay = _overlay();
    if (panel) panel.classList.remove('open');
    if (overlay) overlay.classList.remove('open');
    document.body.style.overflow = '';
    _currentBooking = null;
  }

  // ── Inject DOM if not present ─────────────────────────────────────────────
  function _injectDOM() {
    if ($('lifecyclePanel')) return;
    const html = `
      <div id="lifecyclePanelOverlay" onclick="SLLifecycle.close()"></div>
      <div id="lifecyclePanel" role="dialog" aria-modal="true" aria-label="Bond Lifecycle Timeline">
        <div class="lcp-header">
          <div class="lcp-title" id="lcpTitle">☘️ Bond Lifecycle</div>
          <div class="lcp-subtitle" id="lcpSubtitle">Loading timeline...</div>
          <button class="lcp-close" onclick="SLLifecycle.close()" aria-label="Close">✕</button>
        </div>
        <div class="lcp-meta-strip" id="lcpMetaStrip"></div>
        <div class="lcp-stage-bar" id="lcpStageBar"></div>
        <div class="lcp-body" id="lcpBody"></div>
        <div class="lcp-actions" id="lcpActions"></div>
      </div>`;
    document.body.insertAdjacentHTML('beforeend', html);
    // Close on Escape
    document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
  }

  // ── Render skeleton while loading ─────────────────────────────────────────
  function _renderSkeleton(label) {
    const title = $('lcpTitle');
    const subtitle = $('lcpSubtitle');
    if (title) title.textContent = `☘️ ${label}`;
    if (subtitle) subtitle.textContent = 'Loading timeline...';

    const body = $('lcpBody');
    if (body) body.innerHTML = `
      <div class="lcp-skeleton">
        ${Array(8).fill(0).map((_, i) => `
          <div class="lcp-skeleton-line" style="width:${70 + (i % 3) * 10}%;opacity:${1 - i * 0.08}"></div>
        `).join('')}
      </div>`;

    const meta = $('lcpMetaStrip');
    if (meta) meta.innerHTML = `
      <div class="lcp-meta-cell"><div class="lcp-meta-label">Bond</div><div class="lcp-meta-value">—</div></div>
      <div class="lcp-meta-cell"><div class="lcp-meta-label">County</div><div class="lcp-meta-value">—</div></div>
      <div class="lcp-meta-cell"><div class="lcp-meta-label">Stage</div><div class="lcp-meta-value">—</div></div>`;

    const stages = $('lcpStageBar');
    if (stages) stages.innerHTML = '';
    const actions = $('lcpActions');
    if (actions) actions.innerHTML = '';
  }

  // ── Fetch data from API ───────────────────────────────────────────────────
  async function _loadData(bookingNumber) {
    try {
      const res = await fetch(`/api/lifecycle/${encodeURIComponent(bookingNumber)}`);
      const data = await res.json();
      if (!data.ok) throw new Error(data.error || 'API error');
      _render(data);
    } catch (err) {
      const body = $('lcpBody');
      if (body) body.innerHTML = `
        <div style="padding:32px 24px;text-align:center;color:var(--muted)">
          <div style="font-size:32px;margin-bottom:12px">⚠️</div>
          <div style="font-size:14px;font-weight:600;color:var(--text)">Could not load timeline</div>
          <div style="font-size:12px;margin-top:6px">${err.message}</div>
          <button onclick="SLLifecycle._loadData('${bookingNumber}')"
            style="margin-top:16px;padding:8px 20px;border-radius:8px;background:rgba(16,185,129,.1);
            border:1px solid rgba(16,185,129,.3);color:var(--accent-light);cursor:pointer;font-size:12px">
            ↻ Retry
          </button>
        </div>`;
    }
  }

  // ── Render full timeline ──────────────────────────────────────────────────
  function _render(data) {
    const { meta, stages, events } = data;

    // Title
    const title = $('lcpTitle');
    if (title) title.innerHTML = `☘️ ${_esc(meta.defendant_name)}`;
    const subtitle = $('lcpSubtitle');
    if (subtitle) subtitle.textContent = `Booking #${meta.booking_number} · ${meta.county} County`;

    // Meta strip
    const metaStrip = $('lcpMetaStrip');
    if (metaStrip) metaStrip.innerHTML = `
      <div class="lcp-meta-cell">
        <div class="lcp-meta-label">Bond Amount</div>
        <div class="lcp-meta-value">$${Number(meta.bond_amount || 0).toLocaleString()}</div>
      </div>
      <div class="lcp-meta-cell">
        <div class="lcp-meta-label">County</div>
        <div class="lcp-meta-value">${_esc(meta.county)}</div>
      </div>
      <div class="lcp-meta-cell">
        <div class="lcp-meta-label">Current Stage</div>
        <div class="lcp-meta-value" style="text-transform:capitalize">${_esc(meta.current_stage)}</div>
      </div>`;

    // Stage progress bar
    const stageBar = $('lcpStageBar');
    if (stageBar) {
      stageBar.innerHTML = stages.map((s, i) => `
        <div class="lcp-stage-step">
          <div class="lcp-stage-row">
            <div class="lcp-stage-dot ${s.status}" title="${s.label}">${s.icon}</div>
            ${i < stages.length - 1 ? `<div class="lcp-stage-connector ${s.status === 'done' ? 'done' : ''}"></div>` : ''}
          </div>
          <div class="lcp-stage-label">${s.label}</div>
        </div>`).join('');
    }

    // Body — group events into sections
    const body = $('lcpBody');
    if (!body) return;

    if (!events || events.length === 0) {
      body.innerHTML = `
        <div style="padding:40px 24px;text-align:center;color:var(--muted)">
          <div style="font-size:32px;margin-bottom:12px">📭</div>
          <div style="font-size:14px">No timeline events found yet.</div>
          <div style="font-size:12px;margin-top:6px">Events will appear as the bond progresses through its lifecycle.</div>
        </div>`;
    } else {
      // Group by type
      const groups = {
        'Bond Events': events.filter(e => ['arrest','bond','discharge'].includes(e.type)),
        'Communications': events.filter(e => ['contact','message'].includes(e.type)),
        'Court Dates': events.filter(e => e.type === 'court'),
        'Payments': events.filter(e => e.type === 'payment'),
        'Notes & Alerts': events.filter(e => ['note','alert'].includes(e.type)),
        'All Events': events,
      };

      const sections = Object.entries(groups)
        .filter(([, evts]) => evts.length > 0)
        .filter(([label]) => label !== 'All Events' || events.length <= 5);

      // If only a few events, just show All Events
      const toRender = sections.length > 1
        ? sections.filter(([label]) => label !== 'All Events')
        : [['All Events', events]];

      body.innerHTML = toRender.map(([label, evts]) => {
        const key = label.replace(/\s+/g, '_');
        const isCollapsed = _sectionStates[key] === true;
        return `
          <div class="lcp-section">
            <div class="lcp-section-header" onclick="SLLifecycle._toggleSection('${key}')">
              <span>${label} <span style="opacity:.5;font-weight:400">(${evts.length})</span></span>
              <span id="lcpChevron_${key}">${isCollapsed ? '▶' : '▼'}</span>
            </div>
            <div class="lcp-section-body ${isCollapsed ? 'collapsed' : ''}" id="lcpSection_${key}">
              <div class="lcp-timeline">
                ${evts.map(e => _renderEvent(e)).join('')}
              </div>
            </div>
          </div>`;
      }).join('');
    }

    // Actions
    const actions = $('lcpActions');
    if (actions) {
      const hasActiveBond = meta.has_active_bond;
      const hasArrest = meta.has_arrest_record;
      const hasPipeline = meta.has_pipeline_entry;
      actions.innerHTML = `
        ${hasArrest ? `<button class="lcp-action-btn" onclick="SLLifecycle._goToDefendants('${meta.booking_number}')">👤 Defendants</button>` : ''}
        ${hasPipeline ? `<button class="lcp-action-btn" onclick="SLLifecycle._goToOutreach('${meta.booking_number}')">📋 Outreach</button>` : ''}
        ${hasActiveBond ? `<button class="lcp-action-btn" onclick="SLLifecycle._goToActiveBonds('${meta.booking_number}')">🔒 Active Bond</button>` : ''}
        ${hasActiveBond ? `<button class="lcp-action-btn" onclick="SLLifecycle._goToTracking('${meta.booking_number}')">📍 Tracking</button>` : ''}
        <button class="lcp-action-btn primary" onclick="SLLifecycle._addNote('${meta.booking_number}')">📝 Add Note</button>
        ${hasActiveBond && meta.current_stage !== 'discharged' ? `<button class="lcp-action-btn danger" onclick="SLLifecycle._exonerate('${meta.booking_number}')">✅ Exonerate</button>` : ''}`;
    }
  }

  // ── Render a single timeline event ───────────────────────────────────────
  function _renderEvent(e) {
    const badge = e.badge
      ? `<span class="lcp-event-badge ${e.badge.class}">${_esc(e.badge.text)}</span>`
      : '';
    const timeStr = e.timestamp ? _fmtTime(e.timestamp) : '';
    return `
      <div class="lcp-event">
        <div class="lcp-event-icon ${e.icon_class || 'note'}">${e.icon || '📋'}</div>
        <div class="lcp-event-content">
          <div class="lcp-event-title">${_esc(e.title)}${badge}</div>
          ${e.detail ? `<div class="lcp-event-detail">${_esc(e.detail)}</div>` : ''}
          ${timeStr ? `<div class="lcp-event-time">${timeStr}</div>` : ''}
        </div>
      </div>`;
  }

  // ── Toggle section collapse ───────────────────────────────────────────────
  function _toggleSection(key) {
    const body = $(`lcpSection_${key}`);
    const chevron = $(`lcpChevron_${key}`);
    if (!body) return;
    const collapsed = body.classList.toggle('collapsed');
    _sectionStates[key] = collapsed;
    if (chevron) chevron.textContent = collapsed ? '▶' : '▼';
  }

  // ── Cross-tab navigation ──────────────────────────────────────────────────
  function _goToDefendants(bk) {
    close();
    const tab = document.querySelector('[data-tab="tabDefendants"]') ||
                document.querySelector('.tab-btn[onclick*="Defendants"]');
    if (tab) tab.click();
    setTimeout(() => {
      const s = document.getElementById('defSearch') || document.getElementById('searchInput');
      if (s) { s.value = bk; s.dispatchEvent(new Event('input')); }
    }, 400);
  }

  function _goToOutreach(bk) {
    close();
    const tab = document.querySelector('[data-tab="tabProspective"]') ||
                document.querySelector('.tab-btn[onclick*="Prospective"]');
    if (tab) tab.click();
    setTimeout(() => {
      const s = document.getElementById('pipelineSearch');
      if (s) { s.value = bk; s.dispatchEvent(new Event('input')); }
    }, 400);
  }

  function _goToActiveBonds(bk) {
    close();
    const tab = document.querySelector('[data-tab="tabActiveBonds"]') ||
                document.querySelector('.tab-btn[onclick*="ActiveBonds"]');
    if (tab) tab.click();
    setTimeout(() => {
      const s = document.getElementById('abSearch');
      if (s) { s.value = bk; s.dispatchEvent(new Event('input')); }
    }, 400);
  }

  function _goToTracking(bk) {
    close();
    const tab = document.querySelector('[data-tab="tabTracking"]') ||
                document.querySelector('.tab-btn[onclick*="Tracking"]');
    if (tab) tab.click();
    setTimeout(() => {
      const s = document.getElementById('trkSearch');
      if (s) { s.value = bk; s.dispatchEvent(new Event('input')); }
    }, 400);
  }

  // ── Add note inline ───────────────────────────────────────────────────────
  async function _addNote(bk) {
    const text = prompt('Add a note to this bond\'s lifecycle:');
    if (!text || !text.trim()) return;
    try {
      const res = await fetch(`/api/lifecycle/notes/${encodeURIComponent(bk)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note: text.trim(), source: 'lifecycle_panel' }),
      });
      const d = await res.json();
      if (d.ok || d.success) {
        if (window.SL) SL.toast('Note saved ✅', 'success');
        await _loadData(bk);
      } else {
        if (window.SL) SL.toast('Could not save note', 'error');
      }
    } catch (e) {
      if (window.SL) SL.toast('Error: ' + e.message, 'error');
    }
  }

  // ── Exonerate from lifecycle panel ───────────────────────────────────────
  async function _exonerate(bk) {
    const reason = prompt(
      `Exonerate bond ${bk}?\n\nThis will:\n• Stop all location tracking\n• Cancel court reminders\n• Mark bond as discharged\n\nEnter reason (or Cancel to abort):`
    );
    if (!reason) return;
    try {
      const res = await fetch(`/api/tracking/${encodeURIComponent(bk)}/exonerate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason, source: 'lifecycle_panel' }),
      });
      const d = await res.json();
      if (d.ok || d.success) {
        if (window.SL) SL.toast(`✅ Bond ${bk} exonerated — tracking stopped`, 'success');
        if (window.SLTracking) SLTracking.onBondExonerated({ booking_number: bk });
        await _loadData(bk);
      } else {
        if (window.SL) SL.toast(d.error || 'Exoneration failed', 'error');
      }
    } catch (e) {
      if (window.SL) SL.toast('Error: ' + e.message, 'error');
    }
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function _esc(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function _fmtTime(ts) {
    if (!ts) return '';
    try {
      const d = new Date(ts);
      if (isNaN(d)) return ts;
      const now = new Date();
      const diff = (now - d) / 1000;
      if (diff < 60) return 'Just now';
      if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
      if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
      if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
      return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    } catch { return ts; }
  }

  // ── Init: inject DOM on load ──────────────────────────────────────────────
  function init() {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', _injectDOM);
    } else {
      _injectDOM();
    }
  }

  init();

  return {
    open,
    close,
    _toggleSection,
    _loadData,
    _goToDefendants,
    _goToOutreach,
    _goToActiveBonds,
    _goToTracking,
    _addNote,
    _exonerate,
  };
})();
