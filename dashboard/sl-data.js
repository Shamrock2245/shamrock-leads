/* ShamrockLeads — Data Fetch, Render, Command Center */

// ── Lead Explorer Fetch ──
async function applyFilters() {
  SL_STATE.custody = document.getElementById('custodyFilter')?.value || '';
  SL_STATE.status = document.getElementById('statusFilter')?.value || '';
  SL_STATE.limit = parseInt(document.getElementById('limitSelect')?.value || 50);
  const p = new URLSearchParams({ page: SL_STATE.page, limit: SL_STATE.limit, sort: SL_STATE.sort, order: SL_STATE.order });
  if (SL_STATE.selectedCounties.length) p.set('county', SL_STATE.selectedCounties.join(','));
  if (SL_STATE.days) p.set('days', SL_STATE.days);
  if (SL_STATE.custody) p.set('custody', SL_STATE.custody);
  if (SL_STATE.status) p.set('status', SL_STATE.status);
  if (SL_STATE.minBond) p.set('min_bond', SL_STATE.minBond);
  if (SL_STATE.search) p.set('search', SL_STATE.search);
  try {
    const r = await fetch(`${API}/api/leads?${p}`); const d = await r.json();
    SL_STATE.leads = d.leads || []; SL_STATE.total = d.total || 0; SL_STATE.pages = d.pages || 1;
    if (d.counties && SL_STATE.counties.length === 0) buildCountyOptions(d.counties);
    document.getElementById('leadsBadge').textContent = SL_STATE.total.toLocaleString();
    document.getElementById('resultsMeta').textContent = `${SL_STATE.total.toLocaleString()} results · Page ${SL_STATE.page}/${SL_STATE.pages}`;
    renderLeads(); renderPills(); renderPagination();
  } catch(e) { console.error('applyFilters error:', e); }
}

