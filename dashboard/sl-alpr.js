/* ═══════════════════════════════════════════════════════════════════
   ShamrockLeads — ALPR / LPR Watch desk
   FL511 plate hits · watchlist · ad-hoc image scan
   ═══════════════════════════════════════════════════════════════════ */
/* global SL */
(function () {
  'use strict';

  const API = window.API || '';

  let _status = null;
  let _hits = [];
  let _watch = [];
  let _poll = null;
  let _inited = false;

  window.SLALPR = {
    init,
    load,
    refreshStatus,
    searchHits,
    loadWatchlist,
    addWatch,
    scanImage,
    stopPoll,
  };

  const $ = (id) => document.getElementById(id);
  const toast = (msg, type) => {
    if (window.SL?.toast) SL.toast(msg, type);
    else console.log(msg);
  };
  const fmt = (ts) => {
    if (!ts) return '—';
    try {
      return new Date(ts).toLocaleString();
    } catch {
      return String(ts);
    }
  };
  const esc = (s) =>
    String(s ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');

  function headers(json) {
    const h = {};
    if (json) h['Content-Type'] = 'application/json';
    return h;
  }

  async function init() {
    if (!_inited) {
      _bind();
      _inited = true;
    }
    await load();
    stopPoll();
    _poll = setInterval(() => {
      refreshStatus().catch(() => {});
    }, 45000);
  }

  function stopPoll() {
    if (_poll) {
      clearInterval(_poll);
      _poll = null;
    }
  }

  function _bind() {
    const searchBtn = $('alprSearchBtn');
    if (searchBtn) searchBtn.addEventListener('click', () => searchHits());

    const plateInput = $('alprPlateFilter');
    if (plateInput) {
      plateInput.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') searchHits();
      });
    }

    const addBtn = $('alprWatchAddBtn');
    if (addBtn) addBtn.addEventListener('click', () => addWatch());

    const scanBtn = $('alprScanBtn');
    const fileInput = $('alprScanFile');
    if (scanBtn && fileInput) {
      scanBtn.addEventListener('click', () => fileInput.click());
      fileInput.addEventListener('change', () => {
        if (fileInput.files?.[0]) scanImage(fileInput.files[0]);
      });
    }

    const refreshBtn = $('alprRefreshBtn');
    if (refreshBtn) refreshBtn.addEventListener('click', () => load());
  }

  async function load() {
    await Promise.all([refreshStatus(), searchHits(), loadWatchlist()]);
  }

  async function refreshStatus() {
    const el = $('alprStatusPanel');
    try {
      const r = await fetch(`${API}/api/alpr/status`, {
        credentials: 'same-origin',
        headers: headers(),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      _status = await r.json();
      _renderStatus();
      const badge = $('alprBadge');
      if (badge) {
        const n = _status.hits_last_24h || 0;
        if (n > 0) {
          badge.style.display = '';
          badge.textContent = String(n);
        } else {
          badge.style.display = 'none';
        }
      }
    } catch (e) {
      if (el) {
        el.innerHTML = `<div class="alpr-error">Status unavailable: ${esc(e.message)}. Deploy dashboard with ALPR router and ensure Mongo is reachable.</div>`;
      }
    }
  }

  function _renderStatus() {
    const el = $('alprStatusPanel');
    if (!el || !_status) return;
    const w = _status.worker || {};
    const deps = _status.deps || {};
    const engOk = w.engine_ready === true || deps.engine_ready === true;
    const engCls = engOk ? 'ok' : 'warn';
    const streams = w.streams || {};
    const connected = streams.cameras_connected ?? '—';
    const enabled = _status.cameras_enabled ?? streams.cameras_enabled ?? '—';

    el.innerHTML = `
      <div class="alpr-kpi-row">
        <div class="alpr-kpi">
          <div class="alpr-kpi-label">Worker engine</div>
          <div class="alpr-kpi-value ${engCls}">${engOk ? 'Ready' : 'Not ready'}</div>
          <div class="alpr-kpi-sub">${esc(w.engine_error || deps.error || 'Fast-ALPR + OpenCV')}</div>
        </div>
        <div class="alpr-kpi">
          <div class="alpr-kpi-label">Cameras</div>
          <div class="alpr-kpi-value">${esc(connected)} <span class="alpr-kpi-muted">/ ${esc(enabled)}</span></div>
          <div class="alpr-kpi-sub">connected / enabled</div>
        </div>
        <div class="alpr-kpi">
          <div class="alpr-kpi-label">Hits (24h)</div>
          <div class="alpr-kpi-value">${esc(_status.hits_last_24h ?? 0)}</div>
          <div class="alpr-kpi-sub">matched watchlist</div>
        </div>
        <div class="alpr-kpi">
          <div class="alpr-kpi-label">Watchlist</div>
          <div class="alpr-kpi-value">${esc(_status.watchlist_count ?? 0)}</div>
          <div class="alpr-kpi-sub">active plates</div>
        </div>
        <div class="alpr-kpi">
          <div class="alpr-kpi-label">Worker cycle</div>
          <div class="alpr-kpi-value">${esc(w.cycle ?? '—')}</div>
          <div class="alpr-kpi-sub">${esc(w.updated_at ? fmt(w.updated_at) : 'no heartbeat yet')}</div>
        </div>
      </div>
      ${w.last_error ? `<div class="alpr-error">Last worker error: ${esc(w.last_error)}</div>` : ''}
      ${_renderStreamTable(streams.streams || [])}
    `;
  }

  function _renderStreamTable(list) {
    if (!list || !list.length) {
      return `<div class="alpr-muted" style="margin-top:12px">No live stream telemetry yet. Start worker with <code>docker compose --profile alpr up -d alpr-worker</code> and set camera URLs in <code>config/alpr_cameras.json</code>.</div>`;
    }
    const rows = list
      .map(
        (s) => `
      <tr>
        <td>${esc(s.name || s.id)}</td>
        <td><span class="alpr-pill ${s.connected ? 'ok' : 'off'}">${s.connected ? 'live' : 'down'}</span></td>
        <td>${esc(s.stream_type || '—')}</td>
        <td>${esc(s.frames_ok ?? 0)} / ${esc(s.frames_fail ?? 0)}</td>
        <td class="alpr-muted">${esc(s.last_error || '—')}</td>
      </tr>`
      )
      .join('');
    return `
      <div class="alpr-card" style="margin-top:14px">
        <div class="alpr-card-title">Camera streams</div>
        <div class="alpr-table-wrap">
          <table class="alpr-table">
            <thead><tr><th>Camera</th><th>Status</th><th>Type</th><th>OK / Fail</th><th>Last error</th></tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>`;
  }

  async function searchHits() {
    const plate = ($('alprPlateFilter')?.value || '').trim();
    const defendantId = ($('alprDefFilter')?.value || '').trim();
    const matchedOnly = $('alprMatchedOnly')?.checked !== false;
    const since = ($('alprSince')?.value || '').trim();

    const params = new URLSearchParams();
    params.set('limit', '100');
    params.set('matched_only', matchedOnly ? 'true' : 'false');
    if (plate) params.set('plate', plate);
    if (defendantId) params.set('defendant_id', defendantId);
    if (since) {
      // date input → ISO start of day
      params.set('since', new Date(since + 'T00:00:00').toISOString());
    }

    const el = $('alprHitsBody');
    if (el) el.innerHTML = `<tr><td colspan="7" class="alpr-muted">Loading…</td></tr>`;

    try {
      const r = await fetch(`${API}/api/alpr/hits?${params}`, {
        credentials: 'same-origin',
        headers: headers(),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      _hits = data.hits || [];
      _renderHits();
    } catch (e) {
      if (el) {
        el.innerHTML = `<tr><td colspan="7" class="alpr-error">Failed to load hits: ${esc(e.message)}</td></tr>`;
      }
    }
  }

  function _renderHits() {
    const el = $('alprHitsBody');
    if (!el) return;
    if (!_hits.length) {
      el.innerHTML = `<tr><td colspan="7" class="alpr-muted">No hits for this filter.</td></tr>`;
      return;
    }
    el.innerHTML = _hits
      .map((h) => {
        const conf =
          h.confidence != null ? `${Math.round(Number(h.confidence) * 100)}%` : '—';
        return `<tr>
          <td><code class="alpr-plate">${esc(h.plate_text)}</code> <span class="alpr-muted">${esc(h.state || 'FL')}</span></td>
          <td>${esc(h.defendant_name || '—')}</td>
          <td class="alpr-muted">${esc(h.case_number || '—')}</td>
          <td>${esc(h.camera_name || h.camera_id || '—')}</td>
          <td>${esc(conf)}</td>
          <td>${h.matched ? '<span class="alpr-pill ok">match</span>' : '<span class="alpr-pill off">scan</span>'}</td>
          <td class="alpr-muted">${esc(fmt(h.timestamp))}</td>
        </tr>`;
      })
      .join('');
  }

  async function loadWatchlist() {
    const el = $('alprWatchBody');
    if (el) el.innerHTML = `<tr><td colspan="5" class="alpr-muted">Loading…</td></tr>`;
    try {
      const r = await fetch(`${API}/api/alpr/watchlist?active_only=true`, {
        credentials: 'same-origin',
        headers: headers(),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      _watch = data.watchlist || [];
      _renderWatch();
    } catch (e) {
      if (el) {
        el.innerHTML = `<tr><td colspan="5" class="alpr-error">${esc(e.message)}</td></tr>`;
      }
    }
  }

  function _renderWatch() {
    const el = $('alprWatchBody');
    if (!el) return;
    if (!_watch.length) {
      el.innerHTML = `<tr><td colspan="5" class="alpr-muted">No plates on watchlist yet.</td></tr>`;
      return;
    }
    el.innerHTML = _watch
      .map(
        (w) => `<tr>
        <td><code class="alpr-plate">${esc(w.plate_text)}</code></td>
        <td>${esc(w.defendant_name || '—')}</td>
        <td class="alpr-muted">${esc(w.defendant_id || '—')}</td>
        <td class="alpr-muted">${esc(w.case_number || '—')}</td>
        <td class="alpr-muted">${esc(fmt(w.updated_at || w.created_at))}</td>
      </tr>`
      )
      .join('');
  }

  async function addWatch() {
    const plate = ($('alprWatchPlate')?.value || '').trim();
    const defendantId = ($('alprWatchDefId')?.value || '').trim();
    const defendantName = ($('alprWatchDefName')?.value || '').trim();
    const caseNumber = ($('alprWatchCase')?.value || '').trim();
    const notes = ($('alprWatchNotes')?.value || '').trim();

    if (!plate || !defendantId) {
      toast('Plate and defendant ID are required', 'error');
      return;
    }

    try {
      const r = await fetch(`${API}/api/alpr/watchlist`, {
        method: 'POST',
        credentials: 'same-origin',
        headers: headers(true),
        body: JSON.stringify({
          plate_text: plate,
          defendant_id: defendantId,
          defendant_name: defendantName,
          case_number: caseNumber,
          notes,
        }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      toast(`Watching plate ${plate.toUpperCase()}`, 'success');
      ['alprWatchPlate', 'alprWatchDefId', 'alprWatchDefName', 'alprWatchCase', 'alprWatchNotes'].forEach(
        (id) => {
          const n = $(id);
          if (n) n.value = '';
        }
      );
      await loadWatchlist();
      await refreshStatus();
    } catch (e) {
      toast(e.message || 'Failed to add watchlist entry', 'error');
    }
  }

  async function scanImage(file) {
    const out = $('alprScanResults');
    if (out) out.innerHTML = `<div class="alpr-muted">Scanning ${esc(file.name)}…</div>`;
    try {
      const fd = new FormData();
      fd.append('file', file);
      const r = await fetch(`${API}/api/alpr/scan-image`, {
        method: 'POST',
        credentials: 'same-origin',
        body: fd,
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
      const dets = data.detections || [];
      if (!dets.length) {
        if (out) out.innerHTML = `<div class="alpr-muted">No plates detected (or model not ready on this host).</div>`;
        toast('No plates found', 'info');
        return;
      }
      if (out) {
        out.innerHTML = `
          <div class="alpr-card-title" style="margin-bottom:8px">${dets.length} plate(s) in ${esc(file.name)}</div>
          <ul class="alpr-scan-list">
            ${dets
              .map(
                (d) =>
                  `<li><code class="alpr-plate">${esc(d.plate_text)}</code>
                   <span class="alpr-muted">${Math.round((d.confidence || 0) * 100)}% · ${esc(d.state || 'FL')}</span>
                   <button type="button" class="alpr-link-btn" data-plate="${esc(d.plate_text)}">Add to watchlist</button>
                   </li>`
              )
              .join('')}
          </ul>`;
        out.querySelectorAll('[data-plate]').forEach((btn) => {
          btn.addEventListener('click', () => {
            const p = btn.getAttribute('data-plate');
            const inp = $('alprWatchPlate');
            if (inp) inp.value = p;
            toast('Plate filled — set defendant ID and save', 'info');
            $('alprWatchPlate')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
          });
        });
      }
      toast(`Found ${dets.length} plate(s)`, 'success');
    } catch (e) {
      if (out) out.innerHTML = `<div class="alpr-error">${esc(e.message)}</div>`;
      toast(e.message || 'Scan failed', 'error');
    } finally {
      const fi = $('alprScanFile');
      if (fi) fi.value = '';
    }
  }
})();
