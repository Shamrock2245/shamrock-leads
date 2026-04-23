/* ═══════════════════════════════════════════════════════
   ShamrockLeads — Analytics Dashboard Logic
   ═══════════════════════════════════════════════════════ */

// ── Shared Utilities ──
const $=id=>document.getElementById(id);
const money=n=>n?'$'+Number(n).toLocaleString('en-US',{minimumFractionDigits:0}):'$0';
const bondClass=n=>n>=10000?'bond-high':n>=2500?'bond-mid':'bond-low';
const bondPill=n=>n>=10000?'high':n>=2500?'mid':'low';
const truncate=(s,n)=>s&&s.length>n?s.substring(0,n)+'…':(s||'—');
const val=v=>(v!=null&&v!=='')?v:'—';
const fetchJSON=async u=>{try{return(await fetch(u)).json()}catch(e){console.error(e);return null}};

let currentPage=1,currentSort='created_at',currentDir=-1;
let countyChart,bondChart,searchTimeout;
let bountyPage=1,bountySortCol='bond_amount',bountySortDir=-1;

// ── Theme ──
function toggleTheme(){
  const html=document.documentElement;
  const next=html.getAttribute('data-theme')==='dark'?'light':'dark';
  html.setAttribute('data-theme',next);
  localStorage.setItem('shamrock-theme',next);
  loadCounties();loadBondChart();
}
(function(){const s=localStorage.getItem('shamrock-theme');if(s)document.documentElement.setAttribute('data-theme',s)})();

function getChartColors(){
  const s=getComputedStyle(document.documentElement);
  return{grid:s.getPropertyValue('--chart-grid').trim(),muted:s.getPropertyValue('--muted').trim()};
}

// ── Tabs ──
function switchTab(tab){
  document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
  document.querySelectorAll('.tab-content').forEach(p=>p.classList.remove('active'));
  if(tab==='analytics'){
    $('tabAnalytics').classList.add('active');$('panelAnalytics').classList.add('active');
  }else if(tab==='health'){
    $('tabHealth').classList.add('active');$('panelHealth').classList.add('active');
    if(!window._healthLoaded){loadHealthTab();window._healthLoaded=true}
  }else{
    $('tabDefendants').classList.add('active');$('panelDefendants').classList.add('active');
    if(!window._defLoaded){loadDefendants();window._defLoaded=true}
  }
}
function refreshCurrentTab(){
  $('lastUpdate').textContent='Updated: '+new Date().toLocaleTimeString();
  if($('panelAnalytics').classList.contains('active'))loadDashboard();
  else if($('panelHealth').classList.contains('active')){window._healthLoaded=false;loadHealthTab();}
  else loadDefendants();
}

// ── Stats ──
async function loadStats(){
  const d=await fetchJSON('/api/stats');if(!d)return;
  $('statTotal').textContent=d.total_arrests.toLocaleString();
  $('statCounties').textContent=d.counties_active;
  $('statToday').textContent=d.today_new.toLocaleString();
  $('statAvgBond').textContent=money(d.avg_bond);
  $('statHighValue').textContent=d.high_value_leads.toLocaleString();
  $('defBadge').textContent=d.total_arrests.toLocaleString();
}

