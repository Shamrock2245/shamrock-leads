/**
 * sl-paperwork.js — Twenty CRM Style Document Operations & E-Signature Hub
 */
const SLPaperwork = {
  _currentSubTab: 'live',
  _allPackets: [],

  async load() {
    await this.loadLivePackets();
    await this.loadConfig();
  },

  switchSubTab(tabName) {
    this._currentSubTab = tabName;
    ['live', 'templates', 'rules'].forEach(t => {
      const btn = document.getElementById(`pwSubTab_${t}`);
      const pane = document.getElementById(`pwPane_${t}`);
      if (btn) btn.classList.toggle('active', t === tabName);
      if (pane) pane.style.display = t === tabName ? 'block' : 'none';
    });
  },

  async loadLivePackets() {
    const tbody = document.querySelector('#tableLivePaperworkPackets tbody');
    if (tbody) tbody.innerHTML = `<tr><td colspan="7" class="loading">Loading live document packets…</td></tr>`;

    try {
      const res = await fetch('/api/paperwork/all', { credentials: 'same-origin' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.success === false) throw new Error(data.error || 'Failed to load packets');

      this._allPackets = data.packets || [];
      this.renderLiveSummary(data.summary);
      this.renderLivePacketsTable(this._allPackets);
    } catch (err) {
      console.error(err);
      if (tbody) tbody.innerHTML = `<tr><td colspan="7" style="color:var(--danger);text-align:center;padding:20px">Failed to load packets: ${this._esc(err.message)}</td></tr>`;
    }
  },

  renderLiveSummary(summary) {
    const bar = document.getElementById('paperworkConfigSummary');
    if (!bar || !summary) return;
    const chip = (label, val, color) =>
      `<div style="background:var(--panel,#1e293b);border:1px solid var(--border,#334155);border-radius:10px;padding:12px 16px;min-width:130px">
        <div style="font-size:11px;color:var(--muted,#94a3b8);text-transform:uppercase;letter-spacing:.04em">${label}</div>
        <div style="font-size:22px;font-weight:700;color:${color}">${val ?? 0}</div>
      </div>`;
    bar.innerHTML =
      chip('Total Packets', summary.total_packets, '#38bdf8') +
      chip('Awaiting Signature', summary.pending_signature, '#f59e0b') +
      chip('Signed & Completed', summary.signed_completed, '#10b981') +
      chip('Filed to Drive', summary.filed_to_drive, '#c084fc');
  },

  renderLivePacketsTable(packets) {
    const tbody = document.querySelector('#tableLivePaperworkPackets tbody');
    if (!tbody) return;
    tbody.innerHTML = '';

    if (!packets || packets.length === 0) {
      tbody.innerHTML = `<tr><td colspan="7" style="color:var(--muted);text-align:center;padding:24px">No document packets found</td></tr>`;
      return;
    }

    packets.forEach(p => {
      const pid = p.packet_id || '—';
      const defName = p.defendant_name || p.booking_number || '—';
      const indName = p.indemnitor_name || '—';
      const surety = (p.surety_id || 'osi').toUpperCase();
      const status = p.status || p.signnow_status || 'draft';
      const dt = p.created_at ? p.created_at.slice(0, 10) : '—';

      const suretyChipCls = surety === 'OSI' ? 'inv-chip-osi' : 'inv-chip-palm';
      const suretyIcon = surety === 'OSI' ? '🛡️ OSI' : '🌴 PSC';

      let statusBadge = `<span class="badge bg-blue">${this._esc(status)}</span>`;
      if (['signed', 'completed'].includes(status)) {
        statusBadge = `<span class="badge bg-green">✅ Signed</span>`;
      } else if (['sent', 'signnow_pending'].includes(status)) {
        statusBadge = `<span class="badge bg-orange">📱 Sent (Pending)</span>`;
      } else if (status === 'voided') {
        statusBadge = `<span class="badge bg-red">❌ Voided</span>`;
      }

      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong style="font-family:monospace;font-size:11px">${this._esc(pid)}</strong></td>
        <td><strong>${this._esc(defName)}</strong></td>
        <td>${this._esc(indName)}</td>
        <td><span class="inv-surety-chip ${suretyChipCls}" style="font-size:10px;padding:2px 6px">${suretyIcon}</span></td>
        <td>${statusBadge}</td>
        <td>${this._esc(dt)}</td>
        <td style="text-align:right">
          <div style="display:inline-flex;gap:6px">
            <button type="button" class="inv-btn" onclick="SLPaperwork.showHydrationAudit('${this._esc(pid)}')" style="font-size:11px;padding:3px 7px" title="Audit field hydration completeness">🔍 Audit</button>
            ${p.drive_url ? `<a href="${this._esc(p.drive_url)}" target="_blank" class="inv-btn" style="font-size:11px;padding:3px 7px;color:#38bdf8" title="View signed PDF folder in Drive">☁️ Drive</a>` : ''}
            ${status !== 'voided' ? `<button type="button" class="inv-btn" onclick="SLPaperwork.deliverPacket('${this._esc(pid)}')" style="font-size:11px;padding:3px 7px;color:#34d399" title="Deliver via BlueBubbles iMessage / SMS">📱 Deliver</button>` : ''}
          </div>
        </td>
      `;
      tbody.appendChild(tr);
    });
  },

  filterPackets() {
    const q = (document.getElementById('pwSearchInput')?.value || '').toLowerCase();
    const st = document.getElementById('pwStatusSelect')?.value || 'all';
    const sur = document.getElementById('pwSuretySelect')?.value || 'all';

    const filtered = this._allPackets.filter(p => {
      const matchQ = !q || (
        (p.defendant_name || '').toLowerCase().includes(q) ||
        (p.indemnitor_name || '').toLowerCase().includes(q) ||
        (p.packet_id || '').toLowerCase().includes(q) ||
        (p.case_number || '').toLowerCase().includes(q) ||
        (p.booking_number || '').toLowerCase().includes(q)
      );

      const pStatus = (p.status || p.signnow_status || 'draft').toLowerCase();
      let matchSt = true;
      if (st === 'sent') matchSt = ['sent', 'signnow_pending', 'partially_signed'].includes(pStatus);
      else if (st === 'signed') matchSt = ['signed', 'completed'].includes(pStatus);
      else if (st === 'draft') matchSt = ['draft', 'created'].includes(pStatus);
      else if (st === 'voided') matchSt = pStatus === 'voided';

      const pSurety = (p.surety_id || 'osi').toLowerCase();
      const matchSur = sur === 'all' || pSurety === sur.toLowerCase();

      return matchQ && matchSt && matchSur;
    });

    this.renderLivePacketsTable(filtered);
  },

  async showHydrationAudit(packetId) {
    const modal = document.getElementById('pwHydrationModal');
    const body = document.getElementById('pwHydrationModalBody');
    if (modal) { modal.style.display = 'flex'; modal.classList.add('active'); }
    if (body) body.innerHTML = '<p>Loading field hydration audit…</p>';

    try {
      const res = await fetch(`/api/paperwork/${packetId}/hydration-audit`, { credentials: 'same-origin' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!data.success) throw new Error(data.error || 'Audit failed');

      const scoreColor = data.hydration_score >= 100 ? '#10b981' : data.hydration_score >= 70 ? '#f59e0b' : '#ef4444';

      let rows = (data.fields || []).map(f => `
        <tr style="border-bottom:1px solid rgba(255,255,255,0.06)">
          <td style="font-weight:600;padding:6px 8px">${this._esc(f.label)}</td>
          <td style="font-family:monospace;font-size:11px;color:${f.hydrated ? '#38bdf8' : 'var(--muted)'}">${this._esc(f.val || '— missing —')}</td>
          <td style="text-align:right;padding:6px 8px">${f.hydrated ? '<span style="color:#10b981;font-weight:700">✓ Complete</span>' : '<span style="color:#ef4444">⚠️ Missing</span>'}</td>
        </tr>
      `).join('');

      body.innerHTML = `
        <div style="display:flex;align-items:center;justify-content:space-between;background:rgba(15,23,42,0.8);padding:12px 16px;border-radius:8px;margin-bottom:14px;border:1px solid rgba(255,255,255,0.1)">
          <div>
            <div style="font-size:12px;color:var(--muted)">Packet ID: <span class="mono">${this._esc(data.packet_id)}</span></div>
            <div style="font-size:13px;font-weight:700;color:var(--text);margin-top:2px">Hydration Status: ${this._esc(data.status || 'Draft')}</div>
          </div>
          <div style="text-align:right">
            <div style="font-size:24px;font-weight:800;color:${scoreColor}">${data.hydration_score}%</div>
            <div style="font-size:10px;color:var(--muted)">${data.hydrated_count} of ${data.total_required} fields ready</div>
          </div>
        </div>
        <table style="width:100%;font-size:12px;border-collapse:collapse">
          <thead>
            <tr style="border-bottom:2px solid rgba(255,255,255,0.1);text-align:left">
              <th style="padding:6px 8px">Field Name</th>
              <th>Hydrated Value</th>
              <th style="text-align:right;padding:6px 8px">Audit Status</th>
            </tr>
          </thead>
          <tbody>${rows}</tbody>
        </table>
      `;
    } catch (err) {
      if (body) body.innerHTML = `<p style="color:var(--danger)">Failed to audit packet: ${this._esc(err.message)}</p>`;
    }
  },

  closeHydrationModal() {
    const modal = document.getElementById('pwHydrationModal');
    if (modal) { modal.style.display = 'none'; modal.classList.remove('active'); }
  },

  async deliverPacket(packetId) {
    if (!confirm(`Deliver paperwork packet ${packetId} via BlueBubbles iMessage / SMS?`)) return;
    try {
      const res = await fetch(`/api/paperwork/${packetId}/deliver`, { method: 'POST', credentials: 'same-origin' });
      const data = await res.json();
      if (!res.ok || !data.success) throw new Error(data.error || 'Delivery failed');
      alert(`📱 Paperwork delivered successfully to ${data.recipient || 'client'}`);
      this.loadLivePackets();
    } catch (err) {
      alert(`❌ Delivery error: ${err.message}`);
    }
  },

  async loadConfig() {
    const rulesEl = document.getElementById('paperworkDocRules');
    if (rulesEl) rulesEl.textContent = 'Loading…';
    ['tablePaperworkOsi', 'tablePaperworkPalmetto'].forEach(id => {
      const tb = document.querySelector(`#${id} tbody`);
      if (tb) tb.innerHTML = `<tr><td colspan="4" class="loading">Loading…</td></tr>`;
    });

    try {
      const res = await fetch('/api/paperwork/config', { credentials: 'same-origin' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.success === false) throw new Error(data.error || 'Config error');

      this.renderDocRules(data.doc_rules);
      this.renderTable('tablePaperworkOsi', data.template_map?.osi);
      this.renderTable('tablePaperworkPalmetto', data.template_map?.palmetto);
    } catch (err) {
      console.error(err);
      if (rulesEl) {
        rulesEl.textContent = 'Failed to load: ' + err.message;
        rulesEl.style.color = 'var(--danger)';
      }
    }
  },

  renderDocRules(rules) {
    const el = document.getElementById('paperworkDocRules');
    if (!el) return;
    if (!rules || !Object.keys(rules).length) {
      el.innerHTML = '<span style="color:var(--muted)">No document rules defined.</span>';
      return;
    }
    const rows = Object.entries(rules).map(([key, meta]) => {
      const rule = (meta && meta.rule) || 'static';
      const label = (meta && meta.label) || key;
      return `<tr>
        <td style="font-family:monospace;font-size:12px">${this._esc(key)}</td>
        <td>${this._esc(label)}</td>
        <td><span class="badge ${this.getBadgeClass(rule)}">${this._esc(rule)}</span></td>
      </tr>`;
    }).join('');

    el.innerHTML = `<table class="data-table" style="width:100%">
      <thead><tr><th>Key</th><th>Label</th><th>Rule</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <p style="font-size:11px;color:var(--muted);margin-top:10px">
      <strong>Rules:</strong> static = once per packet · shared = one copy · per-indemnitor / per-person / per-charge = multiply · print-only = never e-sign
    </p>`;
  },

  renderTable(tableId, templates) {
    const tbody = document.querySelector(`#${tableId} tbody`);
    if (!tbody) return;
    tbody.innerHTML = '';

    if (!templates || !Object.keys(templates).length) {
      tbody.innerHTML = `<tr><td colspan="4" style="color:var(--muted);text-align:center;padding:20px">No templates found</td></tr>`;
      return;
    }

    const entries = Object.entries(templates)
      .map(([key, tpl]) => ({ key, ...(typeof tpl === 'object' ? tpl : { template_id: tpl }) }))
      .sort((a, b) => a.key.localeCompare(b.key));

    entries.forEach(t => {
      const tid = t.template_id || '';
      const configured = t.configured !== false && tid && tid !== '(uses shared)';
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong style="font-family:monospace;font-size:12px">${this._esc(t.key)}</strong></td>
        <td>${this._esc(t.label || t.name || 'N/A')}</td>
        <td style="font-family:monospace;font-size:11px;word-break:break-all;color:${configured ? 'var(--text)' : 'var(--muted)'}">${this._esc(tid || '— not set —')}</td>
        <td><span class="badge ${this.getBadgeClass(t.rule)}">${this._esc(t.rule || 'static')}</span>
          ${configured ? '' : '<span style="margin-left:6px;font-size:10px;color:var(--warning)">needs ID</span>'}
        </td>`;
      tbody.appendChild(tr);
    });
  },

  getBadgeClass(rule) {
    switch (rule) {
      case 'per-indemnitor': return 'bg-blue';
      case 'per-charge': return 'bg-orange';
      case 'per-person': return 'bg-purple';
      case 'shared': return 'bg-green';
      case 'print-only': return 'bg-gray';
      default: return '';
    }
  },

  _esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  },
};

window.SLPaperwork = SLPaperwork;

