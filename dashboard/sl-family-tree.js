/**
 * sl-family-tree.js — 1st/2nd degree Family & Relationship Network UI
 *
 * Backend: /api/family-tree/* (Mongo family_relationships + bond co-signors)
 * Node format is relatives-tree compatible (parents/children/siblings/spouses).
 * Renderer: vanilla hierarchical cards (no React dependency).
 */
const SLFamilyTree = (() => {
  'use strict';

  let _degree = 1;
  let _rootName = '';
  let _graph = null;
  let _sessionDismissed = new Set(); // soft hide for this browser session only

  function _esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  function _toast(msg, type) {
    if (typeof SL !== 'undefined' && SL.toast) return SL.toast(msg, type || 'info');
    if (window.SLHealth && typeof window.SLHealth._showToast === 'function') {
      return window.SLHealth._showToast(msg, type);
    }
    console.log('[SLFamilyTree]', msg);
  }

  function init() {
    const nameEl = document.getElementById('ftPersonName');
    if (nameEl && !nameEl.value && _rootName) nameEl.value = _rootName;
    setDegree(_degree, true);
    if (_rootName) loadGraph();
  }

  function setDegree(n, silent) {
    _degree = n === 2 ? 2 : 1;
    const b1 = document.getElementById('ftDegree1');
    const b2 = document.getElementById('ftDegree2');
    if (b1) {
      b1.style.background = _degree === 1 ? 'var(--accent)' : 'var(--panel)';
      b1.style.color = _degree === 1 ? '#000' : 'var(--text)';
      b1.style.border = _degree === 1 ? 'none' : '1px solid var(--border)';
    }
    if (b2) {
      b2.style.background = _degree === 2 ? 'var(--accent)' : 'var(--panel)';
      b2.style.color = _degree === 2 ? '#000' : 'var(--text)';
      b2.style.border = _degree === 2 ? 'none' : '1px solid var(--border)';
    }
    if (!silent && _rootName) loadGraph();
  }

  async function loadGraph(nameOpt) {
    const nameEl = document.getElementById('ftPersonName');
    const name = (nameOpt || nameEl?.value || '').trim();
    if (!name) {
      _toast('Enter a person name first', 'error');
      return;
    }
    _rootName = name;
    if (nameEl && !nameEl.value) nameEl.value = name;

    const canvas = document.getElementById('ftGraphCanvas');
    const list = document.getElementById('ftRelList');
    const title = document.getElementById('ftGraphTitle');
    const meta = document.getElementById('ftGraphMeta');
    if (canvas) canvas.innerHTML = '<div class="loading" style="padding:40px;text-align:center">Loading family graph…</div>';
    if (list) list.innerHTML = 'Loading…';

    try {
      const res = await fetch(
        `/api/family-tree/graph/${encodeURIComponent(name)}?degree=${_degree}`,
        { credentials: 'same-origin' }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || data.error || `HTTP ${res.status}`);
      _graph = data;
      if (title) title.textContent = `🌳 ${data.root_name || name}`;
      if (meta) meta.textContent = `${data.total_nodes || 0} people · degree ≤ ${_degree}`;
      _renderGraph(data);
      _renderRelList(data);
    } catch (e) {
      if (canvas) {
        canvas.innerHTML = `<div style="padding:24px;color:var(--danger);text-align:center">${_esc(e.message)}</div>`;
      }
      if (list) list.innerHTML = `<span style="color:var(--danger)">${_esc(e.message)}</span>`;
      _toast(`Family tree error: ${e.message}`, 'error');
    }
  }

  function _nodeById(nodes, id) {
    return (nodes || []).find(n => n.id === id);
  }

  function _renderGraph(data) {
    const canvas = document.getElementById('ftGraphCanvas');
    if (!canvas) return;
    const nodes = (data.nodes || []).filter(n => !_sessionDismissed.has(n.id));
    if (!nodes.length) {
      canvas.innerHTML = `<div style="padding:40px;text-align:center;color:var(--muted)">
        <div style="font-size:28px;margin-bottom:8px">🌱</div>
        <div>No relatives linked yet for <strong>${_esc(data.root_name)}</strong>.</div>
        <div style="margin-top:8px;font-size:12px">Use <strong>+ Link Relative</strong> or open this person from Active Bonds.</div>
      </div>`;
      return;
    }

    const rootId = data.root_id;
    const root = _nodeById(nodes, rootId) || nodes[0];
    const parents = (root.parents || []).map(p => _nodeById(nodes, p.id)).filter(Boolean);
    const spouses = (root.spouses || []).map(p => _nodeById(nodes, p.id)).filter(Boolean);
    const siblings = (root.siblings || []).map(p => _nodeById(nodes, p.id)).filter(Boolean);
    const children = (root.children || []).map(p => _nodeById(nodes, p.id)).filter(Boolean);
    const relatives = (root.relatives || []).map(p => _nodeById(nodes, p.id)).filter(Boolean);

    // Second-degree: parents of parents / children of children (when degree=2)
    let grandparents = [];
    let grandchildren = [];
    if (_degree >= 2) {
      parents.forEach(p => {
        (p.parents || []).forEach(gp => {
          const n = _nodeById(nodes, gp.id);
          if (n && n.id !== rootId) grandparents.push(n);
        });
      });
      children.forEach(c => {
        (c.children || []).forEach(gc => {
          const n = _nodeById(nodes, gc.id);
          if (n && n.id !== rootId) grandchildren.push(n);
        });
      });
    }

    const seen = new Set([rootId]);
    function dedupe(arr) {
      return arr.filter(n => {
        if (!n || seen.has(n.id)) return false;
        seen.add(n.id);
        return true;
      });
    }

    canvas.innerHTML = `
      ${_tier('Grandparents / elders', dedupe(grandparents))}
      ${_tier('Parents', dedupe(parents))}
      ${_tier('Root + spouses / siblings', [root, ...dedupe(spouses), ...dedupe(siblings)], true)}
      ${_tier('Children', dedupe(children))}
      ${_tier('Grandchildren', dedupe(grandchildren))}
      ${_tier('Other relatives / co-indemnitors', dedupe(relatives))}
    `;
  }

  function _tier(label, nodes, isRootTier) {
    if (!nodes || !nodes.length) return '';
    return `
      <div class="ft-tier-label">${_esc(label)}</div>
      <div class="ft-tier">
        ${nodes.map(n => _card(n, isRootTier && n.role === 'root')).join('')}
      </div>
    `;
  }

  function _card(n, isRoot) {
    const role = n.role || 'relative';
    const cls = [
      'ft-node',
      isRoot || role === 'root' ? 'root' : '',
      role === 'defendant' ? 'defendant' : '',
      role === 'indemnitor' ? 'indemnitor' : '',
    ].filter(Boolean).join(' ');
    const badges = [];
    if (n.has_active_bond) badges.push('<span style="color:#00d4aa">● Active bond</span>');
    if (n.has_warrants) badges.push('<span style="color:#ff4757">⚠ Warrant</span>');
    const phone = n.phone ? `<div class="ft-node-meta" style="font-family:monospace">${_esc(n.phone)}</div>` : '';
    const email = n.email ? `<div class="ft-node-meta">${_esc(n.email)}</div>` : '';
    return `
      <div class="${cls}" data-node-id="${_esc(n.id)}">
        <div class="ft-node-name">${_esc(n.name)}</div>
        <div class="ft-node-role">${_esc(role)}</div>
        ${phone}${email}
        ${badges.length ? `<div class="ft-node-meta">${badges.join(' · ')}</div>` : ''}
        <div style="margin-top:8px;display:flex;gap:4px;justify-content:center;flex-wrap:wrap">
          <button class="btn-export" style="font-size:10px;padding:3px 8px" onclick="SLFamilyTree.focusPerson(${JSON.stringify(n.name)})">Focus</button>
          <button class="btn-export" style="font-size:10px;padding:3px 8px" onclick="SLFamilyTree.dismissSession(${JSON.stringify(n.id)})" title="Hide for this session">✕</button>
        </div>
      </div>
    `;
  }

  function _renderRelList(data) {
    const list = document.getElementById('ftRelList');
    if (!list) return;
    const rels = data.relationships || [];
    if (!rels.length) {
      // Derive from nodes when backend hasn't returned relationship docs
      const root = (data.nodes || []).find(n => n.id === data.root_id) || (data.nodes || [])[0];
      if (!root) {
        list.innerHTML = '<span style="color:var(--muted)">No relationship records.</span>';
        return;
      }
      const rows = [];
      ['parents', 'children', 'siblings', 'spouses', 'relatives'].forEach(k => {
        (root[k] || []).forEach(link => {
          const other = (data.nodes || []).find(n => n.id === link.id);
          rows.push({
            relation_type: k === 'relatives' ? (link.type || 'relative') : k.replace(/s$/, ''),
            relative_name: other?.name || link.id,
            relationship_id: link.relationship_id || null,
            confidence: link.confidence || '—',
          });
        });
      });
      if (!rows.length) {
        list.innerHTML = '<span style="color:var(--muted)">No stored links yet. Add a relative to persist the graph.</span>';
        return;
      }
      list.innerHTML = rows.map(r => _relRow(r)).join('');
      return;
    }
    list.innerHTML = rels
      .filter(r => r.status !== 'soft_deleted')
      .map(r => _relRow(r))
      .join('') || '<span style="color:var(--muted)">No active links.</span>';
  }

  function _relRow(r) {
    const id = r.relationship_id || r._id || r.id || '';
    const delBtn = id
      ? `<button class="btn-export" style="font-size:10px;padding:3px 8px;background:#3b1a1a;color:#fca5a5;border:1px solid #7f1d1d"
           onclick="SLFamilyTree.softDelete(${JSON.stringify(String(id))})">🗑</button>`
      : '';
    return `
      <div class="ft-rel-row">
        <div>
          <strong>${_esc(r.relative_name || r.name || '—')}</strong>
          <div style="font-size:11px;color:var(--muted);margin-top:2px">
            ${_esc(r.relation_type || 'relative')} · ${_esc(r.confidence || 'confirmed')}
            ${r.phone ? ` · ${_esc(r.phone)}` : ''}
          </div>
          ${r.notes ? `<div style="font-size:11px;color:var(--muted);margin-top:2px">${_esc(r.notes)}</div>` : ''}
        </div>
        ${delBtn}
      </div>
    `;
  }

  function focusPerson(name) {
    const el = document.getElementById('ftPersonName');
    if (el) el.value = name;
    loadGraph(name);
  }

  function dismissSession(nodeId) {
    _sessionDismissed.add(nodeId);
    if (_graph) {
      _renderGraph(_graph);
      _toast('Hidden for this session (not deleted)', 'info');
    }
  }

  async function softDelete(relId) {
    if (!relId) return;
    if (!confirm('Soft-delete this relationship? Historical audit data is retained.')) return;
    try {
      const res = await fetch(`/api/family-tree/relationship/${encodeURIComponent(relId)}`, {
        method: 'DELETE',
        credentials: 'same-origin',
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.success === false) throw new Error(data.detail || data.error || `HTTP ${res.status}`);
      _toast('Relationship soft-deleted', 'success');
      if (_rootName) loadGraph(_rootName);
    } catch (e) {
      _toast(`Delete failed: ${e.message}`, 'error');
    }
  }

  function openAddModal(prefill) {
    _ensureAddModal();
    const m = document.getElementById('ftAddModal');
    const person = document.getElementById('ftAddPersonId');
    const relName = document.getElementById('ftAddRelName');
    const relType = document.getElementById('ftAddRelType');
    const phone = document.getElementById('ftAddPhone');
    const email = document.getElementById('ftAddEmail');
    const notes = document.getElementById('ftAddNotes');
    const deg = document.getElementById('ftAddDegree');
    if (person) person.value = prefill?.person_id || _rootName || document.getElementById('ftPersonName')?.value || '';
    if (relName) relName.value = prefill?.relative_name || '';
    if (relType) relType.value = prefill?.relation_type || 'relative';
    if (phone) phone.value = prefill?.phone || '';
    if (email) email.value = prefill?.email || '';
    if (notes) notes.value = prefill?.notes || '';
    if (deg) deg.value = String(prefill?.degree || _degree || 1);
    m.style.display = 'flex';
    relName?.focus();
  }

  function closeAddModal() {
    const m = document.getElementById('ftAddModal');
    if (m) m.style.display = 'none';
  }

  async function submitAdd() {
    const person_id = document.getElementById('ftAddPersonId')?.value?.trim();
    const relative_name = document.getElementById('ftAddRelName')?.value?.trim();
    const relation_type = document.getElementById('ftAddRelType')?.value || 'relative';
    const phone = document.getElementById('ftAddPhone')?.value?.trim() || null;
    const email = document.getElementById('ftAddEmail')?.value?.trim() || null;
    const notes = document.getElementById('ftAddNotes')?.value?.trim() || null;
    const degree = parseInt(document.getElementById('ftAddDegree')?.value || '1', 10) || 1;
    if (!person_id || !relative_name) {
      _toast('Person and relative name are required', 'error');
      return;
    }
    try {
      const res = await fetch('/api/family-tree/relationship', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          person_id,
          relative_name,
          relation_type,
          phone,
          email,
          notes,
          degree,
          confidence: 'confirmed',
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.success === false) {
        throw new Error(data.detail || data.error || `HTTP ${res.status}`);
      }
      _toast('Relative linked', 'success');
      closeAddModal();
      _rootName = person_id;
      const nameEl = document.getElementById('ftPersonName');
      if (nameEl) nameEl.value = person_id;
      loadGraph(person_id);
    } catch (e) {
      _toast(`Add failed: ${e.message}`, 'error');
    }
  }

  function _ensureAddModal() {
    if (document.getElementById('ftAddModal')) return;
    const m = document.createElement('div');
    m.id = 'ftAddModal';
    m.style.cssText = 'display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:10060;align-items:center;justify-content:center';
    m.innerHTML = `
      <div style="background:var(--bg);border:1px solid var(--border);border-radius:14px;width:min(480px,94vw);max-height:90vh;overflow:auto;box-shadow:0 20px 60px rgba(0,0,0,.5)">
        <div style="padding:16px 18px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center">
          <h3 style="margin:0;font-size:16px">+ Link Relative</h3>
          <button onclick="SLFamilyTree.closeAddModal()" style="background:none;border:none;color:var(--muted);font-size:20px;cursor:pointer">✕</button>
        </div>
        <div style="padding:16px 18px;display:flex;flex-direction:column;gap:10px">
          <label style="font-size:11px;color:var(--muted)">Anchor person
            <input id="ftAddPersonId" type="text" style="display:block;width:100%;margin-top:4px;padding:8px 10px;border-radius:8px;border:1px solid var(--border);background:var(--panel);color:var(--text)">
          </label>
          <label style="font-size:11px;color:var(--muted)">Relative full name
            <input id="ftAddRelName" type="text" style="display:block;width:100%;margin-top:4px;padding:8px 10px;border-radius:8px;border:1px solid var(--border);background:var(--panel);color:var(--text)">
          </label>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
            <label style="font-size:11px;color:var(--muted)">Relation
              <select id="ftAddRelType" style="display:block;width:100%;margin-top:4px;padding:8px 10px;border-radius:8px;border:1px solid var(--border);background:var(--panel);color:var(--text)">
                <option value="parent">Parent</option>
                <option value="child">Child</option>
                <option value="sibling">Sibling</option>
                <option value="spouse">Spouse</option>
                <option value="relative">Relative</option>
                <option value="co_indemnitor">Co-indemnitor</option>
              </select>
            </label>
            <label style="font-size:11px;color:var(--muted)">Degree
              <select id="ftAddDegree" style="display:block;width:100%;margin-top:4px;padding:8px 10px;border-radius:8px;border:1px solid var(--border);background:var(--panel);color:var(--text)">
                <option value="1">1st</option>
                <option value="2">2nd</option>
              </select>
            </label>
          </div>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
            <label style="font-size:11px;color:var(--muted)">Phone
              <input id="ftAddPhone" type="tel" style="display:block;width:100%;margin-top:4px;padding:8px 10px;border-radius:8px;border:1px solid var(--border);background:var(--panel);color:var(--text)">
            </label>
            <label style="font-size:11px;color:var(--muted)">Email
              <input id="ftAddEmail" type="email" style="display:block;width:100%;margin-top:4px;padding:8px 10px;border-radius:8px;border:1px solid var(--border);background:var(--panel);color:var(--text)">
            </label>
          </div>
          <label style="font-size:11px;color:var(--muted)">Notes
            <input id="ftAddNotes" type="text" style="display:block;width:100%;margin-top:4px;padding:8px 10px;border-radius:8px;border:1px solid var(--border);background:var(--panel);color:var(--text)">
          </label>
        </div>
        <div style="padding:14px 18px;border-top:1px solid var(--border);display:flex;justify-content:flex-end;gap:8px">
          <button class="btn-export" style="padding:8px 14px" onclick="SLFamilyTree.closeAddModal()">Cancel</button>
          <button class="btn-primary" style="padding:8px 16px;background:var(--accent);color:#000;border:none;border-radius:8px;font-weight:700;cursor:pointer" onclick="SLFamilyTree.submitAdd()">Save Link</button>
        </div>
      </div>`;
    m.addEventListener('click', e => { if (e.target === m) closeAddModal(); });
    document.body.appendChild(m);
  }

  /** Open from Active Bonds / Bond Desk with a defendant or indemnitor name. */
  function openForPerson(name, opts) {
    const btn = document.querySelector('[data-tab="tabFamilyTree"]');
    if (btn && typeof SL !== 'undefined' && SL.switchTab) {
      SL.switchTab(btn);
    }
    const el = document.getElementById('ftPersonName');
    if (el) el.value = name || '';
    _rootName = name || '';
    if (opts?.degree) setDegree(opts.degree, true);
    setTimeout(() => {
      init();
      if (name) loadGraph(name);
      if (opts?.add) openAddModal({ person_id: name, relative_name: opts.relative_name });
    }, 50);
  }

  /** Embed a compact panel into the Active Bonds edit drawer. */
  function mountBondPanel(containerId, personName) {
    const el = document.getElementById(containerId);
    if (!el || !personName) return;
    el.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
        <div style="font-size:12px;color:var(--muted)">Family network for <strong style="color:var(--text)">${_esc(personName)}</strong></div>
        <div style="display:flex;gap:6px">
          <button type="button" class="btn-export" style="font-size:11px;padding:5px 10px;background:#10b981;color:#000"
            onclick="SLFamilyTree.openForPerson(${JSON.stringify(personName)})">👪 Open tree</button>
          <button type="button" class="btn-export" style="font-size:11px;padding:5px 10px;background:#6366f1;color:#fff"
            onclick="SLFamilyTree.openForPerson(${JSON.stringify(personName)},{add:true})">+ Link</button>
        </div>
      </div>
      <div id="${containerId}_preview" style="margin-top:8px;font-size:12px;color:var(--muted)">Loading…</div>
    `;
    fetch(`/api/family-tree/graph/${encodeURIComponent(personName)}?degree=1`, { credentials: 'same-origin' })
      .then(r => r.json())
      .then(data => {
        const prev = document.getElementById(`${containerId}_preview`);
        if (!prev) return;
        const n = data.total_nodes || 0;
        const names = (data.nodes || []).filter(x => x.id !== data.root_id).slice(0, 6).map(x => x.name);
        prev.innerHTML = n <= 1
          ? 'No relatives linked yet.'
          : `${n - 1} linked: ${_esc(names.join(', '))}${n > 7 ? '…' : ''}`;
      })
      .catch(() => {
        const prev = document.getElementById(`${containerId}_preview`);
        if (prev) prev.textContent = 'Family graph unavailable.';
      });
  }

  return {
    init,
    setDegree,
    loadGraph,
    openAddModal,
    closeAddModal,
    submitAdd,
    softDelete,
    dismissSession,
    focusPerson,
    openForPerson,
    mountBondPanel,
  };
})();

window.SLFamilyTree = SLFamilyTree;
