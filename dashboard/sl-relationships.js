/**
 * sl-relationships.js — Person finder + who-knows-who graph for Active Bonds
 *
 * - Find Person: all bonds linked to a defendant or indemnitor (name/phone)
 * - Relationship graph: nodes (people) + edges (same bond / shared phone)
 * - Related cases panel inside the bond edit drawer
 */
const SLRelationships = {
  _esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  },

  _money(n) {
    if (n == null || n === '') return '—';
    const v = Number(n);
    return isNaN(v) ? '—' : '$' + v.toLocaleString();
  },

  openPersonFinder(prefill) {
    this._ensureModals();
    const m = document.getElementById('slPersonFinderModal');
    if (prefill) {
      if (prefill.name) document.getElementById('slPfName').value = prefill.name;
      if (prefill.phone) document.getElementById('slPfPhone').value = prefill.phone;
      if (prefill.role) document.getElementById('slPfRole').value = prefill.role;
    }
    m.style.display = 'flex';
    document.getElementById('slPfName')?.focus();
  },

  closePersonFinder() {
    const m = document.getElementById('slPersonFinderModal');
    if (m) m.style.display = 'none';
  },

  async searchPerson() {
    const name = document.getElementById('slPfName')?.value?.trim() || '';
    const phone = document.getElementById('slPfPhone')?.value?.trim() || '';
    const role = document.getElementById('slPfRole')?.value || 'any';
    const body = document.getElementById('slPfResults');
    if (!name && !phone) {
      body.innerHTML = '<div style="color:var(--warning)">Enter a name and/or phone number.</div>';
      return;
    }
    body.innerHTML = '<div class="loading">Searching bonds…</div>';
    try {
      const q = new URLSearchParams({ name, phone, role, limit: '50' });
      const res = await fetch(`/api/active-bonds/by-person?${q}`, { credentials: 'same-origin' });
      const data = await res.json();
      if (!data.success) throw new Error(data.error || 'Search failed');
      if (!data.bonds?.length) {
        body.innerHTML = '<div style="color:var(--muted);padding:16px">No bonds found for that person.</div>';
        return;
      }
      body.innerHTML = `
        <div style="font-size:12px;color:var(--muted);margin-bottom:10px">${data.count} bond(s)</div>
        <div class="table-responsive"><table class="data-table" style="width:100%;font-size:12px">
          <thead><tr>
            <th>Defendant</th><th>Indemnitor</th><th>Booking</th><th>County</th>
            <th>Bond</th><th>Status</th><th>Court</th><th></th>
          </tr></thead>
          <tbody>
            ${data.bonds.map(b => {
              const ind = b.indemnitor?.name || b.indemnitor_name || '—';
              const indPh = b.indemnitor?.phone || b.indemnitor_phone || '';
              return `<tr>
                <td><strong>${this._esc(b.defendant_name || '—')}</strong></td>
                <td>${this._esc(ind)}${indPh ? `<div style="font-size:10px;color:var(--muted)">${this._esc(indPh)}</div>` : ''}</td>
                <td style="font-family:monospace;font-size:11px">${this._esc(b.booking_number || '')}</td>
                <td>${this._esc(b.county || '')}</td>
                <td>${this._money(b.bond_amount)}</td>
                <td>${this._esc(b.status || 'active')}</td>
                <td>${this._esc((b.court_date || '').toString().slice(0, 10))}</td>
                <td style="white-space:nowrap">
                  <button class="btn-export" style="font-size:10px;padding:3px 8px" onclick="SLRelationships.openCase('${this._esc(b.booking_number)}')">Open</button>
                  <button class="btn-export" style="font-size:10px;padding:3px 8px;background:#6366f1;color:#fff" onclick="SLRelationships.openGraph('${this._esc(b.booking_number)}')">🕸️</button>
                </td>
              </tr>`;
            }).join('')}
          </tbody>
        </table></div>`;
    } catch (e) {
      body.innerHTML = `<div style="color:var(--danger)">${this._esc(e.message)}</div>`;
    }
  },

  openCase(booking) {
    this.closePersonFinder();
    this.closeGraph();
    if (typeof openEditDrawer === 'function') openEditDrawer(booking);
    else if (window.SLActiveBonds?.openEditDrawer) SLActiveBonds.openEditDrawer(booking);
  },

  findFromEdit(role) {
    const name = role === 'indemnitor'
      ? document.getElementById('abEditIndemName')?.value
      : document.getElementById('abEditDefName')?.value;
    const phone = role === 'indemnitor'
      ? document.getElementById('abEditIndemPhone')?.value
      : '';
    this.openPersonFinder({ name, phone, role: role === 'any' ? 'any' : role });
    setTimeout(() => this.searchPerson(), 50);
  },

  async loadRelatedIntoPanel(bond) {
    const panel = document.getElementById('abRelatedCasesPanel');
    if (!panel || !bond) return;
    panel.innerHTML = 'Loading related cases…';
    const name = bond.defendant_name || '';
    const indPhone = bond.indemnitor?.phone || bond.indemnitor_phone || '';
    try {
      const q = new URLSearchParams({
        name: name.split(',')[0] || name,
        phone: indPhone,
        role: 'any',
        limit: '20',
      });
      const res = await fetch(`/api/active-bonds/by-person?${q}`, { credentials: 'same-origin' });
      const data = await res.json();
      const others = (data.bonds || []).filter(
        b => b.booking_number !== bond.booking_number
      );
      if (!others.length) {
        panel.innerHTML = '<span style="color:var(--muted)">No other active bonds linked to this defendant/indemnitor.</span>';
        return;
      }
      panel.innerHTML = others.map(b => {
        const ind = b.indemnitor?.name || b.indemnitor_name || '—';
        return `<div style="display:flex;justify-content:space-between;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid var(--border)">
          <div>
            <strong>${this._esc(b.defendant_name || '—')}</strong>
            <span style="color:var(--muted)"> · ${this._esc(ind)}</span>
            <div style="font-size:10px;color:var(--muted)">${this._esc(b.booking_number)} · ${this._esc(b.county || '')} · ${this._esc(b.status || '')} · ${this._money(b.bond_amount)}</div>
          </div>
          <button class="btn-export" style="font-size:10px;padding:3px 8px" onclick="SLRelationships.openCase('${this._esc(b.booking_number)}')">Open</button>
        </div>`;
      }).join('');
    } catch (e) {
      panel.innerHTML = `<span style="color:var(--danger)">${this._esc(e.message)}</span>`;
    }
  },

  openGraph(seedBooking) {
    this._ensureModals();
    const m = document.getElementById('slRelGraphModal');
    m.style.display = 'flex';
    this.renderGraph(seedBooking || '');
  },

  closeGraph() {
    const m = document.getElementById('slRelGraphModal');
    if (m) m.style.display = 'none';
  },

  async renderGraph(seedBooking) {
    const body = document.getElementById('slRelGraphBody');
    const title = document.getElementById('slRelGraphTitle');
    if (title) {
      title.textContent = seedBooking
        ? `🕸️ Relationships — seed ${seedBooking}`
        : '🕸️ Who Knows Who — Active Bonds network';
    }
    body.innerHTML = '<div class="loading" style="padding:24px;text-align:center">Building graph…</div>';
    try {
      const q = new URLSearchParams({ limit: '120' });
      if (seedBooking) q.set('seed_booking', seedBooking);
      const res = await fetch(`/api/active-bonds/relationship-graph?${q}`, {
        credentials: 'same-origin',
      });
      const data = await res.json();
      if (!data.success) throw new Error(data.error || 'Graph failed');

      const nodes = data.nodes || [];
      const edges = data.edges || [];
      if (!nodes.length) {
        body.innerHTML = '<div style="padding:24px;color:var(--muted);text-align:center">No relationship data yet. Add defendant + indemnitor names/phones on active bonds.</div>';
        return;
      }

      // Sort people by bond_count for a readable list + simple visual map
      const byBonds = [...nodes].sort((a, b) => b.bond_count - a.bond_count);
      const edgeSummary = {};
      edges.forEach(e => {
        const k = e.type || 'link';
        edgeSummary[k] = (edgeSummary[k] || 0) + 1;
      });

      // Build adjacency for "connected to"
      const adj = {};
      edges.forEach(e => {
        adj[e.source] = adj[e.source] || [];
        adj[e.target] = adj[e.target] || [];
        const sn = nodes.find(n => n.id === e.source);
        const tn = nodes.find(n => n.id === e.target);
        if (tn) adj[e.source].push({ ...tn, via: e.type, booking: e.booking_number });
        if (sn) adj[e.target].push({ ...sn, via: e.type, booking: e.booking_number });
      });

      body.innerHTML = `
        <div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:14px">
          <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:10px 14px">
            <div style="font-size:10px;color:var(--muted)">People</div>
            <div style="font-size:20px;font-weight:700">${data.node_count}</div>
          </div>
          <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:10px 14px">
            <div style="font-size:10px;color:var(--muted)">Links</div>
            <div style="font-size:20px;font-weight:700">${data.edge_count}</div>
          </div>
          <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:10px 14px">
            <div style="font-size:10px;color:var(--muted)">Bonds scanned</div>
            <div style="font-size:20px;font-weight:700">${data.bond_count}</div>
          </div>
          ${Object.entries(edgeSummary).map(([k, v]) =>
            `<div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:10px 14px">
              <div style="font-size:10px;color:var(--muted)">${this._esc(k)}</div>
              <div style="font-size:20px;font-weight:700">${v}</div>
            </div>`
          ).join('')}
        </div>
        <div style="font-size:11px;color:var(--muted);margin-bottom:10px">
          <strong>same_bond</strong> = defendant + indemnitor on one case ·
          <strong>shared_phone</strong> = same phone across cases (likely same person or household)
        </div>
        <div style="max-height:55vh;overflow-y:auto">
          ${byBonds.map(n => {
            const roles = (n.roles || []).map(r =>
              `<span style="font-size:10px;padding:1px 6px;border-radius:4px;background:${r === 'defendant' ? 'rgba(0,212,170,.15)' : 'rgba(139,92,246,.15)'};color:${r === 'defendant' ? '#00d4aa' : '#a78bfa'}">${r}</span>`
            ).join(' ');
            const conns = (adj[n.id] || []).slice(0, 8);
            const connHtml = conns.length
              ? conns.map(c =>
                  `<span style="display:inline-block;margin:2px 4px 2px 0;padding:2px 8px;background:rgba(255,255,255,.04);border:1px solid var(--border);border-radius:12px;font-size:11px">
                    ${this._esc(c.name)} <span style="color:var(--muted)">(${this._esc(c.via)}${c.booking ? ' · ' + this._esc(c.booking) : ''})</span>
                  </span>`
                ).join('')
              : '<span style="color:var(--muted);font-size:11px">No links</span>';
            return `<div style="border:1px solid var(--border);border-radius:10px;padding:12px 14px;margin-bottom:8px;background:var(--panel)">
              <div style="display:flex;justify-content:space-between;gap:8px;align-items:flex-start">
                <div>
                  <strong style="font-size:14px">${this._esc(n.name)}</strong> ${roles}
                  ${n.phone ? `<div style="font-size:11px;color:var(--muted);font-family:monospace">${this._esc(n.phone)}</div>` : ''}
                  <div style="font-size:11px;color:var(--muted);margin-top:2px">${n.bond_count} bond(s): ${(n.bookings || []).map(b => this._esc(b)).join(', ')}</div>
                </div>
                <button class="btn-export" style="font-size:10px;padding:4px 8px;background:#8b5cf6;color:#fff"
                  onclick="SLRelationships.openPersonFinder({name:'${this._esc(n.name).replace(/'/g, "\\'")}',phone:'${this._esc(n.phone)}'});SLRelationships.searchPerson()">Find bonds</button>
              </div>
              <div style="margin-top:8px"><span style="font-size:10px;color:var(--muted);text-transform:uppercase">Connected to</span><div style="margin-top:4px">${connHtml}</div></div>
            </div>`;
          }).join('')}
        </div>`;
    } catch (e) {
      body.innerHTML = `<div style="color:var(--danger);padding:20px">${this._esc(e.message)}</div>`;
    }
  },

  _ensureModals() {
    if (document.getElementById('slPersonFinderModal')) return;

    // Person finder
    const pf = document.createElement('div');
    pf.id = 'slPersonFinderModal';
    pf.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:10050;align-items:center;justify-content:center';
    pf.innerHTML = `
      <div style="background:var(--bg);border:1px solid var(--border);border-radius:14px;width:min(920px,96vw);max-height:90vh;overflow:hidden;display:flex;flex-direction:column;box-shadow:0 20px 60px rgba(0,0,0,.5)">
        <div style="padding:16px 20px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center">
          <div>
            <h3 style="margin:0;font-size:16px">👤 Find Person Across Bonds</h3>
            <div style="font-size:12px;color:var(--muted);margin-top:2px">Recall every active case linked to a defendant or indemnitor</div>
          </div>
          <button onclick="SLRelationships.closePersonFinder()" style="background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer">✕</button>
        </div>
        <div style="padding:14px 20px;display:flex;flex-wrap:wrap;gap:10px;border-bottom:1px solid var(--border)">
          <input id="slPfName" type="text" placeholder="Name" style="flex:1;min-width:160px;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--panel);color:var(--text)" onkeydown="if(event.key==='Enter')SLRelationships.searchPerson()">
          <input id="slPfPhone" type="tel" placeholder="Phone" style="width:160px;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--panel);color:var(--text)" onkeydown="if(event.key==='Enter')SLRelationships.searchPerson()">
          <select id="slPfRole" style="padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--panel);color:var(--text)">
            <option value="any">Any role</option>
            <option value="defendant">Defendant</option>
            <option value="indemnitor">Indemnitor</option>
          </select>
          <button class="btn-primary" style="padding:8px 16px" onclick="SLRelationships.searchPerson()">Search</button>
        </div>
        <div id="slPfResults" style="padding:16px 20px;overflow-y:auto;flex:1"></div>
      </div>`;
    pf.addEventListener('click', e => { if (e.target === pf) this.closePersonFinder(); });
    document.body.appendChild(pf);

    // Relationship graph
    const rg = document.createElement('div');
    rg.id = 'slRelGraphModal';
    rg.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:10050;align-items:center;justify-content:center';
    rg.innerHTML = `
      <div style="background:var(--bg);border:1px solid var(--border);border-radius:14px;width:min(960px,96vw);max-height:92vh;overflow:hidden;display:flex;flex-direction:column;box-shadow:0 20px 60px rgba(0,0,0,.5)">
        <div style="padding:16px 20px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center;gap:12px">
          <h3 id="slRelGraphTitle" style="margin:0;font-size:16px">🕸️ Who Knows Who</h3>
          <div style="display:flex;gap:8px">
            <button class="btn-export" style="font-size:11px;padding:5px 10px" onclick="SLRelationships.renderGraph('')">Full network</button>
            <button onclick="SLRelationships.closeGraph()" style="background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer">✕</button>
          </div>
        </div>
        <div id="slRelGraphBody" style="padding:16px 20px;overflow-y:auto;flex:1"></div>
      </div>`;
    rg.addEventListener('click', e => { if (e.target === rg) this.closeGraph(); });
    document.body.appendChild(rg);

    // Minimal toggle styles if missing
    if (!document.getElementById('slToggleCss')) {
      const st = document.createElement('style');
      st.id = 'slToggleCss';
      st.textContent = `
        .toggle-switch{position:relative;display:inline-block;width:42px;height:24px}
        .toggle-switch input{opacity:0;width:0;height:0}
        .toggle-switch .slider{position:absolute;cursor:pointer;inset:0;background:#333;border-radius:24px;transition:.2s}
        .toggle-switch .slider:before{position:absolute;content:"";height:18px;width:18px;left:3px;bottom:3px;background:#fff;border-radius:50%;transition:.2s}
        .toggle-switch input:checked+.slider{background:var(--accent,#00d4aa)}
        .toggle-switch input:checked+.slider:before{transform:translateX(18px)}
        .badge.bg-blue{background:rgba(59,130,246,.2);color:#60a5fa;padding:2px 8px;border-radius:4px;font-size:11px}
        .badge.bg-orange{background:rgba(245,158,11,.2);color:#fbbf24;padding:2px 8px;border-radius:4px;font-size:11px}
        .badge.bg-purple{background:rgba(139,92,246,.2);color:#a78bfa;padding:2px 8px;border-radius:4px;font-size:11px}
        .badge.bg-green{background:rgba(34,197,94,.2);color:#4ade80;padding:2px 8px;border-radius:4px;font-size:11px}
        .badge.bg-gray{background:rgba(148,163,184,.2);color:#94a3b8;padding:2px 8px;border-radius:4px;font-size:11px}
      `;
      document.head.appendChild(st);
    }
  },
};

window.SLRelationships = SLRelationships;