// ── Counties ──
async function loadCounties(){
  const data=await fetchJSON('/api/counties');if(!data)return;
  const colors=['#10b981','#3b82f6','#f59e0b','#8b5cf6','#ef4444','#06b6d4','#ec4899'];
  const c=getChartColors();
  const ctx=$('countyChart').getContext('2d');
  if(countyChart)countyChart.destroy();
  countyChart=new Chart(ctx,{type:'doughnut',data:{labels:data.map(d=>d.county),datasets:[{data:data.map(d=>d.total),backgroundColor:colors,borderWidth:0,hoverOffset:6}]},options:{responsive:true,cutout:'65%',plugins:{legend:{position:'bottom',labels:{color:c.muted,padding:12,font:{size:11,family:'Inter'}}}}}});

  $('countyList').innerHTML=data.map(d=>`
    <div class="county-row">
      <div><div class="county-name">${d.county}</div><div style="color:var(--muted);font-size:11px">${d.in_custody} in custody · Avg ${money(d.avg_bond)}</div></div>
      <div style="text-align:right"><div class="county-count">${d.total}</div><div class="county-bond">${money(d.total_bond)} total</div></div>
    </div>`).join('');

  // Populate filter dropdowns
  const populate=(sel,cur)=>{sel.innerHTML='<option value="">All Counties</option>'+data.map(d=>`<option value="${d.county}">${d.county} (${d.total})</option>`).join('');sel.value=cur};
  populate($('filterCounty'),$('filterCounty').value);
  populate($('defFilterCounty'),$('defFilterCounty').value);
  populate($('bountyCounty'),$('bountyCounty').value);
}

// ── Bond Chart ──
async function loadBondChart(){
  const data=await fetchJSON('/api/bond-distribution');if(!data)return;
  const c=getChartColors();const ctx=$('bondChart').getContext('2d');
  if(bondChart)bondChart.destroy();
  bondChart=new Chart(ctx,{type:'bar',data:{labels:data.labels,datasets:[{data:data.counts,backgroundColor:'rgba(16,185,129,0.5)',borderColor:'#10b981',borderWidth:1,borderRadius:5,hoverBackgroundColor:'rgba(16,185,129,0.7)'}]},options:{responsive:true,plugins:{legend:{display:false}},scales:{y:{grid:{color:c.grid},ticks:{color:c.muted,font:{size:10}}},x:{grid:{display:false},ticks:{color:c.muted,font:{size:9}}}}}});
}

// ── Charges ──
async function loadCharges(){
  const data=await fetchJSON('/api/top-charges');if(!data)return;
  $('chargesList').innerHTML=data.map(d=>`<li><span style="flex:1">${truncate(d.charge,40)}</span><span style="font-weight:700;color:var(--accent)">${d.count}</span></li>`).join('');
}

// ── Bounty Board ──
function sortBounty(col){
  const sel=$('bountySort');
  if(bountySortCol===col){bountySortDir*=-1}
  else{bountySortCol=col;bountySortDir=col==='county'||col==='full_name'?1:-1}
  if(col==='bond_amount')sel.value='bond_amount';
  else if(col==='booking_date')sel.value='booking_date';
  else if(col==='county')sel.value='county';
  bountyPage=1;loadBountyBoard();
}
async function loadBountyBoard(){
  const selVal=$('bountySort').value;
  if(selVal&&selVal!==bountySortCol){bountySortCol=selVal;bountySortDir=selVal==='county'?1:-1;bountyPage=1;}
  const county=$('bountyCounty').value;
  let url=`/api/bounty-board?sort=${bountySortCol}&dir=${bountySortDir}&page=${bountyPage}&limit=50`;
  if(county)url+=`&county=${encodeURIComponent(county)}`;
  const data=await fetchJSON(url);if(!data)return;
  const rows=data.targets||data;const total=data.total||rows.length;
  $('bountyCount').textContent=`${total.toLocaleString()} targets`;
  $('bountyBody').innerHTML=rows.length?rows.map(d=>`<tr><td style="font-weight:600">${val(d.full_name)}</td><td>${val(d.county)}</td><td class="${bondClass(d.bond_amount)}">${money(d.bond_amount)}</td><td title="${d.charges||''}">${truncate(d.charges,50)}</td><td>${val(d.booking_date)}</td><td>${val(d.status)}</td></tr>`).join(''):'<tr><td colspan="6" class="loading">No high-value targets</td></tr>';
  if(data.pages&&data.pages>1){
    $('bountyPagination').innerHTML=`<button ${data.page<=1?'disabled':''} onclick="bountyPage=${data.page-1};loadBountyBoard()">← Prev</button><span>Page ${data.page} of ${data.pages}</span><button ${data.page>=data.pages?'disabled':''} onclick="bountyPage=${data.page+1};loadBountyBoard()">Next →</button>`;
  }else{$('bountyPagination').innerHTML='';}
}

