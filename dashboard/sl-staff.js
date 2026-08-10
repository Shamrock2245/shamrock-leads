/**
 * ShamrockLeads — Staff / Sub-Agent Whitelist Manager (God-Admin only)
 * ====================================================================
 * Manages /api/sub-agents/* and forfeiture alert phone list.
 */
(function () {
  'use strict';

  const API = window.API || '';

  const Staff = {
    agents: [],
    phones: [],
  };

  function toast(msg, type) {
    if (window.SL && SL.toast) SL.toast(msg, type);
    else console.log('[Staff]', msg);
  }

  function esc(s) {
    return String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  async function init() {
    // Only God-Admin should open this tab
    if (window.SL_RBAC && SL_RBAC.loaded && SL_RBAC.isSubAgent) {
      const el = document.getElementById('tabStaff');
      if (el) el.innerHTML = '<div class="alpr-error" style="padding:24px">God-Admin access required.</div>';
      return;
    }
    await Promise.all([loadAgents(), loadForfeiturePhones()]);
  }

  async function loadAgents() {
    const body = document.getElementById('staffAgentsBody');
    try {
      const r = await fetch(`${API}/api/sub-agents/list`, { credentials: 'same-origin' });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      Staff.agents = data.agents || [];
      renderAgents();
    } catch (e) {
      if (body) {
        body.innerHTML = `<tr><td colspan="6" class="alpr-muted">Failed to load agents: ${esc(e.message)}</td></tr>`;
      }
    }
  }

  function renderAgents() {
    const body = document.getElementById('staffAgentsBody');
    if (!body) return;
    if (!Staff.agents.length) {
      body.innerHTML =
        '<tr><td colspan="6" class="alpr-muted">No sub-agents whitelisted yet. Add one below.</td></tr>';
      return;
    }
    body.innerHTML = Staff.agents
      .map((a) => {
        const active = a.is_active !== false;
        return `<tr>
          <td><strong>${esc(a.agent_name)}</strong></td>
          <td><code>${esc(a.license_number)}</code></td>
          <td>${esc(a.phone || '—')}</td>
          <td><span class="alpr-pill ${active ? 'ok' : 'off'}">${active ? 'active' : 'revoked'}</span></td>
          <td style="font-size:0.8rem;color:#94a3b8">${esc(a.whitelisted_by || '')}<br>${esc(
            (a.whitelisted_at || '').slice(0, 19)
          )}</td>
          <td>
            ${
              active
                ? `<button type="button" class="alpr-btn" style="background:#ef4444;color:#fff;border:none;padding:4px 10px;border-radius:6px;cursor:pointer"
                    onclick="SLStaff.revoke('${esc(a.license_number)}')">Revoke</button>`
                : `<span style="color:#64748b;font-size:0.8rem">revoked</span>`
            }
          </td>
        </tr>`;
      })
      .join('');
  }

  async function addAgent() {
    const name = (document.getElementById('staffAgentName')?.value || '').trim();
    const license = (document.getElementById('staffAgentLicense')?.value || '').trim();
    const phone = (document.getElementById('staffAgentPhone')?.value || '').trim();
    const notes = (document.getElementById('staffAgentNotes')?.value || '').trim();
    if (!name || !license) {
      toast('Name and FL license number are required', 'error');
      return;
    }
    try {
      const r = await fetch(`${API}/api/sub-agents/add`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          agent_name: name,
          license_number: license,
          phone,
          notes,
        }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok || !data.success) throw new Error(data.error || `HTTP ${r.status}`);
      toast(`Whitelisted ${name} (${license})`, 'ok');
      ['staffAgentName', 'staffAgentLicense', 'staffAgentPhone', 'staffAgentNotes'].forEach((id) => {
        const el = document.getElementById(id);
        if (el) el.value = '';
      });
      await loadAgents();
    } catch (e) {
      toast(e.message || 'Failed to add agent', 'error');
    }
  }

  async function revoke(license) {
    if (!license) return;
    if (!confirm(`Revoke sub-agent access for ${license}?`)) return;
    try {
      const r = await fetch(`${API}/api/sub-agents/remove`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ license_number: license }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok || !data.success) throw new Error(data.error || `HTTP ${r.status}`);
      toast(`Revoked ${license}`, 'ok');
      await loadAgents();
    } catch (e) {
      toast(e.message || 'Revoke failed', 'error');
    }
  }

  async function loadForfeiturePhones() {
    const el = document.getElementById('staffForfeiturePhones');
    try {
      const r = await fetch(`${API}/api/discharge-monitor/forfeiture-phones`, {
        credentials: 'same-origin',
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      Staff.phones = data.phones || [];
      if (el) el.value = Staff.phones.join('\n');
    } catch (e) {
      if (el) el.placeholder = `Could not load phones: ${e.message}`;
    }
  }

  async function saveForfeiturePhones() {
    const el = document.getElementById('staffForfeiturePhones');
    const raw = (el?.value || '').split(/[\n,;]+/).map((s) => s.trim()).filter(Boolean);
    try {
      const r = await fetch(`${API}/api/discharge-monitor/forfeiture-phones`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phones: raw }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok || data.success === false) throw new Error(data.error || `HTTP ${r.status}`);
      Staff.phones = data.phones || raw;
      if (el) el.value = Staff.phones.join('\n');
      toast(`Saved ${Staff.phones.length} forfeiture alert numbers`, 'ok');
    } catch (e) {
      toast(e.message || 'Save failed', 'error');
    }
  }

  async function testForfeiture() {
    try {
      const r = await fetch(`${API}/api/discharge-monitor/test-forfeiture`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok || data.success === false) {
        const errMsg = data.error || (data.errors && data.errors.length ? data.errors[0].error : null) || 'Test alert failed';
        throw new Error(errMsg);
      }
      toast(`Test alert sent to ${data.sent}/${data.total_phones} phones`, 'ok');
    } catch (e) {
      toast(e.message || 'Test alert failed', 'error');
    }
  }

  window.SLStaff = {
    init,
    loadAgents,
    addAgent,
    revoke,
    loadForfeiturePhones,
    saveForfeiturePhones,
    testForfeiture,
  };
})();
