/**
 * sl-enrichment.js — Enrichment Command Center
 *
 * Dashboard frontend for the Tier 1 API stack:
 *   - Attorney Email Harvester (Tomba + Hunter.io)
 *   - Phone Validator (Veriphone + Numverify)
 *   - Email Verifier (EVA + Kickbox + Disify)
 *   - API Usage Dashboard & Provider Status
 */

/* global SL */
(function () {
  'use strict';

  const API_BASE = '/api/enrichment';

  // ── State ──
  let _attorneys = [];
  let _phoneResults = [];
  let _providerStatus = null;

  // ── Public API ──
  window.SLEnrichment = {
    init,
    refreshStatus,
    searchDomain,
    findEmail,
    verifyEmail,
    harvestAttorneys,
    validatePhone,
    validatePhoneBatch,
    loadAttorneys,
  };

  // ══════════════════════════════════════════════════════════════════
  //  INIT
  // ══════════════════════════════════════════════════════════════════

  async function init() {
    _bindUI();
    await refreshStatus();
    await loadAttorneys();
  }

  function _bindUI() {
    // Domain search
    const domainBtn = document.getElementById('enrichDomainSearchBtn');
    if (domainBtn) domainBtn.addEventListener('click', _onDomainSearch);

    // Email find
    const findBtn = document.getElementById('enrichEmailFindBtn');
    if (findBtn) findBtn.addEventListener('click', _onEmailFind);

    // Email verify
    const verifyBtn = document.getElementById('enrichEmailVerifyBtn');
    if (verifyBtn) verifyBtn.addEventListener('click', _onEmailVerify);

    // Attorney harvest
    const harvestBtn = document.getElementById('enrichHarvestBtn');
    if (harvestBtn) harvestBtn.addEventListener('click', _onHarvestAttorneys);

    // Phone validate
    const phoneBtn = document.getElementById('enrichPhoneValidateBtn');
    if (phoneBtn) phoneBtn.addEventListener('click', _onPhoneValidate);

    // Phone batch
    const phoneBatchBtn = document.getElementById('enrichPhoneBatchBtn');
    if (phoneBatchBtn) phoneBatchBtn.addEventListener('click', _onPhoneBatch);

    // Refresh status
    const statusBtn = document.getElementById('enrichRefreshStatusBtn');
    if (statusBtn) statusBtn.addEventListener('click', refreshStatus);
  }

  // ══════════════════════════════════════════════════════════════════
  //  API CALLS
  // ══════════════════════════════════════════════════════════════════

  async function _api(method, path, body = null) {
    const opts = {
      method,
      headers: { 'Content-Type': 'application/json' },
    };
    if (body) opts.body = JSON.stringify(body);
    try {
      const resp = await fetch(`${API_BASE}${path}`, opts);
      return await resp.json();
    } catch (err) {
      console.error(`[Enrichment] API error ${path}:`, err);
      return { success: false, error: err.message };
    }
  }

  async function refreshStatus() {
    _providerStatus = await _api('GET', '/status');
    _renderStatus(_providerStatus);
    // Also fetch email usage
    const usage = await _api('GET', '/email/usage');
    _renderUsage(usage);
    // Phone stats
    const phoneStats = await _api('GET', '/phone/stats');
    _renderPhoneStats(phoneStats);
    return _providerStatus;
  }

  async function searchDomain(domain) {
    return await _api('POST', '/email/search-domain', { domain });
  }

  async function findEmail(domain, firstName, lastName) {
    return await _api('POST', '/email/find', {
      domain,
      first_name: firstName,
      last_name: lastName,
    });
  }

  async function verifyEmail(email) {
    return await _api('POST', '/email/verify', { email });
  }

  async function harvestAttorneys(domains) {
    return await _api('POST', '/email/harvest-attorneys', { domains });
  }

  async function validatePhone(phone, skipCache = false) {
    return await _api('POST', '/phone/validate', { phone, skip_cache: skipCache });
  }

  async function validatePhoneBatch(phones, skipCache = false) {
    return await _api('POST', '/phone/validate-batch', { phones, skip_cache: skipCache });
  }

  async function loadAttorneys(skip = 0, limit = 50) {
    const result = await _api('GET', `/email/attorneys?skip=${skip}&limit=${limit}`);
    if (result.success) {
      _attorneys = result.attorneys || [];
      _renderAttorneyTable(_attorneys, result.total);
    }
    return result;
  }

  // ══════════════════════════════════════════════════════════════════
  //  EVENT HANDLERS
  // ══════════════════════════════════════════════════════════════════

  async function _onDomainSearch() {
    const input = document.getElementById('enrichDomainInput');
    const domain = (input?.value || '').trim();
    if (!domain) return _flash('Enter a domain', 'warn');

    _setLoading('enrichDomainSearchBtn', true);
    const result = await searchDomain(domain);
    _setLoading('enrichDomainSearchBtn', false);

    _renderDomainResults(result);
    if (result.success) _flash(`Found ${result.total} emails at ${domain}`, 'success');
    else _flash(result.error || 'Search failed', 'error');
  }

  async function _onEmailFind() {
    const domain = (document.getElementById('enrichFindDomain')?.value || '').trim();
    const first = (document.getElementById('enrichFindFirst')?.value || '').trim();
    const last = (document.getElementById('enrichFindLast')?.value || '').trim();
    if (!domain || !first || !last) return _flash('Fill all fields', 'warn');

    _setLoading('enrichEmailFindBtn', true);
    const result = await findEmail(domain, first, last);
    _setLoading('enrichEmailFindBtn', false);

    _renderFindResult(result);
  }

  async function _onEmailVerify() {
    const email = (document.getElementById('enrichVerifyEmail')?.value || '').trim();
    if (!email) return _flash('Enter an email', 'warn');

    _setLoading('enrichEmailVerifyBtn', true);
    const result = await verifyEmail(email);
    _setLoading('enrichEmailVerifyBtn', false);

    _renderVerifyResult(result);
  }

  async function _onHarvestAttorneys() {
    const textarea = document.getElementById('enrichHarvestDomains');
    const raw = (textarea?.value || '').trim();
    if (!raw) return _flash('Enter at least one domain', 'warn');

    const domains = raw.split(/[\n,]+/).map(d => d.trim()).filter(Boolean);
    if (domains.length === 0) return _flash('No valid domains found', 'warn');
    if (domains.length > 10) return _flash('Max 10 domains per batch (free tier)', 'warn');

    _setLoading('enrichHarvestBtn', true);
    const result = await harvestAttorneys(domains);
    _setLoading('enrichHarvestBtn', false);

    if (result.success) {
      _flash(`Harvested ${result.total_emails_found} emails from ${result.domains_searched} domains`, 'success');
      await loadAttorneys();
    } else {
      _flash(result.error || 'Harvest failed', 'error');
    }
  }

  async function _onPhoneValidate() {
    const input = document.getElementById('enrichPhoneInput');
    const phone = (input?.value || '').trim();
    if (!phone) return _flash('Enter a phone number', 'warn');

    _setLoading('enrichPhoneValidateBtn', true);
    const result = await validatePhone(phone);
    _setLoading('enrichPhoneValidateBtn', false);

    _renderPhoneResult(result);
  }

  async function _onPhoneBatch() {
    const textarea = document.getElementById('enrichPhoneBatchInput');
    const raw = (textarea?.value || '').trim();
    if (!raw) return _flash('Enter phone numbers', 'warn');

    const phones = raw.split(/[\n,]+/).map(p => p.trim()).filter(Boolean);
    if (phones.length === 0) return _flash('No valid phones found', 'warn');

    _setLoading('enrichPhoneBatchBtn', true);
    const result = await validatePhoneBatch(phones);
    _setLoading('enrichPhoneBatchBtn', false);

    _renderPhoneBatchResults(result);
  }

  // ══════════════════════════════════════════════════════════════════
  //  RENDERERS
  // ══════════════════════════════════════════════════════════════════

  function _renderStatus(data) {
    const el = document.getElementById('enrichProviderStatus');
    if (!el || !data?.providers) return;

    const providers = data.providers;
    const cache = data.cache || {};

    el.innerHTML = Object.entries(providers).map(([name, p]) => `
      <div class="enrich-provider-card ${p.configured ? 'online' : 'offline'}">
        <div class="provider-indicator ${p.configured ? 'active' : 'inactive'}"></div>
        <div class="provider-info">
          <strong>${_providerLabel(name)}</strong>
          <span class="provider-type">${p.type.replace(/_/g, ' ')}</span>
        </div>
        <div class="provider-limit">${p.free_limits}</div>
      </div>
    `).join('') + `
      <div class="enrich-cache-summary">
        <span>📧 ${cache.email_verifications_cached || 0} cached</span>
        <span>📱 ${cache.phone_validations_cached || 0} cached</span>
        <span>👔 ${cache.attorney_contacts || 0} attorneys</span>
      </div>
    `;
  }

  function _renderUsage(data) {
    const el = document.getElementById('enrichUsageStats');
    if (!el || !data?.usage) return;

    const t = data.usage.tomba;
    const h = data.usage.hunter;

    el.innerHTML = `
      <div class="usage-row">
        <span class="usage-label">Tomba</span>
        ${t ? `<span class="usage-val">${JSON.stringify(t)}</span>` : '<span class="usage-val muted">Not fetched</span>'}
      </div>
      <div class="usage-row">
        <span class="usage-label">Hunter</span>
        ${h ? `
          <span class="usage-val">
            Searches: ${h.searches_used}/${h.searches_available} |
            Verifications: ${h.verifications_used}/${h.verifications_available}
          </span>
        ` : '<span class="usage-val muted">Not fetched</span>'}
      </div>
    `;
  }

  function _renderPhoneStats(data) {
    const el = document.getElementById('enrichPhoneStats');
    if (!el || !data?.success) return;

    const lt = data.line_types || {};
    const rs = data.risk_signals || {};

    el.innerHTML = `
      <div class="phone-stat-grid">
        <div class="phone-stat"><span class="stat-num">${data.total_validated}</span><span class="stat-lbl">Total</span></div>
        <div class="phone-stat good"><span class="stat-num">${data.valid}</span><span class="stat-lbl">Valid</span></div>
        <div class="phone-stat bad"><span class="stat-num">${data.invalid}</span><span class="stat-lbl">Invalid</span></div>
      </div>
      <div class="phone-line-types">
        ${Object.entries(lt).map(([type, count]) => `
          <span class="line-type-badge ${_lineTypeClass(type)}">${type}: ${count}</span>
        `).join('')}
      </div>
      ${Object.keys(rs).length ? `
      <div class="risk-signals">
        <strong>Risk Signals:</strong>
        ${Object.entries(rs).map(([sig, count]) => `
          <span class="risk-badge ${_riskClass(sig)}">${sig.replace(/_/g, ' ')}: ${count}</span>
        `).join('')}
      </div>` : ''}
    `;
  }

  function _renderDomainResults(result) {
    const el = document.getElementById('enrichDomainResults');
    if (!el) return;

    if (!result.success) {
      el.innerHTML = `<div class="enrich-error">${result.error || 'Search failed'}</div>`;
      return;
    }

    const emails = result.emails || [];
    el.innerHTML = `
      <div class="domain-result-header">
        <strong>${result.organization || result.domain}</strong>
        <span class="result-meta">${result.total} emails found via ${result.provider}${result.cached ? ' (cached)' : ''}</span>
      </div>
      <table class="enrich-table">
        <thead><tr><th>Email</th><th>Name</th><th>Position</th><th>Type</th><th>Confidence</th></tr></thead>
        <tbody>
          ${emails.map(e => `
            <tr>
              <td><a href="mailto:${e.email}">${e.email}</a></td>
              <td>${e.first_name} ${e.last_name}</td>
              <td>${e.position || '—'}</td>
              <td><span class="email-type-badge ${e.type}">${e.type || '—'}</span></td>
              <td>${e.confidence}%</td>
            </tr>
          `).join('')}
          ${emails.length === 0 ? '<tr><td colspan="5" class="muted">No emails found</td></tr>' : ''}
        </tbody>
      </table>
    `;
  }

  function _renderFindResult(result) {
    const el = document.getElementById('enrichFindResult');
    if (!el) return;

    if (result.success && result.email) {
      el.innerHTML = `
        <div class="find-result success">
          <span class="find-email">${result.email}</span>
          <span class="find-confidence">${result.confidence}% confidence</span>
          <span class="find-provider">via ${result.provider}</span>
        </div>
      `;
    } else {
      el.innerHTML = `<div class="find-result fail">No email found${result.error ? ': ' + result.error : ''}</div>`;
    }
  }

  function _renderVerifyResult(result) {
    const el = document.getElementById('enrichVerifyResult');
    if (!el) return;

    if (!result.success) {
      el.innerHTML = `<div class="verify-result fail">${result.error || 'Verification failed'}</div>`;
      return;
    }

    const badges = [];
    if (result.valid) badges.push('<span class="vbadge good">✓ Valid</span>');
    else badges.push('<span class="vbadge bad">✗ Invalid</span>');

    if (result.deliverable) badges.push('<span class="vbadge good">Deliverable</span>');
    else if (result.deliverable === false) badges.push('<span class="vbadge bad">Undeliverable</span>');

    if (result.disposable) badges.push('<span class="vbadge warn">⚠ Disposable</span>');
    if (result.free_provider) badges.push('<span class="vbadge muted">Free Provider</span>');
    if (result.mx_found) badges.push('<span class="vbadge good">MX ✓</span>');

    el.innerHTML = `
      <div class="verify-result">
        <div class="verify-badges">${badges.join('')}</div>
        <div class="verify-meta">Provider: ${result.provider}${result.cached ? ' (cached)' : ''} | Score: ${result.score ?? '—'}</div>
      </div>
    `;
  }

  function _renderPhoneResult(result) {
    const el = document.getElementById('enrichPhoneResult');
    if (!el) return;

    if (!result.success && !result.valid) {
      el.innerHTML = `<div class="phone-result fail">${result.error || 'Validation failed'}</div>`;
      return;
    }

    const signals = result.risk_signals || [];
    const lineClass = _lineTypeClass(result.line_type);

    el.innerHTML = `
      <div class="phone-result ${result.valid ? 'valid' : 'invalid'}">
        <div class="phone-header">
          <span class="phone-number">${result.phone_international || result.phone_e164 || result.phone}</span>
          <span class="phone-valid-badge ${result.valid ? 'good' : 'bad'}">${result.valid ? '✓ Valid' : '✗ Invalid'}</span>
        </div>
        <div class="phone-details">
          <div class="phone-detail"><label>Line Type</label><span class="line-type-badge ${lineClass}">${result.line_type || 'Unknown'}</span></div>
          <div class="phone-detail"><label>Carrier</label><span>${result.carrier || 'Unknown'}</span></div>
          <div class="phone-detail"><label>Country</label><span>${result.country || 'Unknown'} (${result.country_code || '—'})</span></div>
          <div class="phone-detail"><label>Provider</label><span>${result.provider || 'Unknown'}${result.cached ? ' (cached)' : ''}</span></div>
        </div>
        ${signals.length ? `
        <div class="phone-signals">
          ${signals.map(s => `<span class="risk-badge ${_riskClass(s)}">${s.replace(/_/g, ' ')}</span>`).join('')}
        </div>` : ''}
      </div>
    `;
  }

  function _renderPhoneBatchResults(result) {
    const el = document.getElementById('enrichPhoneBatchResults');
    if (!el) return;

    if (!result.success) {
      el.innerHTML = `<div class="enrich-error">${result.error || 'Batch validation failed'}</div>`;
      return;
    }

    el.innerHTML = `
      <div class="batch-summary">
        <span class="batch-stat">${result.total} total</span>
        <span class="batch-stat good">${result.valid} valid</span>
        <span class="batch-stat bad">${result.invalid} invalid</span>
        <span class="batch-stat">${result.mobile} mobile</span>
        <span class="batch-stat warn">${result.voip} VOIP</span>
      </div>
      <table class="enrich-table">
        <thead><tr><th>Phone</th><th>Valid</th><th>Type</th><th>Carrier</th><th>Signals</th></tr></thead>
        <tbody>
          ${(result.results || []).map(r => `
            <tr class="${r.valid ? '' : 'row-invalid'}">
              <td>${r.phone_e164 || r.phone || r.phone_original || '—'}</td>
              <td>${r.valid ? '<span class="vbadge good">✓</span>' : '<span class="vbadge bad">✗</span>'}</td>
              <td><span class="line-type-badge ${_lineTypeClass(r.line_type)}">${r.line_type || '—'}</span></td>
              <td>${r.carrier || '—'}</td>
              <td>${(r.risk_signals || []).map(s => `<span class="risk-badge sm ${_riskClass(s)}">${s.replace(/_/g, ' ')}</span>`).join(' ')}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  }

  function _renderAttorneyTable(attorneys, total) {
    const el = document.getElementById('enrichAttorneyTable');
    if (!el) return;

    if (!attorneys.length) {
      el.innerHTML = '<div class="muted" style="padding:20px;text-align:center">No attorneys harvested yet. Enter law firm domains above to start.</div>';
      return;
    }

    el.innerHTML = `
      <div class="attorney-header"><strong>${total} attorney contacts</strong></div>
      <table class="enrich-table">
        <thead><tr><th>Email</th><th>Name</th><th>Organization</th><th>Position</th><th>Confidence</th><th>Status</th></tr></thead>
        <tbody>
          ${attorneys.map(a => `
            <tr>
              <td><a href="mailto:${a.email}">${a.email}</a></td>
              <td>${a.first_name || ''} ${a.last_name || ''}</td>
              <td>${a.organization || a.domain || '—'}</td>
              <td>${a.position || '—'}</td>
              <td>${a.confidence || '—'}%</td>
              <td><span class="outreach-badge ${a.outreach_status}">${a.outreach_status || 'new'}</span></td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;
  }

  // ══════════════════════════════════════════════════════════════════
  //  HELPERS
  // ══════════════════════════════════════════════════════════════════

  function _providerLabel(name) {
    const labels = {
      tomba: 'Tomba.io', hunter: 'Hunter.io', veriphone: 'Veriphone',
      numverify: 'Numverify', eva: 'EVA', kickbox: 'Kickbox', disify: 'Disify',
    };
    return labels[name] || name;
  }

  function _lineTypeClass(type) {
    if (!type) return '';
    const t = type.toLowerCase();
    if (['mobile', 'cell', 'wireless'].includes(t)) return 'lt-mobile';
    if (['voip', 'virtual'].includes(t)) return 'lt-voip';
    if (['landline', 'fixed_line'].includes(t)) return 'lt-landline';
    if (t === 'toll_free') return 'lt-tollfree';
    return 'lt-unknown';
  }

  function _riskClass(signal) {
    if (!signal) return '';
    if (['voip_phone', 'known_voip_carrier', 'toll_free_phone', 'non_us_phone'].includes(signal)) return 'risk-high';
    if (['invalid_phone', 'invalid_length', 'unknown_line_type'].includes(signal)) return 'risk-critical';
    if (['landline_phone'].includes(signal)) return 'risk-medium';
    if (['mobile_phone'].includes(signal)) return 'risk-low';
    return '';
  }

  function _setLoading(btnId, loading) {
    const btn = document.getElementById(btnId);
    if (!btn) return;
    if (loading) {
      btn.dataset.origText = btn.textContent;
      btn.textContent = '⏳';
      btn.disabled = true;
    } else {
      btn.textContent = btn.dataset.origText || btn.textContent;
      btn.disabled = false;
    }
  }

  function _flash(msg, type = 'info') {
    const el = document.getElementById('enrichFlash');
    if (!el) { console.log(`[Enrichment] ${type}: ${msg}`); return; }
    el.textContent = msg;
    el.className = `enrich-flash ${type}`;
    el.style.display = 'block';
    setTimeout(() => { el.style.display = 'none'; }, 4000);
  }

})();
