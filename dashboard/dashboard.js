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

let currentPage=1,currentSort='booking_date',currentDir=-1;
let countyChart,bondChart,searchTimeout;

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
  }else{
    $('tabDefendants').classList.add('active');$('panelDefendants').classList.add('active');
    if(!window._defLoaded){loadDefendants();window._defLoaded=true}
  }
}
function refreshCurrentTab(){
  $('lastUpdate').textContent='Updated: '+new Date().toLocaleTimeString();
  if($('panelAnalytics').classList.contains('active'))loadDashboard();
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
async function loadBountyBoard(){
  const data=await fetchJSON('/api/bounty-board');if(!data)return;
  $('bountyBody').innerHTML=data.length?data.map(d=>`<tr><td style="font-weight:600">${val(d.full_name)}</td><td>${val(d.county)}</td><td class="${bondClass(d.bond_amount)}">${money(d.bond_amount)}</td><td title="${d.charges||''}">${truncate(d.charges,50)}</td><td>${val(d.booking_date)}</td><td>${val(d.status)}</td></tr>`).join(''):'<tr><td colspan="6" class="loading">No high-value targets</td></tr>';
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
