/**
 * ☘️ ShamrockLeads — Lee County Master Command Center (sl-lee-county.js)
 * High-performance UI controller for dominating Shamrock's home county (Lee / Ortiz Ave).
 */

(function (window) {
  'use strict';

  const SLLeeCounty = {
    currentFilter: 'all',
    searchQuery: '',
    isLoading: false,
    selectedLead: null,
    overviewData: null,

    async init() {
      console.log('🏛️ Initializing Lee County Master Command Center...');
      await this.loadOverview();
      await this.loadLeads();
    },

    async loadOverview() {
      try {
        const res = await fetch('/api/lee-county/overview');
        const data = await res.json();
        if (data && data.ok) {
          this.overviewData = data;
          this.renderOverview(data);
        }
      } catch (err) {
        console.error('❌ Failed to fetch Lee County overview:', err);
      }
    },

    renderOverview(data) {
      const m = data.metrics || {};
      const ap = data.autopilot || {};

      const setVal = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
      };

      setVal('leeBookings24h', m.bookings_24h ?? '0');
      setVal('leeInCustody', m.in_custody ?? '0');
      setVal('leeBondReady', m.bond_ready_qualified ?? '0');
      setVal('leeFirstAppearance', m.first_appearance_waiting ?? '0');
      setVal('leePoolValue', '$' + (m.total_bond_pool_value || 0).toLocaleString());
      setVal('leeCommissionPool', '$' + (m.estimated_commission_pool || 0).toLocaleString());
      setVal('leeOutreachSent', m.outreach_total_sent ?? '0');

      // Update sidebar badge
      const badge = document.getElementById('leeCountyBadge');
      if (badge) {
        badge.textContent = m.bond_ready_qualified > 0 ? m.bond_ready_qualified : '—';
        badge.style.background = m.bond_ready_qualified > 0 ? '#10b981' : '';
      }

      // Sync Autopilot switch & pulse dot
      const apToggle = document.getElementById('leeAutopilotToggle');
      const pulseDot = document.getElementById('leePulseDot');
      const apLabel = document.getElementById('leeAutopilotLabel');

      if (apToggle) apToggle.checked = !!ap.enabled;
      if (pulseDot) {
        pulseDot.className = ap.enabled ? 'lee-pulse-dot' : 'lee-pulse-dot off';
      }
      if (apLabel) {
        apLabel.textContent = ap.enabled ? 'Auto-Pilot Active (Speed-to-Contact)' : 'Review-Only Mode (Manual Send)';
      }
    },

    async loadLeads() {
      const container = document.getElementById('leeLeadsContainer');
      if (!container) return;

      this.isLoading = true;
      container.innerHTML = `
        <div style="grid-column: 1/-1; text-align:center; padding: 40px; color:#94a3b8;">
          <div class="spinner" style="margin: 0 auto 12px; width:30px; height:30px; border:3px solid rgba(16,185,129,0.2); border-top-color:#10b981; border-radius:50%; animation: spin 0.8s linear infinite;"></div>
          Scanning Lee County Ortiz Ave Jail Roster & Scored Dossiers...
        </div>
      `;

      try {
        const url = `/api/lee-county/leads?filter=${encodeURIComponent(this.currentFilter)}&search=${encodeURIComponent(this.searchQuery)}&limit=60`;
        const res = await fetch(url);
        const data = await res.json();

        if (data && data.ok) {
          this.renderLeads(data.leads || []);
        } else {
          container.innerHTML = `<div style="grid-column:1/-1; text-align:center; padding:30px; color:#ef4444;">❌ Error loading Lee County leads: ${data.error || 'Unknown error'}</div>`;
        }
      } catch (err) {
        container.innerHTML = `<div style="grid-column:1/-1; text-align:center; padding:30px; color:#ef4444;">❌ Network error loading leads: ${err.message}</div>`;
      } finally {
        this.isLoading = false;
      }
    },

    renderLeads(leads) {
      const container = document.getElementById('leeLeadsContainer');
      if (!container) return;

      if (!leads || leads.length === 0) {
        container.innerHTML = `
          <div style="grid-column: 1/-1; text-align:center; padding: 50px 20px; background:rgba(15,23,42,0.4); border:1px dashed rgba(255,255,255,0.1); border-radius:12px;">
            <div style="font-size:32px; margin-bottom:8px;">🏛️</div>
            <div style="font-size:16px; font-weight:600; color:#f8fafc;">No Lee County arrests found for this filter</div>
            <div style="font-size:13px; color:#64748b; margin-top:4px;">Try selecting "All Bookings" or clearing your search term.</div>
          </div>
        `;
        return;
      }

      container.innerHTML = leads.map(l => {
        const score = l.lead_score || 50;
        let scoreClass = 'ready';
        if (score >= 75) scoreClass = 'hot';
        else if (l.total_bond <= 0) scoreClass = 'pending';

        const terms = l.terms || {};
        const isContacted = l.outreach && l.outreach.status === 'sent';
        const chargesText = (l.charges || []).map(c => typeof c === 'string' ? c : (c.charge || c.description || 'Charge')).join(' · ') || 'Charges pending booking verification';

        return `
          <div class="lee-lead-card" data-booking="${l.booking_number}">
            <div class="lee-card-top">
              <div class="lee-def-info">
                <div class="lee-def-name">${l.full_name}</div>
                <div class="lee-booking-meta">
                  Booking #${l.booking_number || 'PENDING'} · ${l.booking_date ? new Date(l.booking_date).toLocaleDateString() : 'Today'}
                </div>
              </div>
              <div class="lee-score-badge ${scoreClass}">
                Score: ${score}/100
              </div>
            </div>

            <div class="lee-terms-matrix">
              <div class="lee-matrix-item">
                <span class="label">Total Bond</span>
                <span class="val">${l.total_bond > 0 ? '$' + l.total_bond.toLocaleString() : '<span style="color:#f59e0b">First Appearance</span>'}</span>
              </div>
              <div class="lee-matrix-item highlight">
                <span class="label">5% Down Plan</span>
                <span class="val">${terms.down_payment > 0 ? '$' + terms.down_payment.toLocaleString() : 'Pending'}</span>
              </div>
              <div class="lee-matrix-item">
                <span class="label">10% Premium</span>
                <span class="val">${terms.statutory_premium > 0 ? '$' + terms.statutory_premium.toLocaleString() : 'Pending'}</span>
              </div>
              <div class="lee-matrix-item">
                <span class="label">Weekly Terms</span>
                <span class="val">${terms.weekly_4_installments > 0 ? '$' + terms.weekly_4_installments + '/wk' : '—'}</span>
              </div>
            </div>

            <div class="lee-charges-snippet" title="${chargesText}">
              ⚖️ ${chargesText}
            </div>

            ${isContacted ? `
              <div class="lee-outreach-status-tag">
                ✅ Family Contacted (${l.outreach.recipient_name || l.outreach.recipient_phone})
              </div>
            ` : ''}

            <div class="lee-card-actions">
              <button class="lee-btn lee-btn-primary" onclick="SLLeeCounty.openDealStudio('${l.booking_number}')">
                🤝 Make It Work
              </button>
              <button class="lee-btn lee-btn-secondary" onclick="SLLeeCounty.quickFamilyCheck('${l.booking_number}', '${encodeURIComponent(l.full_name)}')">
                👪 Family (${l.booking_number ? 'Scan' : '—'})
              </button>
            </div>
          </div>
        `;
      }).join('');
    },

    setFilter(btn, filterType) {
      document.querySelectorAll('.lee-filter-btn').forEach(b => b.classList.remove('active'));
      if (btn) btn.classList.add('active');
      this.currentFilter = filterType;
      this.loadLeads();
    },

    handleSearch(input) {
      clearTimeout(this._searchTimer);
      this._searchTimer = setTimeout(() => {
        this.searchQuery = input.value.trim();
        this.loadLeads();
      }, 300);
    },

    async toggleAutopilot(checkbox) {
      const enabled = checkbox.checked;
      try {
        const res = await fetch('/api/lee-county/autopilot/config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ autopilot_enabled: enabled }),
        });
        const data = await res.json();
        if (data && data.ok) {
          if (window.SL && window.SL.notify) {
            window.SL.notify(enabled ? '🟢 Lee County Auto-Pilot Activated!' : '🟡 Auto-Pilot Set to Manual Review Mode', 'success');
          }
          await this.loadOverview();
        }
      } catch (err) {
        alert('Failed to update autopilot state: ' + err.message);
        checkbox.checked = !enabled;
      }
    },

    async triggerSweepNow() {
      if (!confirm('Run an immediate Auto-Pilot evaluation & family outreach sweep for all qualifying Lee County arrests?')) return;
      try {
        const res = await fetch('/api/lee-county/autopilot/sweep', { method: 'POST' });
        const data = await res.json();
        if (data && data.ok) {
          alert(`✅ Sweep complete!\nProcessed: ${data.processed_candidates}\nContacted: ${data.contacted_count}`);
          await this.loadOverview();
          await this.loadLeads();
        } else {
          alert(`Sweep skipped/error: ${data.reason || data.error || 'Unknown'}`);
        }
      } catch (err) {
        alert('Error triggering sweep: ' + err.message);
      }
    },

    async openDealStudio(bookingNumber) {
      try {
        const res = await fetch(`/api/lee-county/leads?search=${encodeURIComponent(bookingNumber)}&limit=1`);
        const data = await res.json();
        const lead = (data.leads || [])[0];
        if (!lead) {
          alert('Lead not found');
          return;
        }
        this.selectedLead = lead;

        // Fetch family contacts
        const famRes = await fetch(`/api/lee-county/defendant/${encodeURIComponent(bookingNumber)}/family?name=${encodeURIComponent(lead.full_name)}`);
        const famData = await famRes.json();
        const contacts = famData.contacts || [];

        this.renderDealStudioModal(lead, contacts);
      } catch (err) {
        alert('Failed to open Deal Studio: ' + err.message);
      }
    },

    renderDealStudioModal(lead, contacts) {
      const terms = lead.terms || {};
      const modalHtml = `
        <div class="lee-modal-overlay" id="leeDealModal" onclick="if(event.target===this) SLLeeCounty.closeModal()">
          <div class="lee-modal-content">
            <div class="lee-modal-header">
              <div>
                <h2 style="margin:0; font-size:18px; color:#f8fafc;">🏛️ Lee County "Make It Work" Deal Studio</h2>
                <div style="font-size:12px; color:#94a3b8; margin-top:2px;">
                  Defendant: <strong>${lead.full_name}</strong> · Booking #${lead.booking_number} · Ortiz Ave Facility
                </div>
              </div>
              <button onclick="SLLeeCounty.closeModal()" style="background:none; border:none; color:#94a3b8; font-size:22px; cursor:pointer;">✕</button>
            </div>

            <div class="lee-modal-body">
              <!-- Deal Calculator -->
              <div style="background:rgba(30,41,59,0.7); border:1px solid rgba(255,255,255,0.1); border-radius:12px; padding:16px;">
                <div style="font-size:13px; font-weight:700; color:#10b981; text-transform:uppercase; margin-bottom:10px;">
                  💵 Flexible Payment Plan Calculator
                </div>
                <div style="display:grid; grid-template-columns: repeat(4, 1fr); gap:10px; text-align:center;">
                  <div style="background:rgba(15,23,42,0.6); padding:10px; border-radius:8px;">
                    <div style="font-size:10px; color:#94a3b8;">TOTAL BOND</div>
                    <div style="font-size:16px; font-weight:700; color:#fff;">$${(lead.total_bond || 0).toLocaleString()}</div>
                  </div>
                  <div style="background:rgba(16,185,129,0.15); border:1px solid rgba(16,185,129,0.3); padding:10px; border-radius:8px;">
                    <div style="font-size:10px; color:#a7f3d0;">5% DOWN PAYMENT</div>
                    <div style="font-size:16px; font-weight:700; color:#10b981;">$${(terms.down_payment || 0).toLocaleString()}</div>
                  </div>
                  <div style="background:rgba(15,23,42,0.6); padding:10px; border-radius:8px;">
                    <div style="font-size:10px; color:#94a3b8;">10% STATUTORY</div>
                    <div style="font-size:16px; font-weight:700; color:#fff;">$${(terms.statutory_premium || 0).toLocaleString()}</div>
                  </div>
                  <div style="background:rgba(15,23,42,0.6); padding:10px; border-radius:8px;">
                    <div style="font-size:10px; color:#94a3b8;">WEEKLY (4 WEEKS)</div>
                    <div style="font-size:16px; font-weight:700; color:#38bdf8;">$${terms.weekly_4_installments || 0}/wk</div>
                  </div>
                </div>
              </div>

              <!-- Family & Cosigner Outreach Launcher -->
              <div>
                <div style="font-size:13px; font-weight:700; color:#f8fafc; margin-bottom:10px;">
                  👪 Discovered Family Members & Cosigners (${contacts.length})
                </div>
                ${contacts.length === 0 ? `
                  <div style="padding:16px; background:rgba(30,41,59,0.4); border:1px dashed rgba(255,255,255,0.1); border-radius:8px; font-size:12px; color:#94a3b8; text-align:center;">
                    No auto-linked relatives found yet. You can manually enter a family phone number below.
                  </div>
                ` : `
                  <div style="display:flex; flex-direction:column; gap:8px;">
                    ${contacts.map(c => `
                      <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(30,41,59,0.6); padding:10px 14px; border-radius:8px; border:1px solid rgba(255,255,255,0.06);">
                        <div>
                          <div style="font-weight:600; font-size:13px; color:#fff;">${c.name} <span style="font-size:11px; color:#a7f3d0; background:rgba(16,185,129,0.15); padding:2px 6px; border-radius:4px; margin-left:6px;">${c.relationship}</span></div>
                          <div style="font-size:11px; color:#94a3b8;">${c.phone} · Source: ${c.source} (${Math.round((c.confidence || 0.8) * 100)}% Match)</div>
                        </div>
                        <button class="lee-btn lee-btn-primary" style="flex:0 0 auto; padding:6px 12px;" onclick="SLLeeCounty.dispatchOutreach('${lead.booking_number}', '${c.phone}', '${c.name}')">
                          💬 Send Auto-Text
                        </button>
                      </div>
                    `).join('')}
                  </div>
                `}
              </div>

              <!-- Manual Phone Dispatch -->
              <div style="display:flex; gap:10px; align-items:center; background:rgba(30,41,59,0.4); padding:12px; border-radius:8px;">
                <input type="text" id="leeManualPhone" placeholder="Enter relative cell # (e.g. 239-555-1234)" style="flex:1; background:rgba(15,23,42,0.8); border:1px solid rgba(255,255,255,0.15); padding:8px 12px; border-radius:6px; color:#fff; font-size:12px;">
                <button class="lee-btn lee-btn-primary" style="flex:0 0 auto;" onclick="SLLeeCounty.dispatchManualPhone('${lead.booking_number}')">
                  🚀 Dispatch Outreach
                </button>
              </div>

              <!-- 1-Click Launchpads -->
              <div style="display:flex; gap:10px; border-top:1px solid rgba(255,255,255,0.08); padding-top:16px;">
                <button class="lee-btn lee-btn-secondary" style="background:#0284c7; color:#fff;" onclick="window.open('https://shamrockbailbonds.biz/portal-start?booking=${lead.booking_number}&county=Lee','_blank')">
                  📄 Open DocuSeal Launchpad ↗
                </button>
                <button class="lee-btn lee-btn-secondary" style="background:#7c3aed; color:#fff;" onclick="alert('Routing call to on-call Fort Myers bondsman at (239) 955-0301...')">
                  📞 Transfer to Bondsman Desk
                </button>
              </div>
            </div>
          </div>
        </div>
      `;

      // Remove existing if any
      this.closeModal();
      document.body.insertAdjacentHTML('beforeend', modalHtml);
    },

    closeModal() {
      const el = document.getElementById('leeDealModal');
      if (el) el.remove();
    },

    async dispatchOutreach(bookingNumber, phone, name) {
      if (!confirm(`Send Lee County outreach text to ${name} (${phone})?`)) return;
      try {
        const res = await fetch('/api/lee-county/outreach/send', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            booking_number: bookingNumber,
            recipient_phone: phone,
            recipient_name: name,
          }),
        });
        const data = await res.json();
        if (data && data.ok) {
          alert(`✅ Outreach dispatched successfully to ${phone}!`);
          this.closeModal();
          await this.loadLeads();
          await this.loadOverview();
        } else {
          alert(`❌ Failed to send: ${data.error || 'Unknown error'}`);
        }
      } catch (err) {
        alert('Network error sending outreach: ' + err.message);
      }
    },

    async dispatchManualPhone(bookingNumber) {
      const phoneInput = document.getElementById('leeManualPhone');
      const phone = phoneInput ? phoneInput.value.trim() : '';
      if (!phone) {
        alert('Please enter a valid phone number.');
        return;
      }
      await this.dispatchOutreach(bookingNumber, phone, 'Family Member');
    },

    async quickFamilyCheck(bookingNumber, nameEncoded) {
      await this.openDealStudio(bookingNumber);
    },
  };

  window.SLLeeCounty = SLLeeCounty;
})(window);
