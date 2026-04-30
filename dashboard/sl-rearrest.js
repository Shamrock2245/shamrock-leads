/* ═══════════════════════════════════════════════════════
   ShamrockLeads — Repeat Offender Alert Module (sl-rearrest.js)
   ═══════════════════════════════════════════════════════
   Polls /api/rearrest/pending for unreviewed re-arrest alerts,
   renders them in the Command Center, and handles dismiss/contact actions.
   Human-in-the-Loop: No auto-messaging. Staff must manually trigger outreach.
*/

const SLRearrest = (() => {
  const POLL_INTERVAL = 30_000;  // 30s polling
  let _timer = null;
  let _alerts = [];
  let _loading = false;

  // ── Public API ──────────────────────────────────────────────────────────

  function init() {
    load();
    if (_timer) clearInterval(_timer);
    _timer = setInterval(load, POLL_INTERVAL);
  }

  async function load() {
    if (_loading) return;
    _loading = true;
    try {
      const r = await fetch(`${API}/api/rearrest/pending?limit=25`);
      const d = await r.json();
      if (d.success) {
        _alerts = d.alerts || [];
        render();
        updateBadge();
      }
    } catch (e) {
      console.debug('Rearrest poll error:', e);
    } finally {
      _loading = false;
    }
  }

  function render() {
    const panel = document.getElementById('rearrestAlertPanel');
    const body = document.getElementById('rearrestAlertBody');
    const countEl = document.getElementById('rearrestCount');
    if (!panel || !body) return;

    if (_alerts.length === 0) {
      panel.classList.remove('has-alerts');
      countEl.textContent = '0';
      body.innerHTML = `
        <div class="ra-empty">
          <span class="ra-empty-icon">✅</span>
          <span>No repeat offender alerts — all clear</span>
        </div>`;
      return;
    }

    panel.classList.add('has-alerts');
    countEl.textContent = _alerts.length;

    body.innerHTML = _alerts.map(a => {
      const bond = a.bond_amount || 0;
      const priorCount = a.prior_bonds_count || 1;
      const phone = a.indemnitor_phone || '';
      const phoneMask = phone ? `(***) ***-${phone.replace(/\D/g,'').slice(-4)}` : 'No phone';
      const indName = a.indemnitor_name || 'Unknown';
      const charges = (a.charges || '').slice(0, 100);
      const county = a.county || '';
      const defendant = a.defendant_name || 'Unknown';
      const timeStr = a.created_at ? _timeAgo(a.created_at) : '';
      const bondClass = bond >= 10000 ? 'ra-bond-high' : bond >= 2500 ? 'ra-bond-mid' : 'ra-bond-low';

      return `
        <div class="ra-card" id="ra-${a._id}" data-id="${a._id}">
          <div class="ra-pulse-dot"></div>
          <div class="ra-card-top">
            <div class="ra-defendant">
              <span class="ra-icon">🚨</span>
              <div>
                <div class="ra-name">${defendant}</div>
                <div class="ra-county">${county} County · ${timeStr}</div>
              </div>
            </div>
            <div class="ra-bond-pill ${bondClass}">$${bond.toLocaleString()}</div>
          </div>
          <div class="ra-charges">${charges || 'Charges not available'}</div>
          <div class="ra-indemnitor-row">
            <div class="ra-indem-info">
              <span class="ra-indem-label">Prior Indemnitor</span>
              <span class="ra-indem-name">${indName}</span>
              <span class="ra-indem-phone">${phoneMask}</span>
              <span class="ra-prior-count">${priorCount} prior bond${priorCount > 1 ? 's' : ''}</span>
            </div>
            <div class="ra-actions">
              <button class="ra-btn ra-btn-contact" onclick="SLRearrest.contact('${a._id}')" title="Mark as contacted">
                📞 Contact
              </button>
              <button class="ra-btn ra-btn-dismiss" onclick="SLRearrest.dismiss('${a._id}')" title="Dismiss alert">
                ✕
              </button>
            </div>
          </div>
        </div>`;
    }).join('');
  }

  async function dismiss(id) {
    const card = document.getElementById(`ra-${id}`);
    if (card) card.style.opacity = '0.4';

    try {
      const r = await fetch(`${API}/api/rearrest/${id}/dismiss`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reviewed_by: 'dashboard' }),
      });
      const d = await r.json();
      if (d.success) {
        _alerts = _alerts.filter(a => a._id !== id);
        render();
        updateBadge();
        toast('Alert dismissed', 'info');
      }
    } catch (e) {
      if (card) card.style.opacity = '1';
      toast('Dismiss failed', 'error');
    }
  }

  async function contact(id) {
    const alert = _alerts.find(a => a._id === id);
    if (!alert) return;

    const card = document.getElementById(`ra-${id}`);

    // Prompt for optional notes
    const notes = prompt(`Contact notes for ${alert.indemnitor_name || 'indemnitor'}:\n(leave blank to skip)`) || '';

    if (card) card.classList.add('ra-contacted');

    try {
      const r = await fetch(`${API}/api/rearrest/${id}/contacted`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ contacted_by: 'dashboard', notes }),
      });
      const d = await r.json();
      if (d.success) {
        _alerts = _alerts.filter(a => a._id !== id);
        render();
        updateBadge();
        toast(`Marked ${alert.indemnitor_name || 'indemnitor'} as contacted`, 'success');
      }
    } catch (e) {
      if (card) card.classList.remove('ra-contacted');
      toast('Contact update failed', 'error');
    }
  }

  // ── SSE handler (called from sl-core.js) ───────────────────────────────

  function onSSE(data) {
    // Immediately reload on SSE event
    load();
    // Play alert sound + toast
    toast(`🚨 Repeat offender: ${data.defendant_name || 'Unknown'} (${data.county || ''})`, 'info');
    _playRearrestAlert();
  }

  // ── Internal helpers ───────────────────────────────────────────────────

  function updateBadge() {
    // No tab badge to update for Command Center sub-panel,
    // but update the count display in the panel header
    const countEl = document.getElementById('rearrestCount');
    if (countEl) countEl.textContent = _alerts.length;
  }

  function _timeAgo(iso) {
    if (!iso) return '';
    const d = (Date.now() - new Date(iso).getTime()) / 1000;
    if (d < 60) return Math.round(d) + 's ago';
    if (d < 3600) return Math.round(d / 60) + 'm ago';
    if (d < 86400) return Math.round(d / 3600) + 'h ago';
    return Math.round(d / 86400) + 'd ago';
  }

  function _playRearrestAlert() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain); gain.connect(ctx.destination);
      // Two-tone urgent alert: low-high-low
      osc.frequency.setValueAtTime(660, ctx.currentTime);
      osc.frequency.setValueAtTime(880, ctx.currentTime + 0.15);
      osc.frequency.setValueAtTime(660, ctx.currentTime + 0.3);
      gain.gain.setValueAtTime(0.2, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.5);
      osc.start(ctx.currentTime); osc.stop(ctx.currentTime + 0.5);
    } catch (e) {}
  }

  // ── Expose public API ──────────────────────────────────────────────────
  return { init, load, render, dismiss, contact, onSSE };
})();

// Auto-init when DOM ready (Command Center is default tab)
document.addEventListener('DOMContentLoaded', () => SLRearrest.init());
