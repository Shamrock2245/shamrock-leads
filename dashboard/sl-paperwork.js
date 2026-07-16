/**
 * sl-paperwork.js — Paperwork Configuration tab
 * Renders OSI / Palmetto TEMPLATE_MAP + DOC_RULES from /api/paperwork/config
 */
const SLPaperwork = {
  async load() {
    const rulesEl = document.getElementById('paperworkDocRules');
    if (rulesEl) rulesEl.textContent = 'Loading…';
    ['tablePaperworkOsi', 'tablePaperworkPalmetto'].forEach(id => {
      const tb = document.querySelector(`#${id} tbody`);
      if (tb) tb.innerHTML = `<tr><td colspan="4" class="loading">Loading…</td></tr>`;
    });

    try {
      const res = await fetch('/api/paperwork/config', { credentials: 'same-origin' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.success === false) throw new Error(data.error || 'Config error');

      this.renderSummary(data.counts);
      this.renderDocRules(data.doc_rules);
      this.renderTable('tablePaperworkOsi', data.template_map?.osi);
      this.renderTable('tablePaperworkPalmetto', data.template_map?.palmetto);
    } catch (err) {
      console.error(err);
      if (window.SL && SL.flash) SL.flash('Error loading paperwork config: ' + err.message, 'error');
      else if (window.SL && SL.notify) SL.notify('Paperwork config: ' + err.message, 'error');
      if (rulesEl) {
        rulesEl.textContent = 'Failed to load: ' + err.message;
        rulesEl.style.color = 'var(--danger)';
      }
      ['tablePaperworkOsi', 'tablePaperworkPalmetto'].forEach(id => {
        const tb = document.querySelector(`#${id} tbody`);
        if (tb) tb.innerHTML = `<tr><td colspan="4" style="color:var(--danger)">Failed to load</td></tr>`;
      });
    }
  },

  renderSummary(counts) {
    let bar = document.getElementById('paperworkConfigSummary');
    if (!bar) {
      const tab = document.getElementById('tabPaperwork');
      const header = tab?.querySelector('.header-banner, .glass-panel');
      if (header && header.parentNode) {
        bar = document.createElement('div');
        bar.id = 'paperworkConfigSummary';
        bar.style.cssText = 'display:flex;flex-wrap:wrap;gap:12px;margin:16px 0;';
        header.after(bar);
      }
    }
    if (!bar || !counts) return;
    const chip = (label, val, color) =>
      `<div style="background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:12px 16px;min-width:120px">
        <div style="font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em">${label}</div>
        <div style="font-size:22px;font-weight:700;color:${color || 'var(--text)'}">${val ?? '—'}</div>
      </div>`;
    bar.innerHTML =
      chip('OSI templates', counts.osi, '#00d4aa') +
      chip('OSI configured', counts.configured_osi, '#22c55e') +
      chip('Palmetto templates', counts.palmetto, '#8b5cf6') +
      chip('Palmetto configured', counts.configured_palmetto, '#a78bfa') +
      chip('Doc rules', counts.rules, '#f59e0b');
  },

  renderDocRules(rules) {
    const el = document.getElementById('paperworkDocRules');
    if (!el) return;
    if (!rules || !Object.keys(rules).length) {
      el.innerHTML = '<span style="color:var(--muted)">No document rules defined.</span>';
      return;
    }
    const rows = Object.entries(rules).map(([key, meta]) => {
      const rule = (meta && meta.rule) || 'static';
      const label = (meta && meta.label) || key;
      return `<tr>
        <td style="font-family:monospace;font-size:12px">${this._esc(key)}</td>
        <td>${this._esc(label)}</td>
        <td><span class="badge ${this.getBadgeClass(rule)}">${this._esc(rule)}</span></td>
      </tr>`;
    }).join('');
    // Keep element id stable for re-loads (pre or div)
    el.innerHTML = '';
    if (el.tagName === 'PRE') {
      // Upgrade pre → table container on first successful load
      const wrap = document.createElement('div');
      wrap.id = 'paperworkDocRules';
      wrap.className = 'table-responsive';
      wrap.innerHTML = `<table class="data-table" style="width:100%">
        <thead><tr><th>Key</th><th>Label</th><th>Rule</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <p style="font-size:11px;color:var(--muted);margin-top:10px">
        <strong>Rules:</strong> static = once per packet · shared = one copy · per-indemnitor / per-person / per-charge = multiply · print-only = never e-sign
      </p>`;
      el.replaceWith(wrap);
    } else {
      el.innerHTML = `<table class="data-table" style="width:100%">
        <thead><tr><th>Key</th><th>Label</th><th>Rule</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <p style="font-size:11px;color:var(--muted);margin-top:10px">
        <strong>Rules:</strong> static = once per packet · shared = one copy · per-indemnitor / per-person / per-charge = multiply · print-only = never e-sign
      </p>`;
    }
  },

  renderTable(tableId, templates) {
    const tbody = document.querySelector(`#${tableId} tbody`);
    if (!tbody) return;
    tbody.innerHTML = '';

    if (!templates || !Object.keys(templates).length) {
      tbody.innerHTML = `<tr><td colspan="4" style="color:var(--muted);text-align:center;padding:20px">No templates found</td></tr>`;
      return;
    }

    const entries = Object.entries(templates)
      .map(([key, tpl]) => ({ key, ...(typeof tpl === 'object' ? tpl : { template_id: tpl }) }))
      .sort((a, b) => a.key.localeCompare(b.key));

    entries.forEach(t => {
      const tid = t.template_id || '';
      const configured = t.configured !== false && tid && tid !== '(uses shared)';
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong style="font-family:monospace;font-size:12px">${this._esc(t.key)}</strong></td>
        <td>${this._esc(t.label || t.name || 'N/A')}</td>
        <td style="font-family:monospace;font-size:11px;word-break:break-all;color:${configured ? 'var(--text)' : 'var(--muted)'}">${this._esc(tid || '— not set —')}</td>
        <td><span class="badge ${this.getBadgeClass(t.rule)}">${this._esc(t.rule || 'static')}</span>
          ${configured ? '' : '<span style="margin-left:6px;font-size:10px;color:var(--warning)">needs ID</span>'}
        </td>`;
      tbody.appendChild(tr);
    });
  },

  getBadgeClass(rule) {
    switch (rule) {
      case 'per-indemnitor': return 'bg-blue';
      case 'per-charge': return 'bg-orange';
      case 'per-person': return 'bg-purple';
      case 'shared': return 'bg-green';
      case 'print-only': return 'bg-gray';
      default: return '';
    }
  },

  _esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  },
};

window.SLPaperwork = SLPaperwork;