// ── Arrests Table ──
async function loadArrests(page){
  if(page)currentPage=page;
  let url=`/api/arrests?page=${currentPage}&sort=${currentSort}&dir=${currentDir}`;
  const county=$('filterCounty').value,search=$('filterSearch').value,minBond=$('filterMinBond').value;
  if(county)url+=`&county=${encodeURIComponent(county)}`;
  if(search)url+=`&search=${encodeURIComponent(search)}`;
  if(minBond)url+=`&min_bond=${minBond}`;
  const data=await fetchJSON(url);if(!data)return;
  $('resultCount').textContent=`${data.total.toLocaleString()} results`;
  $('arrestBody').innerHTML=data.arrests.length?data.arrests.map(d=>`<tr><td style="font-weight:500">${val(d.full_name)}</td><td>${val(d.county)}</td><td class="${bondClass(d.bond_amount)}">${money(d.bond_amount)}</td><td title="${d.charges||''}">${truncate(d.charges,45)}</td><td>${val(d.booking_date)}</td><td>${val(d.arrest_date)}</td><td>${val(d.status)}</td><td style="color:var(--muted)">${val(d.facility)}</td></tr>`).join(''):'<tr><td colspan="8" class="loading">No results</td></tr>';
  $('pagination').innerHTML=`<button ${data.page<=1?'disabled':''} onclick="loadArrests(${data.page-1})">← Prev</button><span>Page ${data.page} of ${data.pages}</span><button ${data.page>=data.pages?'disabled':''} onclick="loadArrests(${data.page+1})">Next →</button>`;
}

function sortTable(col){if(currentSort===col)currentDir*=-1;else{currentSort=col;currentDir=-1}loadArrests(1)}
function debounceSearch(){clearTimeout(searchTimeout);searchTimeout=setTimeout(()=>loadArrests(1),300)}

// ── Boot ──
async function loadDashboard(){
  $('lastUpdate').textContent='Updated: '+new Date().toLocaleTimeString();
  await Promise.all([loadStats(),loadCounties(),loadBondChart(),loadCharges(),loadBountyBoard(),loadArrests(1)]);
}
loadDashboard();
setInterval(()=>{if($('panelAnalytics').classList.contains('active'))loadDashboard()},120000);

// ═══════════════════════════════════════════════════════
//  Scraper Health Tab
// ═══════════════════════════════════════════════════════
let healthData=[], healthSortCol='total_records', healthSortDir=-1;
let drillCounty='', drillPage=1, drillSort='created_at', drillDir=-1, drillTimeout;

async function loadHealthTab(){
  healthData = await fetchJSON('/api/scraper-health');
  if(!healthData) return;
  
  const healthy = healthData.filter(d=>d.status==='healthy').length;
  const stale = healthData.filter(d=>['stale','warning','offline'].includes(d.status)).length;
  const total24h = healthData.reduce((s,d)=>s+d.records_24h, 0);
  
  $('healthCounties').textContent = healthData.length;
  $('healthHealthy').textContent = healthy;
  $('health24h').textContent = total24h.toLocaleString();
  $('healthStale').textContent = stale;
  $('healthBadge').textContent = healthData.length;
  
  renderHealthTable();
}

