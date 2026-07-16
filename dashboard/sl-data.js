/* ShamrockLeads — Data Fetch, Render, Command Center */

function _relTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return '';
  const mins = Math.round((Date.now() - d) / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

// ── Lead Explorer Fetch ──
async function applyFilters() {
  SL_STATE.custody = document.getElementById('custodyFilter')?.value || '';
  SL_STATE.status = document.getElementById('statusFilter')?.value || '';
  SL_STATE.stateCode = document.getElementById('stateFilter')?.value || '';
  SL_STATE.limit = parseInt(document.getElementById('limitSelect')?.value || 50);
  const p = new URLSearchParams({
    page: SL_STATE.page,
    limit: SL_STATE.limit,
    sort: SL_STATE.sort || 'scraped_at',
    order: SL_STATE.order || 'desc',
  });
  if (SL_STATE.selectedCounties.length) p.set('county', SL_STATE.selectedCounties.join(','));
  if (SL_STATE.stateCode) p.set('state', SL_STATE.stateCode);
  if (SL_STATE.days) p.set('days', SL_STATE.days);
  if (SL_STATE.custody) p.set('custody', SL_STATE.custody);
  if (SL_STATE.status) p.set('status', SL_STATE.status);
  if (SL_STATE.minBond) p.set('min_bond', SL_STATE.minBond);
  if (SL_STATE.search) p.set('search', SL_STATE.search);
  try {
    const r = await fetch(`${API}/api/leads?${p}`);
    if (!r.ok) { console.warn('[Leads] HTTP', r.status); return; }
    const ct = r.headers.get('content-type') || '';
    if (!ct.includes('application/json')) { console.warn('[Leads] non-JSON response'); return; }
    const d = await r.json();
    if (d.error) { console.warn('[Leads] API error', d.error); return; }
    SL_STATE.leads = d.leads || []; SL_STATE.total = d.total || 0; SL_STATE.pages = d.pages || 1;
    // Always refresh county options when server returns a fuller list
    if (d.counties && d.counties.length) {
      if (SL_STATE.counties.length !== d.counties.length) buildCountyOptions(d.counties);
      else SL_STATE.counties = d.counties;
    }
    const badge = document.getElementById('leadsBadge');
    if (badge) badge.textContent = SL_STATE.total.toLocaleString();
    const activity = d.activity || {};
    const fresh = activity.scraped_last_hour != null
      ? ` · ${activity.scraped_last_hour.toLocaleString()} scraped last hour`
      : '';
    const meta = document.getElementById('resultsMeta');
    if (meta) {
      meta.textContent = `${SL_STATE.total.toLocaleString()} results · Page ${SL_STATE.page}/${SL_STATE.pages}${fresh}`;
    }
    renderLeads(); renderPills(); renderPagination();
  } catch(e) { console.error('applyFilters error:', e); }
}

function renderLeads() {
  const tb = document.getElementById('leadsBody');
  if (!tb) return;
  if (!SL_STATE.leads.length) { tb.innerHTML = '<tr><td colspan="10" class="loading">No leads match current filters</td></tr>'; return; }
  const stateColors = { FL: '#00d4aa', GA: '#f59e0b', SC: '#8b5cf6', NC: '#3b82f6', TN: '#ef4444', TX: '#eab308', LA: '#ec4899' };
  tb.innerHTML = SL_STATE.leads.map(l => {
    const bond = l.bond_amount || 0;
    const bc = bond >= 10000 ? 'bond-high' : bond >= 2500 ? 'bond-mid' : 'bond-low';
    const sc = (l.lead_status||'').toLowerCase();
    const scoreCls = sc === 'hot' ? 'score-hot' : sc === 'warm' ? 'score-warm' : sc === 'disqualified' ? 'score-disq' : 'score-cold';
    const statusLabel = sc === 'initial' || sc === 'scrape' ? 'Unscored' : (l.lead_status || '');
    const charges = (l.charges||'').length > 50 ? (l.charges||'').slice(0,47)+'…' : (l.charges||'—');
    const custVal = (l.status||'').trim();
    const custLower = custVal.toLowerCase();
    const custClass = custLower.includes('custody') ? 'custody' : custLower.includes('release') || custLower.includes('bonded') ? 'released' : custLower.includes('not in') ? 'released' : 'other';
    const bkEsc = String(l.booking_number||'').replace(/"/g,'&quot;');
    const custDropdown = `<select class="def-status-badge ${custClass}" style="cursor:pointer;border:1px solid var(--border);background:transparent;padding:2px 6px;font-size:11px;border-radius:6px" onchange="updateCustody('${bkEsc}',this.value,this)"><option value="" ${!custVal?'selected':''}>${custVal||'—'}</option><option value="In Custody" ${'In Custody'===custVal?'selected':''}>In Custody</option><option value="Not In Custody" ${'Not In Custody'===custVal?'selected':''}>Not In Custody</option><option value="Released" ${'Released'===custVal?'selected':''}>Released</option><option value="Bonded Out" ${'Bonded Out'===custVal?'selected':''}>Bonded Out</option></select>`;
    const courtCls = isCourtSoon(l.court_date) ? 'court-soon' : '';
    const st = (l.state || 'FL').toUpperCase();
    const stColor = stateColors[st] || '#64748b';
    const scrapedRel = _relTime(l.scraped_at);
    const arrestDisp = fmtDate(l.arrest_date || l.booking_date);
    return `<tr>
      <td><strong>${l.full_name||'Unknown'}</strong><br><span style="color:var(--muted);font-size:11px">${[l.sex,l.race,l.dob].filter(Boolean).join(' · ')}</span></td>
      <td><span style="background:${stColor}22;color:${stColor};border:1px solid ${stColor}44;padding:2px 6px;border-radius:4px;font-size:11px;font-weight:700">${st}</span></td>
      <td>${(l.county&&l.county!=='—')?`<span class="county-badge" data-county="${l.county}">${l.county}</span>`:'—'}</td>
      <td title="${(l.charges||'').replace(/"/g,'&quot;')}">${charges}</td>
      <td class="${bc}">$${bond.toLocaleString()}</td>
      <td><span class="score-pill ${scoreCls}">${l.lead_score||0} ${statusLabel}</span></td>
      <td>${custDropdown}</td>
      <td>${arrestDisp}<br><span style="color:var(--muted);font-size:10px" title="${l.scraped_at||''}">${scrapedRel ? 'scraped '+scrapedRel : ''}</span></td>
      <td class="${courtCls}">${l.court_date || '—'}</td>
      <td>${l.detail_url ? `<a href="${l.detail_url}" target="_blank" style="color:var(--accent)">🔗</a>` : '—'}</td>
    </tr>`;
  }).join('');
}

// ── Filter Pills ──
function renderPills() {
  const el = document.getElementById('filterPills'); const pills = [];
  if (SL_STATE.selectedCounties.length) pills.push(mkPill(`Counties: ${SL_STATE.selectedCounties.length}`, () => { SL_STATE.selectedCounties = []; buildCountyOptions(SL_STATE.counties); applyFilters(); }));
  if (SL_STATE.stateCode) pills.push(mkPill(`State: ${SL_STATE.stateCode}`, () => { const sf = document.getElementById('stateFilter'); if (sf) sf.value=''; SL_STATE.stateCode=''; applyFilters(); }));
  if (SL_STATE.days) pills.push(mkPill(`${SL_STATE.days}d range`, () => { SL_STATE.days = 0; applyFilters(); }));
  if (SL_STATE.minBond) pills.push(mkPill(`$${SL_STATE.minBond.toLocaleString()}+ bond`, () => { SL_STATE.minBond = 0; applyFilters(); }));
  if (SL_STATE.custody) pills.push(mkPill(`Custody: ${SL_STATE.custody}`, () => { document.getElementById('custodyFilter').value=''; applyFilters(); }));
  if (SL_STATE.status) pills.push(mkPill(`Score: ${SL_STATE.status}`, () => { document.getElementById('statusFilter').value=''; applyFilters(); }));
  if (SL_STATE.search) pills.push(mkPill(`"${SL_STATE.search}"`, () => { document.getElementById('searchInput').value=''; SL_STATE.search=''; applyFilters(); }));
  if (pills.length) pills.push('<span class="filter-clear-all" onclick="clearAll()">Clear all</span>');
  el.innerHTML = pills.join('');
}
function mkPill(label, onclick) { const id = 'p'+Math.random().toString(36).slice(2,6); window['_pill_'+id] = onclick; return `<span class="filter-pill">${label}<span class="pill-close" onclick="window._pill_${id}()">✕</span></span>`; }
function clearAll() {
  SL_STATE.selectedCounties=[]; SL_STATE.days=0; SL_STATE.minBond=0; SL_STATE.search=''; SL_STATE.custody=''; SL_STATE.status=''; SL_STATE.stateCode='';
  document.getElementById('searchInput').value=''; document.getElementById('custodyFilter').value=''; document.getElementById('statusFilter').value='';
  const sf = document.getElementById('stateFilter'); if (sf) sf.value='';
  document.querySelectorAll('#dateRange button').forEach((b,i) => b.classList.toggle('active', i===4));
  document.querySelectorAll('#bondRange button').forEach((b,i) => b.classList.toggle('active', i===0));
  buildCountyOptions(SL_STATE.counties); applyFilters();
}

// ── Pagination ──
function renderPagination() {
  document.getElementById('pagination').innerHTML = `<button ${SL_STATE.page<=1?'disabled':''} onclick="goPage(${SL_STATE.page-1})">← Prev</button><span>Page ${SL_STATE.page} of ${SL_STATE.pages}</span><button ${SL_STATE.page>=SL_STATE.pages?'disabled':''} onclick="goPage(${SL_STATE.page+1})">Next →</button>`;
}
function goPage(p) { SL_STATE.page = p; applyFilters(); document.getElementById('tabLeads').scrollIntoView({behavior:'smooth'}); }

// ── Command Center ──
async function loadDashboard() {
  try {
    const safeFetchJSON = async (url) => {
      const r = await fetch(url);
      if (!r.ok) return null;
      const ct = r.headers.get('content-type') || '';
      if (!ct.includes('application/json')) return null;
      return r.json();
    };
    const [s, m, cmd] = await Promise.all([
      safeFetchJSON(`${API}/api/status`),
      safeFetchJSON(`${API}/api/mongo-stats`),
      safeFetchJSON(`${API}/api/command`).catch(()=>null)
    ]);
    if (!s || !m) { console.warn('[Dashboard] core status endpoints unavailable'); return; }
    SL_STATE.scraperData = s; SL_STATE.mongoData = m;
    const sc = s.scrapers||{}, by = m.by_county||{}, scores = m.scores||{};
    // Use server-computed counts if available (new /api/status response with 3-layer logic)
    const totalReg = s.total_registered || Object.keys(sc).length;
    const ok = s.active_count != null ? s.active_count : Object.values(sc).filter(x=>x.status==='ok').length;
    const err = s.error_count != null ? s.error_count : Object.values(sc).filter(x=>x.status==='error').length;
    const neverRun = s.never_run_count != null ? s.never_run_count : 0;

    document.getElementById('kpiRecords').textContent = fmt(m.total_records||0);
    document.getElementById('kpiRecordsSub').textContent = `${Object.keys(by).length} counties`;
    document.getElementById('kpiActive').textContent = `${ok} / ${totalReg}`;
    document.getElementById('kpiActiveSub').textContent = `${err} errors · ${neverRun} never run`;
    document.getElementById('kpiHot').textContent = scores.hot||0;
    // Dynamic sub-label with warm count
    const hotSub = document.querySelector('#kpiHot + .stat-sub') || document.getElementById('kpiHot')?.parentElement?.querySelector('.stat-sub');
    if (hotSub) hotSub.textContent = `score ≥ 70 · ${scores.warm||0} warm`;

    // Command center data
    if (cmd) {
      document.getElementById('kpiBondReady').textContent = cmd.bond_ready_count||0;
      document.getElementById('kpiPipeline').textContent = '$'+fmt(cmd.pipeline_total||0);
      document.getElementById('kpiPipelineSub').textContent = 'est. premium: $'+fmt(cmd.premium_estimate||0);

      // Bond-ready queue table
      const bq = cmd.bond_ready || [];
      const totalBondReady = cmd.bond_ready_count || bq.length;
      const shownLabel = bq.length < totalBondReady ? `showing ${bq.length} of ${totalBondReady}` : `${totalBondReady} defendants`;
      document.getElementById('bondQueueMeta').textContent = `${shownLabel} · $${(cmd.pipeline_total||0).toLocaleString()} total bond`;
      document.getElementById('bondQueueBody').innerHTML = bq.length ? bq.map(l => {
        const bond = l.bond_amount||0;
        const prem = Math.max(100, bond * 0.1);
        const bc = bond>=10000?'bond-high':bond>=2500?'bond-mid':'bond-low';
        const charges = (l.charges||'').length > 60 ? (l.charges||'').slice(0,57)+'…' : (l.charges||'—');
        return `<tr>
          <td><strong>${l.full_name||'?'}</strong><br><span style="color:var(--muted);font-size:11px">${l.dob||''} · ${l.booking_number||''}</span></td>
          <td>${(l.county&&l.county!=='—')?`<span class="county-badge" data-county="${l.county}">${l.county}</span>`:'—'}</td>
          <td class="${bc}">$${bond.toLocaleString()}</td>
          <td style="color:var(--success);font-weight:600">$${prem.toLocaleString()}</td>
          <td><span class="score-pill ${{'hot':'score-hot','warm':'score-warm','cold':'score-cold','disqualified':'score-disq'}[(l.lead_status||'').toLowerCase()]||'score-warm'}">${l.lead_score||0}</span></td>
          <td title="${(l.charges||'').replace(/"/g,'&quot;')}" style="font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${charges}</td>
          <td><button class="btn-write-bond" onclick="openBondModal('${(l.full_name||'').replace(/'/g,"\\'")}',${bond},'${l.county||''}','${l.booking_number||''}')">✍️ Write</button></td>
        </tr>`;
      }).join('') : '<tr><td colspan="7" class="loading">No bond-ready defendants</td></tr>';

      // Custody by county — with state color badge
      const STATE_COLORS_CMD = { FL: '#00d4aa', GA: '#f59e0b', SC: '#8b5cf6', NC: '#3b82f6' };
      const cbc = cmd.custody_by_county || [];
      document.getElementById('custodyCountyList').innerHTML = cbc.length ? cbc.map(c => {
        const st = (c.state || 'FL').toUpperCase();
        const stColor = STATE_COLORS_CMD[st] || '#64748b';
        return `<div class="county-row">
          <span style="background:${stColor}22;color:${stColor};border:1px solid ${stColor}44;padding:1px 5px;border-radius:4px;font-size:10px;font-weight:700;margin-right:5px">${st}</span>
          <span class="county-name">${c.county}</span>
          <span style="color:var(--success);font-size:12px;font-weight:600">$${fmt(c.total_bond)}</span>
          <span class="county-count">${c.count} in custody</span>
        </div>`;
      }).join('') : '<div class="loading">No in-custody defendants</div>';

      // Recent activity — with state badge and charge preview
      const ra = cmd.recent_activity || [];
      document.getElementById('cmdRecentActivity').innerHTML = ra.length ? ra.map(l => {
        const sc2 = (l.lead_status||'').toLowerCase();
        const dot = sc2==='hot'?'🔥':sc2==='warm'?'🟡':'⚪';
        const st2 = (l.state || 'FL').toUpperCase();
        const stColor2 = STATE_COLORS_CMD[st2] || '#64748b';
        const charge = (l.charges||'').length > 38 ? (l.charges||'').slice(0,35)+'…' : (l.charges||'—');
        return `<div class="county-row" style="flex-wrap:wrap;gap:2px">
          <span style="background:${stColor2}22;color:${stColor2};border:1px solid ${stColor2}44;padding:1px 5px;border-radius:4px;font-size:10px;font-weight:700">${st2}</span>
          <span class="county-name" style="font-size:12px">${dot} ${l.full_name||'?'}</span>
          <span style="color:var(--accent);font-size:12px;font-weight:700">$${(l.bond_amount||0).toLocaleString()}</span>
          <span class="county-count" style="width:100%;padding-left:4px;font-size:11px;color:var(--muted)">${l.county||'—'} · ${charge} · ${timeAgo(l.scraped_at)}</span>
        </div>`;
      }).join('') : '<div class="loading">No recent activity</div>';

      // State breakdown strip
      const sb = cmd.state_breakdown || {};
      const STATE_META_CMD = {
        FL: { name: 'Florida',        emoji: '🌴', color: '#00d4aa' },
        GA: { name: 'Georgia',        emoji: '🍑', color: '#f59e0b' },
        SC: { name: 'South Carolina', emoji: '🌙', color: '#8b5cf6' },
        NC: { name: 'North Carolina', emoji: '🦅', color: '#3b82f6' },
      };
      ['FL','GA','SC','NC'].forEach(st => {
        const el = document.getElementById(`cmdState${st}`);
        if (!el) return;
        const d = sb[st] || {};
        const meta = STATE_META_CMD[st];
        el.classList.remove('cmd-state-chip-skeleton');
        el.style.setProperty('--chip-color', meta.color);
        el.innerHTML = `
          <span class="cmd-chip-flag">${meta.emoji}</span>
          <span class="cmd-chip-abbr" style="color:${meta.color}">${st}</span>
          <span class="cmd-chip-stat">${(d.total||0).toLocaleString()} <span class="cmd-chip-lbl">arrests</span></span>
          <span class="cmd-chip-stat">${(d.last_24h||0).toLocaleString()} <span class="cmd-chip-lbl">today</span></span>
          <span class="cmd-chip-stat" style="color:#ef4444">🔥 ${(d.hot_leads||0).toLocaleString()} <span class="cmd-chip-lbl">hot</span></span>
          <span class="cmd-chip-stat" style="color:${meta.color}">$${fmt(d.pipeline||0)} <span class="cmd-chip-lbl">pipeline</span></span>
        `;
      });
    }

    // Hot lead audio alert
    if (SL_STATE.prevHotCount >= 0 && (scores.hot||0) > SL_STATE.prevHotCount) playHotAlert();
    SL_STATE.prevHotCount = scores.hot||0;

    if (SL_STATE.counties.length === 0 && m.by_county) buildCountyOptions(Object.keys(m.by_county).sort());
  } catch(e) { console.error('Dashboard load error:', e); }
}

/* ══════════════════════════════════════════════════════════════════
   COMMAND CENTER KPI AUTO-REFRESH (every 60 seconds, targeted)
   Refreshes only the KPI cards without reloading the full page.
   This runs independently of the 30s full-page refresh so the
   Command Center always shows live data.
   ══════════════════════════════════════════════════════════════════ */
(function startKpiAutoRefresh() {
  // Only start if we're on the dashboard page
  if (typeof loadDashboard !== 'function') return;

  let _kpiRefreshTimer = null;
  const KPI_REFRESH_INTERVAL_MS = 60_000; // 60 seconds

  function _scheduleKpiRefresh() {
    _kpiRefreshTimer = setTimeout(async () => {
      try {
        await loadDashboard();
        // Flash the KPI cards to signal a refresh
        document.querySelectorAll('.stat-card').forEach(card => {
          card.style.transition = 'opacity 0.2s';
          card.style.opacity = '0.6';
          setTimeout(() => { card.style.opacity = '1'; }, 200);
        });
      } catch (e) {
        console.warn('[KPI auto-refresh] error:', e);
      }
      _scheduleKpiRefresh(); // reschedule
    }, KPI_REFRESH_INTERVAL_MS);
  }

  // Start after initial load
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _scheduleKpiRefresh);
  } else {
    _scheduleKpiRefresh();
  }
})();

/* ══════════════════════════════════════════════════════════════════
   LEAD EXPLORER AUTO-REFRESH (every 45s while tab is active)
   Keeps the grid aligned with live scraper writes.
   ══════════════════════════════════════════════════════════════════ */
(function startLeadsAutoRefresh() {
  const LEADS_REFRESH_MS = 45_000;
  setInterval(() => {
    const tab = document.getElementById('tabLeads');
    if (tab && tab.classList.contains('active') && typeof applyFilters === 'function') {
      applyFilters();
    }
  }, LEADS_REFRESH_MS);
})();
