/**
 * SLAdminHygiene — Superadmin data repair & hard-delete tools
 * Delete Jon/John Doe test pollution, fix mismatches, unlink wrong matches.
 */
const SLAdminHygiene = (() => {
  let _testData = null;
  let _searchResults = [];
  let _related = null;

  function toast(msg, type) {
    if (typeof window.toast === 'function') window.toast(msg, type);
    else if (window.SL && typeof SL.toast === 'function') SL.toast(msg, type);
    else console.log('[AdminHygiene]', type, msg);
  }

  async function _json(url, opts = {}) {
    const res = await fetch(url, {
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
      ...opts,
    });
    const text = await res.text();
    let data = {};
    try { data = text ? JSON.parse(text) : {}; } catch (_) {
      throw new Error(`Non-JSON (${res.status}): ${text.slice(0, 120)}`);
    }
    if (!res.ok) {
      const detail = data.detail || data.error || res.statusText;
      throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
    }
    return data;
  }

  function init() {
    const root = document.getElementById('tabAdminHygiene');
    if (!root) return;
    if (!root.dataset.bound) {
      root.dataset.bound = '1';
      _renderShell();
    }
    scanTestRecords(true);
  }

  function _renderShell() {
    const root = document.getElementById('tabAdminHygiene');
    if (!root) return;
    root.innerHTML = `
      <div class="container" style="max-width:1200px">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:18px;flex-wrap:wrap">
          <div>
            <h2 style="margin:0 0 6px;font-size:22px">🛡️ Superadmin Data Hygiene</h2>
            <p style="margin:0;color:var(--muted);font-size:13px;max-width:640px">
              Hard-delete test junk (Jon/John/Jane Doe), repair mismatched fields, and unlink wrong matches.
              Destructive actions are audited and require confirmation.
            </p>
          </div>
          <button class="btn-primary" onclick="SLAdminHygiene.scanTestRecords()" style="padding:8px 14px">↻ Scan test records</button>
        </div>

        <!-- PURGE TEST -->
        <div class="panel" style="padding:16px;border:1px solid var(--border);border-radius:12px;margin-bottom:16px;background:var(--panel)">
          <h3 style="margin:0 0 10px;font-size:15px">🧹 Purge Jon Doe / test pollution</h3>
          <p style="margin:0 0 12px;font-size:12px;color:var(--muted)">
            Finds <code>Jon Doe</code>, <code>John Doe</code>, <code>Jane Doe</code>, and other TEST names across arrests, defendants, leads, intake, and pipeline.
          </p>
          <div id="hygTestSummary" style="font-size:13px;margin-bottom:12px;color:var(--text-muted)">Not scanned yet.</div>
          <div id="hygTestList" style="max-height:220px;overflow:auto;margin-bottom:12px;font-size:12px"></div>
          <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
            <button class="btn-sm" onclick="SLAdminHygiene.scanTestRecords()" style="padding:6px 12px">Preview hits</button>
            <button class="btn-sm" onclick="SLAdminHygiene.purgeTest(true)" style="padding:6px 12px;background:rgba(245,158,11,.2);border:1px solid #f59e0b;color:#fbbf24">Dry-run purge</button>
            <button class="btn-sm" onclick="SLAdminHygiene.purgeTest(false)" style="padding:6px 12px;background:rgba(239,68,68,.25);border:1px solid #ef4444;color:#fca5a5;font-weight:700">🔥 PURGE TEST DATA</button>
          </div>
        </div>

        <!-- SEARCH + FIX -->
        <div class="panel" style="padding:16px;border:1px solid var(--border);border-radius:12px;margin-bottom:16px;background:var(--panel)">
          <h3 style="margin:0 0 10px;font-size:15px">🔍 Find &amp; repair a record</h3>
          <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">
            <input id="hygSearchQ" type="text" placeholder="Name, booking #, defendant id…" style="flex:1;min-width:200px;padding:8px 12px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text)">
            <select id="hygSearchCol" style="padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text)">
              <option value="arrests">Arrests / leads</option>
              <option value="defendants">Defendants</option>
              <option value="prospective_bonds">Lead pipeline</option>
              <option value="intake_queue">Intake queue</option>
              <option value="active_bonds">Active bonds</option>
              <option value="matches">Matches</option>
            </select>
            <button class="btn-primary" onclick="SLAdminHygiene.search()" style="padding:8px 14px">Search</button>
          </div>
          <div id="hygSearchResults" style="max-height:280px;overflow:auto;font-size:12px"></div>
        </div>

        <!-- RELATED + DELETE -->
        <div class="panel" style="padding:16px;border:1px solid var(--border);border-radius:12px;margin-bottom:16px;background:var(--panel)">
          <h3 style="margin:0 0 10px;font-size:15px">🗑️ Hard-delete identity graph</h3>
          <p style="margin:0 0 12px;font-size:12px;color:var(--muted)">
            Removes matching rows from arrests, defendants, leads, matches, intake, notes, etc.
            Type <strong>DELETE</strong> to confirm. Active bonds are blocked unless force is checked.
          </p>
          <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;margin-bottom:12px">
            <label style="font-size:11px;color:var(--muted)">Booking #
              <input id="hygDelBooking" type="text" placeholder="BK-…" style="width:100%;margin-top:4px;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text)">
            </label>
            <label style="font-size:11px;color:var(--muted)">Full name (exact for name-only delete)
              <input id="hygDelName" type="text" placeholder="Jon Doe" style="width:100%;margin-top:4px;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text)">
            </label>
            <label style="font-size:11px;color:var(--muted)">County
              <input id="hygDelCounty" type="text" placeholder="Lee" style="width:100%;margin-top:4px;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text)">
            </label>
            <label style="font-size:11px;color:var(--muted)">State
              <input id="hygDelState" type="text" placeholder="FL" maxlength="2" style="width:100%;margin-top:4px;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text)">
            </label>
            <label style="font-size:11px;color:var(--muted)">Defendant ID
              <input id="hygDelDefId" type="text" placeholder="uuid…" style="width:100%;margin-top:4px;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text)">
            </label>
            <label style="font-size:11px;color:var(--muted)">Confirm (type DELETE)
              <input id="hygDelConfirm" type="text" placeholder="DELETE" style="width:100%;margin-top:4px;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text)">
            </label>
          </div>
          <label style="display:flex;align-items:center;gap:8px;font-size:12px;margin-bottom:12px;cursor:pointer">
            <input type="checkbox" id="hygForceBond"> Allow delete even if active bond exists
          </label>
          <div style="display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn-sm" onclick="SLAdminHygiene.previewDelete()" style="padding:6px 12px">Preview related docs</button>
            <button class="btn-sm" onclick="SLAdminHygiene.hardDelete(true)" style="padding:6px 12px;background:rgba(245,158,11,.2);border:1px solid #f59e0b;color:#fbbf24">Dry-run delete</button>
            <button class="btn-sm" onclick="SLAdminHygiene.hardDelete(false)" style="padding:6px 12px;background:rgba(239,68,68,.3);border:1px solid #ef4444;color:#fecaca;font-weight:700">Hard delete</button>
          </div>
          <pre id="hygDeleteOut" style="margin-top:12px;padding:12px;background:#0b1220;border-radius:8px;font-size:11px;max-height:240px;overflow:auto;color:#94a3b8;display:none"></pre>
        </div>

        <!-- PATCH FORM -->
        <div class="panel" style="padding:16px;border:1px solid var(--border);border-radius:12px;margin-bottom:16px;background:var(--panel)">
          <h3 style="margin:0 0 10px;font-size:15px">✏️ Fix mismatched arrest fields</h3>
          <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin-bottom:12px">
            <label style="font-size:11px;color:var(--muted)">Booking # (required)
              <input id="hygPatchBooking" type="text" style="width:100%;margin-top:4px;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text)">
            </label>
            <label style="font-size:11px;color:var(--muted)">County (lookup)
              <input id="hygPatchCounty" type="text" style="width:100%;margin-top:4px;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text)">
            </label>
            <label style="font-size:11px;color:var(--muted)">State (lookup)
              <input id="hygPatchState" type="text" maxlength="2" style="width:100%;margin-top:4px;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text)">
            </label>
            <label style="font-size:11px;color:var(--muted)">Full name
              <input id="hygPatchName" type="text" style="width:100%;margin-top:4px;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text)">
            </label>
            <label style="font-size:11px;color:var(--muted)">Bond amount
              <input id="hygPatchBond" type="number" min="0" step="1" style="width:100%;margin-top:4px;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text)">
            </label>
            <label style="font-size:11px;color:var(--muted)">Status
              <input id="hygPatchStatus" type="text" placeholder="In Custody" style="width:100%;margin-top:4px;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text)">
            </label>
            <label style="font-size:11px;color:var(--muted)">Charges
              <input id="hygPatchCharges" type="text" style="width:100%;margin-top:4px;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text)">
            </label>
            <label style="font-size:11px;color:var(--muted)">Reason (audit)
              <input id="hygPatchReason" type="text" placeholder="Wrong name from scrape" style="width:100%;margin-top:4px;padding:8px;border-radius:8px;border:1px solid var(--border);background:var(--bg);color:var(--text)">
            </label>
          </div>
          <button class="btn-primary" onclick="SLAdminHygiene.patchArrest()" style="padding:8px 14px">Save arrest fix</button>
        </div>
      </div>
    `;
  }

  async function scanTestRecords(silent) {
    try {
      _testData = await _json('/api/admin/hygiene/test-records');
      const sum = document.getElementById('hygTestSummary');
      const list = document.getElementById('hygTestList');
      if (!sum) return;
      const c = _testData.counts || {};
      sum.innerHTML = Object.keys(c).map(k =>
        `<span style="display:inline-block;margin:0 10px 6px 0;padding:4px 10px;border-radius:8px;background:rgba(239,68,68,.12);border:1px solid rgba(239,68,68,.35);color:#fca5a5"><strong>${c[k]}</strong> in ${k}</span>`
      ).join('') + `<div style="margin-top:8px;color:var(--muted)">Total hits: <strong>${_testData.total_hits || 0}</strong></div>`;

      const rows = [];
      const recs = _testData.records || {};
      Object.keys(recs).forEach(cname => {
        (recs[cname] || []).slice(0, 40).forEach(r => {
          const name = r.full_name || r.defendant_name || r.name || '—';
          const bk = r.booking_number || '—';
          const co = r.county || '';
          const st = r.state || '';
          rows.push(`<div style="padding:6px 0;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap">
            <span><code style="color:#94a3b8">${cname}</code> · <strong>${_esc(name)}</strong> · ${ _esc(bk)} ${co ? '· ' + _esc(co) : ''} ${st}</span>
            <span>
              <button class="btn-sm" style="font-size:10px;padding:2px 8px" onclick="SLAdminHygiene.fillDelete('${_escAttr(bk)}','${_escAttr(name)}','${_escAttr(co)}','${_escAttr(st)}')">Select</button>
            </span>
          </div>`);
        });
      });
      if (list) list.innerHTML = rows.join('') || '<div style="color:var(--muted)">No test-name hits. Clean!</div>';
      if (!silent) toast(`Found ${_testData.total_hits || 0} test-name hits`, 'success');
    } catch (e) {
      toast(e.message, 'error');
      const sum = document.getElementById('hygTestSummary');
      if (sum) sum.innerHTML = `<span style="color:#f87171">${_esc(e.message)}</span>`;
    }
  }

  function fillDelete(booking, name, county, state) {
    const set = (id, v) => { const el = document.getElementById(id); if (el && v) el.value = v; };
    set('hygDelBooking', booking === '—' ? '' : booking);
    set('hygDelName', name === '—' ? '' : name);
    set('hygDelCounty', county);
    set('hygDelState', state);
    set('hygDelConfirm', 'DELETE');
    set('hygPatchBooking', booking === '—' ? '' : booking);
    set('hygPatchCounty', county);
    set('hygPatchState', state);
    set('hygPatchName', name === '—' ? '' : name);
    toast('Filled delete/fix form', 'success');
  }

  async function purgeTest(dryRun) {
    if (!dryRun) {
      if (!confirm('This will PERMANENTLY delete all Jon/John/Jane Doe and TEST-name records. Continue?')) return;
    }
    try {
      const data = await _json('/api/admin/hygiene/purge-test', {
        method: 'POST',
        body: JSON.stringify({ confirm: 'PURGE_TEST', dry_run: !!dryRun }),
      });
      if (dryRun) {
        toast(`Dry-run: would delete ${data.total} docs`, 'success');
        console.log('[purge-test dry]', data);
        alert('Dry-run would delete:\n' + JSON.stringify(data.would_delete_counts || data, null, 2));
      } else {
        toast(`Purged ${data.total} test records`, 'success');
        scanTestRecords();
      }
    } catch (e) {
      toast(e.message, 'error');
    }
  }

  async function search() {
    const q = document.getElementById('hygSearchQ')?.value || '';
    const collection = document.getElementById('hygSearchCol')?.value || 'arrests';
    if (!q.trim()) { toast('Enter a search term', 'error'); return; }
    try {
      const data = await _json(`/api/admin/hygiene/search?q=${encodeURIComponent(q)}&collection=${encodeURIComponent(collection)}`);
      _searchResults = data.results || [];
      const el = document.getElementById('hygSearchResults');
      if (!el) return;
      if (!_searchResults.length) {
        el.innerHTML = '<div style="color:var(--muted)">No results</div>';
        return;
      }
      el.innerHTML = _searchResults.map(r => {
        const name = r.full_name || r.defendant_name || r.name || '—';
        const bk = r.booking_number || '—';
        const co = r.county || '';
        const st = r.state || '';
        return `<div style="padding:8px 0;border-bottom:1px solid var(--border)">
          <strong>${_esc(name)}</strong>
          <span style="color:var(--muted)"> · ${ _esc(bk)} · ${ _esc(co)} ${st} · score ${r.lead_score ?? '—'}</span>
          <div style="margin-top:4px;display:flex;gap:6px;flex-wrap:wrap">
            <button class="btn-sm" style="font-size:10px;padding:2px 8px" onclick="SLAdminHygiene.fillDelete('${_escAttr(bk)}','${_escAttr(name)}','${_escAttr(co)}','${_escAttr(st)}')">Use for delete</button>
            <button class="btn-sm" style="font-size:10px;padding:2px 8px" onclick="SLAdminHygiene.loadRelated('${_escAttr(bk)}')">Related graph</button>
          </div>
        </div>`;
      }).join('');
    } catch (e) {
      toast(e.message, 'error');
    }
  }

  async function loadRelated(booking) {
    try {
      const data = await _json(`/api/admin/hygiene/related?booking_number=${encodeURIComponent(booking)}`);
      _related = data;
      const out = document.getElementById('hygDeleteOut');
      if (out) {
        out.style.display = 'block';
        out.textContent = JSON.stringify({
          booking_number: data.booking_number,
          collections_hit: data.collections_hit,
          counts: Object.fromEntries(
            Object.entries(data.graph || {}).map(([k, v]) => [k, (v || []).length])
          ),
        }, null, 2);
      }
      toast(`Related: ${(data.collections_hit || []).join(', ') || 'none'}`, 'success');
    } catch (e) {
      toast(e.message, 'error');
    }
  }

  async function previewDelete() {
    const booking = document.getElementById('hygDelBooking')?.value || '';
    if (!booking) { toast('Enter a booking number', 'error'); return; }
    await loadRelated(booking);
  }

  async function hardDelete(dryRun) {
    const body = {
      booking_number: document.getElementById('hygDelBooking')?.value || null,
      full_name: document.getElementById('hygDelName')?.value || null,
      county: document.getElementById('hygDelCounty')?.value || null,
      state: document.getElementById('hygDelState')?.value || null,
      defendant_id: document.getElementById('hygDelDefId')?.value || null,
      confirm: document.getElementById('hygDelConfirm')?.value || '',
      dry_run: !!dryRun,
      allow_active_bond: !!document.getElementById('hygForceBond')?.checked,
    };
    // clean nulls
    Object.keys(body).forEach(k => { if (body[k] === '' || body[k] === null) body[k] = body[k] === false ? false : (body[k] === 0 ? 0 : (k === 'dry_run' || k === 'allow_active_bond' ? body[k] : null)); });
    if (!body.booking_number && !body.full_name && !body.defendant_id) {
      toast('Need booking, name, or defendant id', 'error');
      return;
    }
    if (!dryRun && !confirm('PERMANENT hard delete. Are you sure?')) return;
    try {
      const data = await _json('/api/admin/hygiene/delete', {
        method: 'POST',
        body: JSON.stringify(body),
      });
      const out = document.getElementById('hygDeleteOut');
      if (out) {
        out.style.display = 'block';
        out.textContent = JSON.stringify(data, null, 2);
      }
      toast(dryRun ? 'Dry-run complete' : 'Hard delete complete', 'success');
      if (!dryRun) scanTestRecords(true);
    } catch (e) {
      toast(e.message, 'error');
    }
  }

  async function patchArrest() {
    const booking = document.getElementById('hygPatchBooking')?.value?.trim();
    if (!booking) { toast('Booking # required', 'error'); return; }
    const body = {
      booking_number: booking,
      county: document.getElementById('hygPatchCounty')?.value || null,
      state: document.getElementById('hygPatchState')?.value || null,
      full_name: document.getElementById('hygPatchName')?.value || null,
      status: document.getElementById('hygPatchStatus')?.value || null,
      charges: document.getElementById('hygPatchCharges')?.value || null,
      reason: document.getElementById('hygPatchReason')?.value || '',
    };
    const bond = document.getElementById('hygPatchBond')?.value;
    if (bond !== '' && bond != null) body.bond_amount = parseFloat(bond);
    Object.keys(body).forEach(k => { if (body[k] === '' || body[k] === null) delete body[k]; });
    body.booking_number = booking;
    try {
      const data = await _json('/api/admin/hygiene/arrest', {
        method: 'PATCH',
        body: JSON.stringify(body),
      });
      toast('Arrest updated', 'success');
      console.log('[patch arrest]', data);
    } catch (e) {
      toast(e.message, 'error');
    }
  }

  /** Called from defendant card delete button */
  async function deleteFromCard(booking, name, county, state) {
    fillDelete(booking, name, county || '', state || '');
    const conf = prompt(`Hard-delete "${name}" (${booking})?\nType DELETE to confirm:`, '');
    if ((conf || '').trim().toUpperCase() !== 'DELETE') {
      toast('Cancelled', 'error');
      return;
    }
    document.getElementById('hygDelConfirm').value = 'DELETE';
    await hardDelete(false);
    if (typeof loadDefendants === 'function') loadDefendants();
    if (typeof applyFilters === 'function') applyFilters();
  }

  function _esc(s) {
    return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function _escAttr(s) {
    return String(s ?? '').replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
  }

  return {
    init,
    scanTestRecords,
    purgeTest,
    search,
    fillDelete,
    loadRelated,
    previewDelete,
    hardDelete,
    patchArrest,
    deleteFromCard,
  };
})();