function renderLeads() {
  const tb = document.getElementById('leadsBody');
  if (!SL_STATE.leads.length) { tb.innerHTML = '<tr><td colspan="9" class="loading">No leads match current filters</td></tr>'; return; }
  tb.innerHTML = SL_STATE.leads.map(l => {
    const bond = l.bond_amount || 0;
    const bc = bond >= 10000 ? 'bond-high' : bond >= 2500 ? 'bond-mid' : 'bond-low';
    const sc = (l.lead_status||'').toLowerCase();
    const scoreCls = sc === 'hot' ? 'score-hot' : sc === 'warm' ? 'score-warm' : sc === 'disqualified' ? 'score-disq' : 'score-cold';
    const charges = (l.charges||'').length > 50 ? (l.charges||'').slice(0,47)+'…' : (l.charges||'—');
    const custVal = (l.status||'').trim();
    const custLower = custVal.toLowerCase();
    const custClass = custLower.includes('custody') ? 'custody' : custLower.includes('release') || custLower.includes('bonded') ? 'released' : custLower.includes('not in') ? 'released' : 'other';
    const bkEsc = (l.booking_number||'').replace(/"/g,'&quot;');
    const custDropdown = `<select class="def-status-badge ${custClass}" style="cursor:pointer;border:1px solid var(--border);background:transparent;padding:2px 6px;font-size:11px;border-radius:6px" onchange="updateCustody('${bkEsc}',this.value,this)"><option value="" ${!custVal?'selected':''}>${custVal||'—'}</option><option value="In Custody" ${'In Custody'===custVal?'selected':''}>In Custody</option><option value="Not In Custody" ${'Not In Custody'===custVal?'selected':''}>Not In Custody</option><option value="Released" ${'Released'===custVal?'selected':''}>Released</option><option value="Bonded Out" ${'Bonded Out'===custVal?'selected':''}>Bonded Out</option></select>`;
    const courtCls = isCourtSoon(l.court_date) ? 'court-soon' : '';
    return `<tr>
      <td><strong>${l.full_name||'Unknown'}</strong><br><span style="color:var(--muted);font-size:11px">${[l.sex,l.race,l.dob].filter(Boolean).join(' · ')}</span></td>
      <td><span class="county-count">${l.county||'—'}</span></td>
      <td title="${(l.charges||'').replace(/"/g,'&quot;')}">${charges}</td>
      <td class="${bc}">$${bond.toLocaleString()}</td>
      <td><span class="score-pill ${scoreCls}">${l.lead_score||0} ${l.lead_status||''}</span></td>
      <td>${custDropdown}</td>
      <td>${fmtDate(l.arrest_date || l.booking_date)}</td>
      <td class="${courtCls}">${l.court_date || '—'}</td>
      <td>${l.detail_url ? `<a href="${l.detail_url}" target="_blank" style="color:var(--accent)">🔗</a>` : '—'}</td>
    </tr>`;
  }).join('');
}

// ── Filter Pills ──
function renderPills() {
  const el = document.getElementById('filterPills'); const pills = [];
  if (SL_STATE.selectedCounties.length) pills.push(mkPill(`Counties: ${SL_STATE.selectedCounties.length}`, () => { SL_STATE.selectedCounties = []; buildCountyOptions(SL_STATE.counties); applyFilters(); }));
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
  SL_STATE.selectedCounties=[]; SL_STATE.days=0; SL_STATE.minBond=0; SL_STATE.search=''; SL_STATE.custody=''; SL_STATE.status='';
  document.getElementById('searchInput').value=''; document.getElementById('custodyFilter').value=''; document.getElementById('statusFilter').value='';
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
    const [s, m, cmd] = await Promise.all([
      fetch(`${API}/api/status`).then(r=>r.json()),
      fetch(`${API}/api/mongo-stats`).then(r=>r.json()),
      fetch(`${API}/api/command`).then(r=>r.json()).catch(()=>null)
    ]);
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

    // Command center data
    if (cmd) {
      document.getElementById('kpiBondReady').textContent = cmd.bond_ready_count||0;
      document.getElementById('kpiPipeline').textContent = '$'+fmt(cmd.pipeline_total||0);
      document.getElementById('kpiPipelineSub').textContent = 'est. premium: $'+fmt(cmd.premium_estimate||0);

      // Bond-ready queue table
      const bq = cmd.bond_ready || [];
      document.getElementById('bondQueueMeta').textContent = `${bq.length} defendants · $${(cmd.pipeline_total||0).toLocaleString()} total bond`;
      document.getElementById('bondQueueBody').innerHTML = bq.length ? bq.map(l => {
        const bond = l.bond_amount||0;
        const prem = Math.max(100, bond * 0.1);
        const bc = bond>=10000?'bond-high':bond>=2500?'bond-mid':'bond-low';
        const charges = (l.charges||'').length > 60 ? (l.charges||'').slice(0,57)+'…' : (l.charges||'—');
        return `<tr>
          <td><strong>${l.full_name||'?'}</strong><br><span style="color:var(--muted);font-size:11px">${l.dob||''} · ${l.booking_number||''}</span></td>
          <td>${l.county||'—'}</td>
          <td class="${bc}">$${bond.toLocaleString()}</td>
          <td style="color:var(--success);font-weight:600">$${prem.toLocaleString()}</td>
          <td><span class="score-pill ${(l.lead_status||'').toLowerCase()==='hot'?'score-hot':'score-warm'}">${l.lead_score||0}</span></td>
          <td title="${(l.charges||'').replace(/"/g,'&quot;')}" style="font-size:11px;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${charges}</td>
          <td><button class="btn-write-bond" onclick="openBondModal('${(l.full_name||'').replace(/'/g,"\\'")}',${bond},'${l.county||''}','${l.booking_number||''}')">✍️ Write</button></td>
        </tr>`;
      }).join('') : '<tr><td colspan="7" class="loading">No bond-ready defendants</td></tr>';

      // Custody by county
      const cbc = cmd.custody_by_county || [];
      document.getElementById('custodyCountyList').innerHTML = cbc.map(c =>
        `<div class="county-row"><span class="county-name">${c.county}</span><span style="color:var(--success);font-size:12px;font-weight:600">$${fmt(c.total_bond)}</span><span class="county-count">${c.count} in custody</span></div>`
      ).join('') || '<div class="loading">No data</div>';

      // Recent activity
      const ra = cmd.recent_activity || [];
      document.getElementById('recentActivity').innerHTML = ra.map(l => {
        const sc2 = (l.lead_status||'').toLowerCase();
        const dot = sc2==='hot'?'🔥':sc2==='warm'?'🟡':'⚪';
        return `<div class="county-row"><span class="county-name" style="font-size:12px">${dot} ${l.full_name||'?'}</span><span style="color:var(--accent);font-size:12px;font-weight:700">$${(l.bond_amount||0).toLocaleString()}</span><span class="county-count">${l.county||'—'} · ${timeAgo(l.scraped_at)}</span></div>`;
      }).join('') || '<div class="loading">No recent activity</div>';
    }

    // Hot lead audio alert
    if (SL_STATE.prevHotCount >= 0 && (scores.hot||0) > SL_STATE.prevHotCount) playHotAlert();
    SL_STATE.prevHotCount = scores.hot||0;

    if (SL_STATE.counties.length === 0 && m.by_county) buildCountyOptions(Object.keys(m.by_county).sort());
  } catch(e) { console.error('Dashboard load error:', e); }
}
