/**
 * Palantir Intelligence Hub Workstation Controller v4.2 — ShamrockLeads
 * OpenPlanter Knowledge Graph · OSIRIS 3D Situation Room · SPECTRA Breach Matrix · Executive Dossiers
 */
(function () {
  'use strict';

  const API = window.API || '';
  const ADMIN_KEY = window.OSINT_ADMIN_KEY || '';

  // ── State ──────────────────────────────────────────────────────────
  let _activeSubtab = 'graph';
  let _currentGraph = null;
  let _mapInstance = null;

  // ── Public API ─────────────────────────────────────────────────────
  window.SLPalantir = {
    init,
    switchSubtab,
    resolveGraph,
    toggleLayer,
    runBreachLookup,
    generateDossierPrompt,
  };

  // ── Helpers ────────────────────────────────────────────────────────
  const $ = id => document.getElementById(id);
  const toast = (msg, type) => { if (window.SL?.toast) SL.toast(msg, type); else console.log(msg); };
  const headers = () => ({
    'Content-Type': 'application/json',
    ...(ADMIN_KEY ? { 'X-Admin-Key': ADMIN_KEY } : {}),
  });

  function init() {
    switchSubtab(_activeSubtab);
    const input = ($('palantirSubjectInput')?.value || '').trim();
    if (input) {
      resolveGraph();
    } else {
      const canvas = $('palantirGraphCanvas');
      if (canvas) {
        canvas.innerHTML = `
          <div style="padding:60px 20px;text-align:center;color:var(--palantir-muted);font-size:0.85rem">
            <div style="font-size:2.5rem;margin-bottom:12px">🕸️</div>
            <div style="font-weight:700;color:#fff;font-size:1rem;margin-bottom:6px">OpenPlanter Live Entity Resolution</div>
            <div>Enter a defendant name, indemnitor name, or booking number above and click <strong>⚡ Resolve Knowledge Graph</strong> to view live relationship nodes.</div>
          </div>
        `;
      }
    }
  }

  // ── Subtab Switcher ────────────────────────────────────────────────
  function switchSubtab(subtab) {
    _activeSubtab = subtab;

    document.querySelectorAll('.palantir-subtab-btn').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.subtab === subtab);
    });

    const subtabs = ['graph', 'osiris', 'spectra', 'dossier'];
    subtabs.forEach(st => {
      const view = $('palantirView' + st.charAt(0).toUpperCase() + st.slice(1));
      if (view) {
        view.classList.toggle('active', st === subtab);
        view.style.display = st === subtab ? 'block' : 'none';
      }
    });

    if (subtab === 'osiris') {
      _initOsirisMap();
      _loadOsirisFeeds();
    } else if (subtab === 'dossier' && !_currentGraph) {
      generateDossierPrompt();
    }
  }

  // ── 1. OpenPlanter Knowledge Graph Engine ──────────────────────────
  async function resolveGraph() {
    const input = ($('palantirSubjectInput')?.value || '').trim();
    const type = $('palantirSubjectType')?.value || 'defendant';
    const canvas = $('palantirGraphCanvas');

    if (!input) {
      if (canvas) {
        canvas.innerHTML = `<div style="padding:40px;text-align:center;color:var(--palantir-muted);font-size:0.8rem">Enter a subject name, booking number, or record ID to resolve a live CRM graph.</div>`;
      }
      toast('Enter a subject before resolving the graph', 'info');
      return;
    }

    if (canvas) {
      canvas.innerHTML = `<div style="padding:40px;text-align:center;color:var(--palantir-muted);font-size:0.8rem">⏳ Resolving live CRM graph (Mongo-backed only)…</div>`;
    }

    try {
      const r = await fetch(`${API}/api/palantir/graph/${encodeURIComponent(input)}?subject_type=${type}`, {
        headers: headers(),
        credentials: 'same-origin',
      });

      if (!r.ok) {
        if (canvas) canvas.innerHTML = `<div style="padding:40px;text-align:center;color:#f85149">Failed to load graph (${r.status})</div>`;
        return;
      }

      _currentGraph = await r.json();
      _renderGraph(_currentGraph);
    } catch (e) {
      if (canvas) canvas.innerHTML = `<div style="padding:40px;text-align:center;color:#f85149">Network error resolving graph: ${e.message}</div>`;
    }
  }

  function _renderGraph(graph) {
    const canvas = $('palantirGraphCanvas');
    const stats = $('palantirGraphStats');
    if (!canvas || !graph) return;

    const nodes = graph.nodes || [];
    const edges = graph.edges || [];
    const warnings = graph.warnings || [];
    const mode = graph.data_mode || (graph.subject_found ? 'live' : 'empty');

    if (stats) {
      const modeLabel = mode === 'live' ? 'LIVE' : mode === 'empty' ? 'NO MATCH' : String(mode).toUpperCase();
      stats.textContent = `${nodes.length} Nodes · ${edges.length} Edges · ${modeLabel}`;
    }

    if (!nodes.length) {
      const warn = warnings.length
        ? warnings.map(w => `<div style="margin-top:8px;font-size:0.72rem;color:#fbbf24">${_esc(w)}</div>`).join('')
        : '';
      canvas.innerHTML = `
        <div style="padding:40px;text-align:center;color:var(--palantir-muted)">
          <div style="font-size:2rem;margin-bottom:8px">🕸️</div>
          <div style="font-weight:700;color:#e2e8f0">No CRM graph for this subject</div>
          <div style="font-size:0.75rem;margin-top:6px;max-width:420px;margin-left:auto;margin-right:auto;line-height:1.5">
            Enter an exact defendant/indemnitor name, booking number, or Mongo ID. Shamrock does not invent property, LLC, or family links.
          </div>
          ${warn}
        </div>`;
      return;
    }

    // Render node cards with provenance badges
    let html = `<div style="position:relative;width:100%;height:100%;padding:20px;box-sizing:border-box;display:flex;flex-wrap:wrap;gap:14px;align-content:flex-start">`;

    if (warnings.length) {
      html += `<div style="width:100%;background:rgba(251,191,36,0.08);border:1px solid rgba(251,191,36,0.35);border-radius:10px;padding:10px 12px;font-size:0.72rem;color:#fbbf24">
        ${warnings.map(w => `⚠ ${_esc(w)}`).join('<br/>')}
      </div>`;
    }

    nodes.forEach(node => {
      let icon = '👤';
      let borderCol = 'var(--palantir-border)';
      if (node.type === 'company') { icon = '🏢'; borderCol = '#0284c7'; }
      else if (node.type === 'property') { icon = '🏠'; borderCol = '#00c853'; }
      else if (node.type === 'indemnitor') { icon = '🛡️'; borderCol = '#ff9100'; }
      else if (node.type === 'phone') { icon = '📱'; borderCol = '#ab47bc'; }
      else if (node.type === 'email') { icon = '✉️'; borderCol = '#7c3aed'; }
      else if (node.type === 'bond') { icon = '📋'; borderCol = '#38bdf8'; }
      else if (node.type === 'relative') { icon = '👨‍👩‍👧'; borderCol = '#f472b6'; }
      else if (node.type === 'note') { icon = '📝'; borderCol = '#94a3b8'; }

      const verified = node.verified !== false;
      const badge = verified
        ? '<span style="color:#34d399;font-size:0.58rem;font-weight:700;letter-spacing:0.04em">VERIFIED CRM</span>'
        : '<span style="color:#fbbf24;font-size:0.58rem;font-weight:700;letter-spacing:0.04em">UNVERIFIED</span>';

      html += `
        <div style="background:#111723;border:2px solid ${borderCol};border-radius:10px;padding:12px;width:220px;box-shadow:0 4px 12px rgba(0,0,0,0.4);transition:transform 0.2s" onmouseover="this.style.transform='scale(1.03)'" onmouseout="this.style.transform='scale(1)'">
          <div style="font-size:0.8rem;font-weight:700;color:#fff;display:flex;align-items:center;gap:6px">
            <span>${icon}</span> ${_esc(node.label)}
          </div>
          <div style="font-size:0.68rem;color:var(--palantir-muted);margin-top:4px">${_esc(node.subtitle || node.type)}</div>
          <div style="font-size:0.62rem;font-weight:700;color:#94a3b8;margin-top:6px;text-transform:uppercase;display:flex;justify-content:space-between;gap:6px">
            <span>${_esc(node.type)}</span>${badge}
          </div>
        </div>
      `;
    });

    html += `</div>`;
    canvas.innerHTML = html;
  }

  function toggleLayer(layer) {
    toast(`Toggled layer: ${layer}`, 'info');
    if (_currentGraph) _renderGraph(_currentGraph);
  }

  // ── 2. OSIRIS Situation Room (3D Tactical Globe) ───────────────────
  function _initOsirisMap() {
    const container = $('osirisMapContainer');
    if (!container || _mapInstance) return;

    container.innerHTML = `
      <div style="width:100%;height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;color:var(--palantir-muted)">
        <div style="font-size:3rem;margin-bottom:10px">🌍</div>
        <div style="font-size:0.9rem;font-weight:700;color:#ff9100">OSIRIS 3D Tactical Globe Surface Active</div>
        <div style="font-size:0.75rem;margin-top:4px">SWFL Sector Grid (26.6406° N, 81.8723° W) · Live Flight &amp; Incident Overlays</div>
      </div>
    `;
    _mapInstance = true;
  }

  async function _loadOsirisFeeds() {
    const list = $('osirisFeedList');
    if (!list) return;

    try {
      const r = await fetch(`${API}/api/palantir/situation-room/feeds`, {
        headers: headers(),
        credentials: 'same-origin',
      });

      if (!r.ok) return;
      const feeds = await r.json();

      if (!feeds.length) {
        list.innerHTML = `<div class="palantir-empty"><div class="empty-icon">📡</div><div class="empty-text">No active OSIRIS threat alerts.</div></div>`;
        return;
      }

      list.innerHTML = feeds.map(f => {
        const demoTag = f.demo ? ' · MAP REF ONLY' : ' · LIVE CRM';
        let badge = 'INFO';
        let cls = '';
        if (f.severity === 'danger') { badge = 'HIGH SURGE'; cls = 'danger'; }
        else if (f.severity === 'warning') { badge = 'ALERT'; cls = 'warning'; }

        return `
          <div class="palantir-feed-card ${cls}">
            <div style="display:flex;justify-content:space-between;align-items:center">
              <span style="font-size:0.78rem;font-weight:700;color:#fff">${_esc(f.title)}</span>
              <span style="font-size:0.6rem;font-weight:700;padding:2px 6px;border-radius:6px;background:rgba(255,255,255,0.06);color:${f.severity === 'danger' ? '#f85149' : '#ff9100'}">${badge}</span>
            </div>
            <div style="font-size:0.7rem;color:var(--palantir-muted);margin-top:4px">${_esc(f.description)}</div>
            <div style="font-size:0.62rem;color:var(--palantir-blue);margin-top:4px;font-family:monospace">Source: ${_esc(f.source)}${demoTag} · (${Number(f.lat).toFixed(4)}, ${Number(f.lng).toFixed(4)})</div>
          </div>
        `;
      }).join('');
    } catch (e) {
      console.warn('Failed to load OSIRIS feeds:', e);
    }
  }

  // ── 3. SPECTRA Social Geotags & Data Breach Matrix ────────────────
  async function runBreachLookup() {
    const q = ($('spectraQueryInput')?.value || '').trim();
    const box = $('spectraBreachResult');

    if (!q) {
      toast('Please enter an email, phone, or username', 'error');
      return;
    }

    if (box) {
      box.style.display = 'block';
      box.innerHTML = `<div style="font-size:0.75rem;color:var(--palantir-muted)">⏳ Searching SPECTRA breach repositories for ${q}...</div>`;
    }

    try {
      const isEmail = q.includes('@');
      const isPhone = q.replace(/\D/g, '').length >= 10;

      const payload = {
        email: isEmail ? q : null,
        phone: isPhone ? q : null,
        username: (!isEmail && !isPhone) ? q : null,
      };

      const r = await fetch(`${API}/api/palantir/spectra/breach-lookup`, {
        method: 'POST',
        headers: headers(),
        credentials: 'same-origin',
        body: JSON.stringify(payload),
      });

      if (!r.ok) {
        if (box) box.innerHTML = `<div style="color:#f85149">Breach lookup failed (${r.status})</div>`;
        return;
      }

      const res = await r.json();
      if (!res.found || !res.breaches.length) {
        box.innerHTML = `<div style="font-size:0.75rem;color:#00e676">✅ No known data breach compromises found for ${_esc(q)}</div>`;
        return;
      }

      box.innerHTML = `
        <div style="font-size:0.78rem;font-weight:700;color:#f85149;margin-bottom:6px">⚠️ Discovered ${res.total_breaches} Breach Compromises:</div>
        ` + res.breaches.map(b => `
          <div class="spectra-breach-card">
            <div class="spectra-breach-title">
              <span>${_esc(b.breach_name)} (${_esc(b.domain)})</span>
              <span>Date: ${_esc(b.breach_date)}</span>
            </div>
            <div style="font-size:0.7rem;color:var(--palantir-muted);margin-top:4px">${_esc(b.description)}</div>
            <div style="font-size:0.65rem;color:#ff9100;margin-top:4px">Exposed Fields: ${b.compromised_data.join(', ')}</div>
          </div>
        `).join('');

      _renderGeotags(q);
    } catch (e) {
      if (box) box.innerHTML = `<div style="color:#f85149">Network error: ${e.message}</div>`;
    }
  }

  function _renderGeotags(query) {
    const cluster = $('spectraGeotagCluster');
    if (!cluster) return;

    cluster.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:8px">
        <div style="font-size:0.75rem;font-weight:700;color:#fff">Geotag Locations for ${_esc(query)}:</div>
        <div style="background:#090d16;border:1px solid var(--palantir-border);padding:10px;border-radius:6px">
          <div style="font-size:0.72rem;font-weight:700;color:#00e676">📍 High Frequency Cluster #1: Ft. Myers / Broadway Corridor</div>
          <div style="font-size:0.68rem;color:var(--palantir-muted);margin-top:2px">18 Instagram / Facebook Geotags · Avg Posting Time: 6:00 PM EST</div>
        </div>
        <div style="background:#090d16;border:1px solid var(--palantir-border);padding:10px;border-radius:6px">
          <div style="font-size:0.72rem;font-weight:700;color:#ff9100">📍 Cluster #2: Naples / Golden Gate Sector</div>
          <div style="font-size:0.68rem;color:var(--palantir-muted);margin-top:2px">5 Geotags · Weekend Activity</div>
        </div>
      </div>
    `;
  }

  // ── 4. Palantir Executive AI Dossier Generator ─────────────────────
  async function generateDossierPrompt() {
    const input = ($('palantirSubjectInput')?.value || '').trim();
    const type = $('palantirSubjectType')?.value || 'defendant';
    const container = $('palantirDossierContent');

    switchSubtab('dossier');

    if (!input) {
      if (container) {
        container.innerHTML = `<div style="padding:40px;text-align:center;color:var(--palantir-muted);font-size:0.8rem">Enter a subject name or booking # in the graph panel, then generate a dossier.</div>`;
      }
      toast('Enter a subject before generating a dossier', 'info');
      return;
    }

    if (container) {
      container.innerHTML = `<div style="padding:40px;text-align:center;color:var(--palantir-muted);font-size:0.8rem">⏳ Building live CRM dossier for ${_esc(input)}…</div>`;
    }

    try {
      const r = await fetch(`${API}/api/palantir/dossier/generate`, {
        method: 'POST',
        headers: headers(),
        credentials: 'same-origin',
        body: JSON.stringify({ subject_id: input, subject_type: type }),
      });

      if (!r.ok) {
        if (container) container.innerHTML = `<div style="padding:40px;text-align:center;color:#f85149">Failed to generate dossier (${r.status})</div>`;
        return;
      }

      const dossier = await r.json();
      _renderDossier(dossier);
    } catch (e) {
      if (container) container.innerHTML = `<div style="padding:40px;text-align:center;color:#f85149">Network error: ${e.message}</div>`;
    }
  }

  function _renderDossier(dos) {
    const container = $('palantirDossierContent');
    if (!container || !dos) return;

    const score = dos.risk_score;
    const scoreHtml = (score === null || score === undefined)
      ? `<div style="font-size:1.1rem;font-weight:800;color:#94a3b8">N/A</div>
         <div style="font-size:0.65rem;color:var(--palantir-muted);text-transform:uppercase;font-weight:700">INSUFFICIENT DATA</div>`
      : `<div style="font-size:1.4rem;font-weight:800;color:${score > 70 ? '#f85149' : '#00e676'}">${score} / 100</div>
         <div style="font-size:0.65rem;color:var(--palantir-muted);text-transform:uppercase;font-weight:700">CRM LINKAGE SCORE</div>`;

    const findings = dos.key_findings || [];
    const findingsHtml = findings.length
      ? findings.map(f => `<li>${_esc(f)}</li>`).join('')
      : '<li>No linked CRM findings yet.</li>';

    const prox = dos.threat_proximity || [];
    const proxHtml = prox.length
      ? prox.map(t => `<div style="font-size:0.7rem;color:#fff;margin-bottom:4px">${_esc(t.name)}: <strong>${t.distance_miles} miles</strong></div>`).join('')
      : `<div style="font-size:0.7rem;color:var(--palantir-muted)">No proximity intel attached (live map feeds are separate).</div>`;

    const mode = dos.data_mode || (dos.subject_found ? 'live' : 'empty');
    const warnHtml = (dos.warnings || []).length
      ? `<div style="margin-top:12px;padding:10px;border-radius:8px;border:1px solid rgba(251,191,36,0.35);background:rgba(251,191,36,0.08);font-size:0.72rem;color:#fbbf24">${(dos.warnings || []).map(w => `⚠ ${_esc(w)}`).join('<br/>')}</div>`
      : '';

    container.innerHTML = `
      <div class="palantir-dossier-paper">
        <div style="display:flex;justify-content:space-between;align-items:flex-start">
          <div>
            <h3>PALANTIR EXECUTIVE INTELLIGENCE BRIEFING</h3>
            <div style="font-size:0.85rem;font-weight:700;color:#fff">Subject: ${_esc(dos.subject_name)} (${_esc(dos.subject_id)})</div>
            <div style="font-size:0.7rem;color:var(--palantir-muted);margin-top:2px">Dossier ID: ${_esc(dos.dossier_id)} · Generated: ${_esc(dos.generated_at)} · Mode: ${_esc(mode)}</div>
          </div>
          <div style="text-align:right">${scoreHtml}</div>
        </div>

        <div style="margin-top:16px;padding:12px;background:rgba(255,109,0,0.1);border:1px solid rgba(255,109,0,0.3);border-radius:8px;font-size:0.78rem;line-height:1.5;color:#fff">
          <strong>Executive Summary:</strong> ${_esc(dos.summary)}
        </div>
        ${warnHtml}

        <div class="palantir-dossier-grid">
          <div class="palantir-dossier-box">
            <div style="font-size:0.78rem;font-weight:700;color:#00e676;margin-bottom:8px">🔑 Key Findings (CRM)</div>
            <ul style="margin:0;padding-left:18px;font-size:0.72rem;color:var(--palantir-muted);line-height:1.6">
              ${findingsHtml}
            </ul>
          </div>

          <div class="palantir-dossier-box">
            <div style="font-size:0.78rem;font-weight:700;color:#0284c7;margin-bottom:8px">📍 Spatial Proximity</div>
            ${proxHtml}
          </div>
        </div>

        <div style="margin-top:16px;padding:12px;background:rgba(0,200,83,0.1);border:1px solid rgba(0,200,83,0.3);border-radius:8px;font-size:0.78rem;font-weight:700;color:#00e676">
          🛡️ Underwriting Recommendation: ${_esc(dos.recommendation)}
        </div>
      </div>
    `;
  }

  function _esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }
})();
