/**
 * sl-fta.js — FTA Alert Center
 * ShamrockLeads Dashboard
 *
 * Displays open Failure-to-Appear alerts with KPIs, sortable table,
 * resolve/escalate actions, and a manual scan trigger.
 */

const SLFTA = (() => {
  // ─────────────────────────────────────────────────────────────────────────
  // State
  // ─────────────────────────────────────────────────────────────────────────
  let _loaded = false;

  // ─────────────────────────────────────────────────────────────────────────
  // Public API
  // ─────────────────────────────────────────────────────────────────────────
  async function load() {
    const levelFilter = (document.getElementById('ftaFilterLevel') || {}).value || '';
    await _fetchAndRender(levelFilter);
  }

  async function runScan() {
    const btn = document.querySelector('[onclick="SLFTA.runScan()"]');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Scanning…'; }
    try {
      const res = await fetch('/api/fta/scan', { method: 'POST' });
      const data = await res.json();
      if (data.success) {
        _toast(`FTA scan complete — ${data.fta_detected || 0} new FTAs detected`, 'success');
        await load();
      } else {
        _toast('Scan failed: ' + (data.error || 'unknown error'), 'error');
      }
    } catch (e) {
      _toast('Scan request failed: ' + e.message, 'error');
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = '⚡ Run FTA Scan Now'; }
    }
  }

  async function resolve(bookingNumber) {
    const resolution = await _promptResolution();
    if (!resolution) return;
    try {
      const res = await fetch(`/api/fta/${encodeURIComponent(bookingNumber)}/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ resolution }),
      });
      const data = await res.json();
      if (data.success) {
        _toast(`FTA resolved for ${bookingNumber}`, 'success');
        await load();
      } else {
        _toast('Resolve failed: ' + (data.error || 'unknown'), 'error');
      }
    } catch (e) {
      _toast('Request failed: ' + e.message, 'error');
    }
  }

  async function sendGeoLink(bookingNumber, phone) {
    if (!phone) { _toast('No phone number on file', 'error'); return; }
    try {
      const res = await fetch(`/tracking/${encodeURIComponent(bookingNumber)}/send-geo-link`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone, recipient: 'defendant_fta' }),
      });
      const data = await res.json();
      if (data.success) {
        _toast(`Geolocator link sent via ${data.channel || 'iMessage'}`, 'success');
      } else {
        _toast('Send failed: ' + (data.error || 'unknown'), 'error');
      }
    } catch (e) {
      _toast('Request failed: ' + e.message, 'error');
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Internal
  // ─────────────────────────────────────────────────────────────────────────
  async function _fetchAndRender(levelFilter) {
    const tbody = document.getElementById('ftaTableBody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:32px;color:var(--muted)">Loading…</td></tr>';

    try {
      const params = new URLSearchParams();
      if (levelFilter) params.set('level', levelFilter);
      const res = await fetch('/api/fta/open?' + params.toString());
      const data = await res.json();

      if (!data.success) throw new Error(data.error || 'API error');

      const ftas = data.ftas || [];
      const stats = data.stats || {};

      // KPIs
      _setText('ftaKpiOpen', ftas.length);
      _setText('ftaKpiLevel3', stats.level3 || 0);
      _setText('ftaKpiResolved', stats.resolved_30d || 0);
      _setText('ftaKpiExposure', _fmt$(stats.exposure_at_risk || 0));

      // Badge
      const badge = document.getElementById('ftaBadge');
      if (badge) {
        badge.textContent = ftas.length || '';
        badge.style.display = ftas.length ? 'inline-flex' : 'none';
      }

      // Table
      if (!ftas.length) {
        tbody.innerHTML = '<tr><td colspan="9" style="text-align:center;padding:32px;color:var(--muted)">No open FTA alerts 🎉</td></tr>';
        return;
      }

      tbody.innerHTML = ftas.map(f => _buildRow(f)).join('');

    } catch (e) {
      tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;padding:32px;color:var(--danger)">Error: ${e.message}</td></tr>`;
    }
  }

  function _buildRow(f) {
    const level = f.escalation_level || 1;
    const levelColors = { 1: '#f59e0b', 2: '#ef4444', 3: '#dc2626' };
    const levelLabels = { 1: '⚠️ L1', 2: '🔴 L2', 3: '🆘 L3' };
    const color = levelColors[level] || '#f59e0b';
    const label = levelLabels[level] || `L${level}`;

    const hoursPast = f.hours_past ? Math.round(f.hours_past) : '—';
    const detected = f.detected_at ? new Date(f.detected_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
    const courtDate = f.court_date ? new Date(f.court_date).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : '—';
    const bondAmt = f.bond_amount ? _fmt$(f.bond_amount) : '—';
    const phone = f.defendant_phone || f.indemnitor_phone || '';
    const surrenderBadge = f.surrender_flagged ? ' <span style="background:#dc2626;color:#fff;font-size:10px;padding:2px 6px;border-radius:4px;margin-left:4px">SURRENDER</span>' : '';

    return `
      <tr style="border-bottom:1px solid var(--border);transition:background .15s" onmouseover="this.style.background='rgba(255,255,255,0.03)'" onmouseout="this.style.background=''">
        <td style="padding:10px 12px">
          <span style="background:${color};color:#fff;font-size:11px;font-weight:700;padding:3px 8px;border-radius:4px">${label}</span>
          ${surrenderBadge}
        </td>
        <td style="padding:10px 12px;font-weight:600">${_esc(f.defendant_name || '—')}</td>
        <td style="padding:10px 12px;font-family:monospace;font-size:12px">${_esc(f.booking_number || '—')}</td>
        <td style="padding:10px 12px;color:#10b981;font-weight:600">${bondAmt}</td>
        <td style="padding:10px 12px">${courtDate}</td>
        <td style="padding:10px 12px;color:${color};font-weight:600">${hoursPast}h</td>
        <td style="padding:10px 12px">${_esc(f.county || '—')}</td>
        <td style="padding:10px 12px;font-size:11px;color:var(--muted)">${detected}</td>
        <td style="padding:10px 12px">
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            <button onclick="SLFTA.resolve('${_esc(f.booking_number)}')"
              style="padding:4px 10px;background:var(--accent);border:none;border-radius:4px;color:#fff;font-size:11px;cursor:pointer;font-weight:600">
              ✅ Resolve
            </button>
            <button onclick="SLFTA.sendGeoLink('${_esc(f.booking_number)}','${_esc(phone)}')"
              style="padding:4px 10px;background:var(--panel);border:1px solid var(--border);border-radius:4px;color:var(--text);font-size:11px;cursor:pointer">
              📍 Geo Link
            </button>
          </div>
        </td>
      </tr>`;
  }

  async function _promptResolution() {
    return new Promise(resolve => {
      // Build inline modal
      const overlay = document.createElement('div');
      overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:20000;display:flex;align-items:center;justify-content:center';
      overlay.innerHTML = `
        <div style="background:var(--panel,#1e293b);border:1px solid var(--border,#334155);border-radius:14px;padding:24px;width:360px;max-width:90vw">
          <h3 style="margin:0 0 16px;font-size:1rem">Resolve FTA Alert</h3>
          <select id="_ftaResolutionSel" style="width:100%;padding:10px;border-radius:8px;border:1px solid var(--border);background:var(--bg-main,#0f172a);color:var(--text);font-size:0.9rem;margin-bottom:16px">
            <option value="appeared">Defendant appeared in court</option>
            <option value="warrant_recalled">Warrant recalled</option>
            <option value="surrendered">Defendant surrendered</option>
            <option value="bond_reinstated">Bond reinstated by court</option>
            <option value="other">Other</option>
          </select>
          <div style="display:flex;gap:8px">
            <button id="_ftaResolveConfirm" style="flex:1;padding:10px;background:var(--accent);border:none;border-radius:8px;color:#fff;font-weight:600;cursor:pointer">Confirm</button>
            <button id="_ftaResolveCancel" style="flex:1;padding:10px;background:var(--border);border:none;border-radius:8px;color:var(--text);cursor:pointer">Cancel</button>
          </div>
        </div>`;
      document.body.appendChild(overlay);
      document.getElementById('_ftaResolveConfirm').onclick = () => {
        const val = document.getElementById('_ftaResolutionSel').value;
        document.body.removeChild(overlay);
        resolve(val);
      };
      document.getElementById('_ftaResolveCancel').onclick = () => {
        document.body.removeChild(overlay);
        resolve(null);
      };
    });
  }

  function _fmt$(n) {
    if (!n && n !== 0) return '—';
    return '$' + Number(n).toLocaleString('en-US', { maximumFractionDigits: 0 });
  }

  function _setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
  }

  function _esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  function _toast(msg, type = 'info') {
    if (typeof SL !== 'undefined' && SL.toast) { SL.toast(msg, type); return; }
    const colors = { success: '#10b981', error: '#ef4444', info: '#3b82f6' };
    const t = document.createElement('div');
    t.style.cssText = `position:fixed;bottom:24px;right:24px;background:${colors[type]||colors.info};color:#fff;padding:12px 20px;border-radius:10px;font-size:13px;font-weight:600;z-index:99999;box-shadow:0 4px 16px rgba(0,0,0,.3)`;
    t.textContent = msg;
    document.body.appendChild(t);
    setTimeout(() => t.remove(), 3500);
  }

  return { load, runScan, resolve, sendGeoLink };
})();
