/**
 * ShamrockLeads — Client Portal Management (Staff Side)
 * Generate, manage, send, and revoke magic-link portal tokens.
 * Fortune 50 aesthetic — premium data surfaces for bondsmen who love their business.
 */

const SLPortal = {
  _tokens: [],
  _bondSearch: [],
  _loaded: false,

  async init() {
    if (this._loaded) return;
    this._loaded = true;
    await this.loadStats();
  },

  // ═══ LOAD PORTAL OVERVIEW STATS ════════════════════════════════════════════
  async loadStats() {
    const body = document.getElementById('portalBody');
    if (!body) return;

    body.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:center;padding:60px 0">
        <div class="sl-spinner"></div>
      </div>`;

    try {
      // Load active bonds that have tokens + recent tokens
      const [bondsRes] = await Promise.all([
        fetch('/api/active-bonds'),
      ]);
      const bondsData = await bondsRes.json();
      const bonds = bondsData.bonds || bondsData.data || [];

      this._bondSearch = bonds;
      this._renderPortalHub(bonds);
    } catch (e) {
      body.innerHTML = `<div class="sl-empty-state">
        <div class="sl-empty-icon">⚠️</div>
        <div class="sl-empty-title">Failed to load portal data</div>
        <div class="sl-empty-desc">${e.message}</div>
      </div>`;
    }
  },

  _renderPortalHub(bonds) {
    const body = document.getElementById('portalBody');
    const activeBonds = bonds.filter(b => b.status === 'active' || b.status === 'monitoring');

    body.innerHTML = `
      <!-- Portal KPIs -->
      <div class="portal-kpi-row">
        <div class="portal-kpi">
          <div class="portal-kpi-value" id="kpiTotalBonds">${activeBonds.length}</div>
          <div class="portal-kpi-label">Active Bonds</div>
        </div>
        <div class="portal-kpi accent">
          <div class="portal-kpi-value" id="kpiPortalLinks">—</div>
          <div class="portal-kpi-label">Portal Links</div>
        </div>
        <div class="portal-kpi">
          <div class="portal-kpi-value" id="kpiCheckins">—</div>
          <div class="portal-kpi-label">Check-Ins (7d)</div>
        </div>
      </div>

      <!-- Action Bar -->
      <div class="portal-action-bar">
        <div class="portal-search-wrap">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="opacity:0.4;margin-right:6px">
            <circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>
          </svg>
          <input type="text" id="portalSearchInput" placeholder="Search by defendant name or booking #…"
                 oninput="SLPortal.filterBonds(this.value)" autocomplete="off">
        </div>
        <button class="portal-btn-generate" onclick="SLPortal.openGenerateModal()">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
          Generate Portal Link
        </button>
      </div>

      <!-- Bond List -->
      <div class="portal-section-label">Active Bonds — Portal Access</div>
      <div id="portalBondList" class="portal-bond-list"></div>

      <!-- Generate Modal (hidden) -->
      <div id="portalGenerateModal" class="portal-modal-overlay" style="display:none" onclick="if(event.target===this)SLPortal.closeGenerateModal()">
        <div class="portal-modal">
          <div class="portal-modal-header">
            <h3>🔗 Generate Portal Link</h3>
            <button class="portal-modal-close" onclick="SLPortal.closeGenerateModal()">✕</button>
          </div>
          <div class="portal-modal-body" id="portalModalBody">
            <div class="portal-form-group">
              <label>Booking Number</label>
              <input type="text" id="genBookingNum" placeholder="e.g. 2025-00012345">
            </div>
            <div class="portal-form-group">
              <label>Recipient Role</label>
              <div class="portal-role-toggle">
                <button class="portal-role-btn active" data-role="indemnitor" onclick="SLPortal.selectRole(this)">
                  🤝 Indemnitor
                </button>
                <button class="portal-role-btn" data-role="defendant" onclick="SLPortal.selectRole(this)">
                  👤 Defendant
                </button>
              </div>
              <div class="portal-role-hint" id="roleHint">
                Indemnitors can view bond status, payment history, and make payments.
              </div>
            </div>
            <div class="portal-form-group" id="sendPhoneGroup" style="display:none">
              <label>Send via iMessage/SMS (optional)</label>
              <input type="tel" id="genPhone" placeholder="(239) 555-0100">
            </div>
            <div id="genResult" style="display:none"></div>
          </div>
          <div class="portal-modal-footer">
            <button class="portal-btn-secondary" onclick="SLPortal.closeGenerateModal()">Cancel</button>
            <button class="portal-btn-primary" id="genSubmitBtn" onclick="SLPortal.generateLink()">
              Generate Link
            </button>
          </div>
        </div>
      </div>
    `;

    this._renderBondList(activeBonds);
    this._loadTokenStats();
  },

  async _loadTokenStats() {
    try {
      const res = await fetch('/api/portal/stats');
      if (!res.ok) return;
      const data = await res.json();
      const el = document.getElementById('kpiPortalLinks');
      if (el) el.textContent = data.active_tokens ?? '—';
      const elCheckins = document.getElementById('kpiPortalCheckins');
      if (elCheckins) elCheckins.textContent = data.total_checkins ?? '—';
    } catch(e) { /* silent */ }
  },

  _renderBondList(bonds) {
    const container = document.getElementById('portalBondList');
    if (!container) return;

    if (bonds.length === 0) {
      container.innerHTML = `<div class="sl-empty-state" style="padding:40px 0">
        <div class="sl-empty-icon">🔒</div>
        <div class="sl-empty-title">No active bonds</div>
        <div class="sl-empty-desc">Bonds will appear here once cases are activated.</div>
      </div>`;
      return;
    }

    container.innerHTML = bonds.map(b => {
      const name = b.defendant_name || b.Defendant_Name || 'Unknown';
      const booking = b.booking_number || b.Booking_Number || '';
      const county = b.county || b.County || '';
      const amount = b.bond_amount || b.Bond_Amount || 0;
      const status = b.status || 'active';

      const statusCls = {
        active: 'st-active', monitoring: 'st-monitoring',
        alert: 'st-alert', exonerated: 'st-exonerated',
      }[status] || 'st-active';

      return `
        <div class="portal-bond-row" data-booking="${this._esc(booking)}" data-name="${this._esc(name.toLowerCase())}">
          <div class="portal-bond-info">
            <div class="portal-bond-name">${this._esc(name)}</div>
            <div class="portal-bond-meta">
              <span class="portal-bond-status ${statusCls}">${status}</span>
              <span>${this._esc(county)} County</span>
              <span>•</span>
              <span>${this._fmtMoney(amount)}</span>
              <span>•</span>
              <span class="portal-bond-booking">${this._esc(booking)}</span>
            </div>
          </div>
          <div class="portal-bond-actions">
            <button class="portal-action-btn" onclick="SLPortal.quickGenerate('${this._esc(booking)}','indemnitor')" title="Generate indemnitor link">
              🤝 Indemnitor
            </button>
            <button class="portal-action-btn" onclick="SLPortal.quickGenerate('${this._esc(booking)}','defendant')" title="Generate defendant link">
              👤 Defendant
            </button>
            <button class="portal-action-btn subtle" onclick="SLPortal.viewTokens('${this._esc(booking)}')" title="View active links">
              🔗
            </button>
          </div>
        </div>`;
    }).join('');
  },

  filterBonds(q) {
    q = (q || '').toLowerCase().trim();
    const rows = document.querySelectorAll('.portal-bond-row');
    rows.forEach(row => {
      const name = row.dataset.name || '';
      const booking = row.dataset.booking || '';
      row.style.display = (!q || name.includes(q) || booking.toLowerCase().includes(q)) ? '' : 'none';
    });
  },

  // ═══ ROLE TOGGLE ═══════════════════════════════════════════════════════════
  selectRole(btn) {
    document.querySelectorAll('.portal-role-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const role = btn.dataset.role;
    const hint = document.getElementById('roleHint');
    if (hint) {
      hint.textContent = role === 'defendant'
        ? 'Defendants can check in, view court dates, and monitor compliance.'
        : 'Indemnitors can view bond status, payment history, and make payments.';
    }
  },

  // ═══ GENERATE MODAL ═══════════════════════════════════════════════════════
  openGenerateModal(booking) {
    const modal = document.getElementById('portalGenerateModal');
    if (modal) modal.style.display = 'flex';
    if (booking) document.getElementById('genBookingNum').value = booking;
    document.getElementById('genResult').style.display = 'none';
    document.getElementById('genSubmitBtn').disabled = false;
    document.getElementById('genSubmitBtn').textContent = 'Generate Link';
  },

  closeGenerateModal() {
    const modal = document.getElementById('portalGenerateModal');
    if (modal) modal.style.display = 'none';
  },

  async generateLink() {
    const booking = (document.getElementById('genBookingNum')?.value || '').trim();
    const roleBtn = document.querySelector('.portal-role-btn.active');
    const role = roleBtn?.dataset.role || 'indemnitor';
    const phone = (document.getElementById('genPhone')?.value || '').trim();
    const resultDiv = document.getElementById('genResult');
    const submitBtn = document.getElementById('genSubmitBtn');

    if (!booking) {
      this._showToast('Enter a booking number', 'error');
      return;
    }

    submitBtn.disabled = true;
    submitBtn.innerHTML = '<div class="sl-spinner" style="width:16px;height:16px;border-width:2px"></div>';

    try {
      let res, data;
      if (phone) {
        res = await fetch('/api/portal/send-link', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ booking_number: booking, role, phone }),
        });
        data = await res.json();
      } else {
        res = await fetch('/api/portal/generate', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ booking_number: booking, role, created_by: 'staff' }),
        });
        data = await res.json();
      }

      if (data.success || data.token_generated) {
        const url = data.url || '';
        resultDiv.innerHTML = `
          <div class="portal-result-success">
            <div class="portal-result-icon">✅</div>
            <div class="portal-result-title">Link Generated</div>
            ${phone && data.message_sent ? '<div class="portal-result-sent">📱 Sent via ' + (data.channel || 'message') + '</div>' : ''}
            <div class="portal-result-url">
              <input type="text" value="${this._esc(url)}" readonly id="portalUrlCopy" onclick="this.select()">
              <button class="portal-copy-btn" onclick="SLPortal.copyUrl()">📋 Copy</button>
            </div>
            <div class="portal-result-meta">Role: ${role} · Expires in 90 days</div>
          </div>`;
        resultDiv.style.display = 'block';
        submitBtn.textContent = '✅ Done';
        this._showToast('Portal link generated', 'success');
      } else {
        resultDiv.innerHTML = `<div class="portal-result-error">❌ ${this._esc(data.error || 'Failed to generate')}</div>`;
        resultDiv.style.display = 'block';
        submitBtn.textContent = 'Generate Link';
        submitBtn.disabled = false;
      }
    } catch (e) {
      resultDiv.innerHTML = `<div class="portal-result-error">❌ ${e.message}</div>`;
      resultDiv.style.display = 'block';
      submitBtn.textContent = 'Generate Link';
      submitBtn.disabled = false;
    }
  },

  async quickGenerate(booking, role) {
    this._showToast('Generating link…', 'info');
    try {
      const res = await fetch('/api/portal/generate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ booking_number: booking, role, created_by: 'staff' }),
      });
      const data = await res.json();
      if (data.success) {
        await navigator.clipboard.writeText(data.url);
        this._showToast(`${role === 'defendant' ? '👤' : '🤝'} Link copied to clipboard`, 'success');
      } else {
        this._showToast(data.error || 'Failed', 'error');
      }
    } catch(e) {
      this._showToast(e.message, 'error');
    }
  },

  async viewTokens(booking) {
    try {
      const res = await fetch(`/api/portal/tokens/${encodeURIComponent(booking)}`);
      const data = await res.json();
      const tokens = data.tokens || [];

      if (tokens.length === 0) {
        this._showToast('No active portal links for this bond', 'info');
        return;
      }

      // Show in modal
      this.openGenerateModal(booking);
      const resultDiv = document.getElementById('genResult');
      resultDiv.innerHTML = `
        <div class="portal-token-list-header">🔗 Active Links (${tokens.length})</div>
        ${tokens.map(t => `
          <div class="portal-token-row">
            <div class="portal-token-info">
              <span class="portal-bond-status ${t.role === 'defendant' ? 'st-monitoring' : 'st-active'}">${t.role}</span>
              <span class="portal-token-meta">Accessed ${t.access_count || 0}x · Created ${this._timeAgo(t.created_at)}</span>
            </div>
            <div class="portal-token-actions">
              <button class="portal-action-btn subtle" onclick="navigator.clipboard.writeText('${this._esc(t.url)}');SLPortal._showToast('Copied','success')" title="Copy link">📋</button>
              <button class="portal-action-btn subtle danger" onclick="SLPortal.revokeToken('${this._esc(t.token)}')" title="Revoke">🗑️</button>
            </div>
          </div>
        `).join('')}`;
      resultDiv.style.display = 'block';
    } catch(e) {
      this._showToast(e.message, 'error');
    }
  },

  async revokeToken(token) {
    if (!confirm('Revoke this portal link? The client will no longer be able to access it.')) return;
    try {
      const res = await fetch('/api/portal/revoke', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ token }),
      });
      const data = await res.json();
      if (data.success) {
        this._showToast('Link revoked', 'success');
        this.closeGenerateModal();
      } else {
        this._showToast(data.error || 'Failed', 'error');
      }
    } catch(e) {
      this._showToast(e.message, 'error');
    }
  },

  copyUrl() {
    const input = document.getElementById('portalUrlCopy');
    if (input) {
      input.select();
      navigator.clipboard.writeText(input.value);
      this._showToast('Link copied to clipboard', 'success');
    }
  },

  // ═══ HELPERS ═══════════════════════════════════════════════════════════════
  _fmtMoney(n) {
    return '$' + Number(n || 0).toLocaleString('en-US', {minimumFractionDigits: 0, maximumFractionDigits: 0});
  },
  _timeAgo(iso) {
    if (!iso) return '—';
    const s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (s < 3600) return Math.round(s/60) + 'm ago';
    if (s < 86400) return Math.round(s/3600) + 'h ago';
    return Math.round(s/86400) + 'd ago';
  },
  _esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  },
  _showToast(msg, type) {
    // Use existing SL toast if available, else simple alert
    if (typeof SL !== 'undefined' && SL.toast) {
      SL.toast(msg, type);
    } else if (typeof showToast === 'function') {
      showToast(msg, type);
    } else {
      console.log(`[${type}] ${msg}`);
    }
  },
};