function renderHealthTable(){
  const d = [...healthData];
  d.sort((a,b)=>{
    let av=a[healthSortCol], bv=b[healthSortCol];
    if(typeof av==='string') return healthSortDir * av.localeCompare(bv);
    return healthSortDir * ((av||0) - (bv||0));
  });
  
  const statusBadge = s => {
    const map = {healthy:'🟢', stale:'🟡', warning:'🟠', offline:'🔴'};
    return `<span class="status-badge status-${s}">${map[s]||'⚪'} ${s}</span>`;
  };
  const timeAgo = h => {
    if(h>=999) return '—';
    if(h<1) return `${Math.round(h*60)}m ago`;
    if(h<24) return `${Math.round(h)}h ago`;
    return `${Math.round(h/24)}d ago`;
  };
  
  $('healthBody').innerHTML = d.length ? d.map(r => `
    <tr class="health-row" onclick="openDrill('${r.county}')" style="cursor:pointer">
      <td>${statusBadge(r.status)}</td>
      <td style="font-weight:600">${r.county}</td>
      <td>${r.total_records.toLocaleString()}</td>
      <td style="color:${r.records_24h>0?'#10b981':'var(--muted)'}">${r.records_24h}</td>
      <td>${r.in_custody.toLocaleString()}</td>
      <td class="${bondClass(r.avg_bond)}">${money(r.avg_bond)}</td>
      <td>${money(r.total_bond)}</td>
      <td style="color:${r.hours_since_update<2?'#10b981':r.hours_since_update<6?'#f59e0b':'#ef4444'}">${timeAgo(r.hours_since_update)}</td>
    </tr>
  `).join('') : '<tr><td colspan="8" class="loading">No scraper data</td></tr>';
}

function sortHealth(col){
  if(healthSortCol===col) healthSortDir*=-1;
  else { healthSortCol=col; healthSortDir=-1; }
  renderHealthTable();
}

// ── County Drill-Down ──
async function openDrill(county){
  drillCounty=county; drillPage=1;
  $('countyDrillPanel').style.display='block';
  $('drillTitle').textContent=`📋 ${county} County — Recent Arrests (newest first)`;
  $('drillSearch').value='';
  loadDrillData();
  $('countyDrillPanel').scrollIntoView({behavior:'smooth'});
}
function closeDrill(){$('countyDrillPanel').style.display='none';}
function sortDrill(col){
  if(drillSort===col) drillDir*=-1;
  else { drillSort=col; drillDir=-1; }
  drillPage=1; loadDrillData();
}
function debounceDrillSearch(){clearTimeout(drillTimeout);drillTimeout=setTimeout(()=>{drillPage=1;loadDrillData();},300);}

async function loadDrillData(){
  let url=`/api/county-arrests/${encodeURIComponent(drillCounty)}?page=${drillPage}&sort=${drillSort}&dir=${drillDir}&limit=25`;
  const s=$('drillSearch').value;
  if(s) url+=`&search=${encodeURIComponent(s)}`;
  const data = await fetchJSON(url);
  if(!data) return;
  $('drillCount').textContent=`${data.total} results`;
  $('drillBody').innerHTML = data.arrests.length ? data.arrests.map(d=>`
    <tr>
      <td style="font-weight:500">${val(d.full_name)}</td>
      <td class="${bondClass(d.bond_amount)}">${money(d.bond_amount)}</td>
      <td title="${d.charges||''}">${truncate(d.charges,40)}</td>
      <td>${val(d.booking_date)}</td>
      <td style="color:var(--muted);font-size:11px">${d.created_at?new Date(d.created_at).toLocaleString():'—'}</td>
      <td>${val(d.status)}</td>
      <td style="color:var(--muted)">${val(d.facility)}</td>
    </tr>
  `).join('') : '<tr><td colspan="7" class="loading">No records</td></tr>';
  $('drillPagination').innerHTML=`<button ${data.page<=1?'disabled':''} onclick="drillPage=${data.page-1};loadDrillData()">← Prev</button><span>Page ${data.page} of ${data.pages}</span><button ${data.page>=data.pages?'disabled':''} onclick="drillPage=${data.page+1};loadDrillData()">Next →</button>`;
}
