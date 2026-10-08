/**
 * Super CRM — Add surety.
 * Staff upload PDFs, confirm canonical mappings, preview a local sample
 * packet, and publish an immutable version. No client contact.
 */
(function () {
  const API = '/api/crm/sureties/onboarding';
  const state = {
    catalog: [],
    required: [],
    sureties: [],
    draft: null,
  };

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  async function api(path, options) {
    const res = await fetch(API + path, Object.assign({ credentials: 'same-origin' }, options || {}));
    const type = res.headers.get('content-type') || '';
    if (type.indexOf('application/pdf') !== -1) {
      if (!res.ok) throw new Error('Preview failed (' + res.status + ')');
      return res.blob();
    }
    const body = await res.json().catch(function () { return {}; });
    if (!res.ok || body.success === false) {
      const missing = (body.missing || []).join(', ');
      const msg = body.message || body.error || ('Request failed (' + res.status + ')');
      const err = new Error(missing ? (msg + ': ' + missing) : msg);
      err.payload = body;
      throw err;
    }
    return body;
  }

  function fieldOptions(selected) {
    const opts = ['<option value="">— unmapped —</option>'];
    state.catalog.forEach(function (field) {
      const sel = field.id === selected ? ' selected' : '';
      opts.push('<option value="' + esc(field.id) + '"' + sel + '>' + esc(field.label) + '</option>');
    });
    return opts.join('');
  }

  function render() {
    const root = document.getElementById('suretyOnboardingRoot');
    if (!root) return;
    const rows = state.sureties.map(function (row) {
      const version = row.published_version ? ('v' + row.published_version) : 'none';
      return '<tr><td>' + esc(row.label) + '</td><td><code>' + esc(row.surety_id) + '</code></td><td>' +
        esc(version) + '</td><td>' + esc(row.value_profile || '') + '</td><td>' +
        esc(row.docuseal_template_id || 'env / unset') + '</td></tr>';
    }).join('');
    root.innerHTML =
      '<h3>Add surety</h3>' +
      '<p style="font-size:12px;color:#94a3b8;margin:0 0 10px 0;">Upload one or more carrier PDFs, confirm mappings to the bond schema, preview with sample data, then publish. Write Bond uses the active published version. OSI and Palmetto keep their current DocuSeal env template ids.</p>' +
      '<div id="suretyOnboardingStatus" style="font-size:12px;margin-bottom:8px;color:#fbbf24;"></div>' +
      '<table class="data-table"><thead><tr><th>Surety</th><th>Id</th><th>Published</th><th>Profile</th><th>DocuSeal id</th></tr></thead><tbody>' +
      (rows || '<tr><td colspan="5">No sureties loaded.</td></tr>') +
      '</tbody></table>' +
      '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:8px;margin-top:14px;">' +
      '<label>Surety id<input id="soSuretyId" placeholder="lexington" style="width:100%"></label>' +
      '<label>Label<input id="soLabel" placeholder="Lexington National" style="width:100%"></label>' +
      '<label>DocuSeal template id (optional)<input id="soDocuSealId" placeholder="leave blank to keep env" style="width:100%"></label>' +
      '<label>POA prefixes (PREFIX max, one per line)<textarea id="soPrefixes" rows="3" placeholder="LN5 5000" style="width:100%"></textarea></label>' +
      '</div>' +
      '<label style="display:block;margin:8px 0;font-size:13px;"><input type="checkbox" id="soRepeat" checked> Repeat a page once per charge (POA, amount, charge)</label>' +
      '<button type="button" class="btn btn-primary" id="soCreate">Start draft</button> ' +
      '<div id="soDraft"></div>';
    document.getElementById('soCreate').onclick = createDraft;
    if (state.draft) renderDraft();
  }

  function setStatus(text, ok) {
    const el = document.getElementById('suretyOnboardingStatus');
    if (!el) return;
    el.style.color = ok ? '#4ade80' : '#fbbf24';
    el.textContent = text || '';
  }

  function parsePrefixes(text) {
    return String(text || '').split('\n').map(function (line) {
      const parts = line.trim().split(/\s+/);
      if (!parts[0]) return null;
      return { prefix: parts[0], max_bond_amount: Number(parts[1] || 0) };
    }).filter(Boolean);
  }

  async function createDraft() {
    try {
      const body = await api('/drafts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          surety_id: document.getElementById('soSuretyId').value.trim(),
          label: document.getElementById('soLabel').value.trim(),
          docuseal_template_id: document.getElementById('soDocuSealId').value.trim(),
          poa_prefixes: parsePrefixes(document.getElementById('soPrefixes').value),
          repeat_per_charge: document.getElementById('soRepeat').checked,
        }),
      });
      state.draft = body.version;
      setStatus('Draft started. Upload PDFs next.', true);
      renderDraft();
    } catch (err) {
      setStatus(err.message, false);
    }
  }

  function renderDraft() {
    const host = document.getElementById('soDraft');
    const draft = state.draft;
    if (!host || !draft) return;
    const forms = (draft.forms || []).map(function (form, formIndex) {
      const fields = (form.fields || []).map(function (field, fieldIndex) {
        const selected = field.canonical || field.suggestion || '';
        return '<tr><td><code>' + esc(field.name) + '</code></td><td>' + esc(field.type) +
          '</td><td><select data-form="' + formIndex + '" data-field="' + fieldIndex + '">' +
          fieldOptions(selected) + '</select></td><td>' + esc(field.suggestion || '') + '</td></tr>';
      }).join('');
      const placed = form.kind === 'flat'
        ? '<p style="font-size:12px;">No fillable fields detected. Place a box: page, x0, y0, x1, y1, and a canonical field.</p>' +
          '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:8px;">' +
          '<input data-place="page" placeholder="page" size="4" value="0">' +
          '<input data-place="x0" placeholder="x0" size="5"><input data-place="y0" placeholder="y0" size="5">' +
          '<input data-place="x1" placeholder="x1" size="5"><input data-place="y1" placeholder="y1" size="5">' +
          '<select data-place="canonical">' + fieldOptions('defendant.full_name') + '</select>' +
          '<button type="button" class="btn btn-secondary btn-sm" data-add-place="' + formIndex + '">Place field</button></div>'
        : '';
      const marks = '<div style="display:flex;gap:6px;flex-wrap:wrap;margin:8px 0;">' +
        '<select data-mark-role="' + formIndex + '"><option>indemnitor</option><option>co_indemnitor</option><option>defendant</option><option>bondsman</option></select>' +
        '<input data-mark="page" data-form="' + formIndex + '" value="0" size="3">' +
        '<input data-mark="x0" data-form="' + formIndex + '" placeholder="x0" size="4">' +
        '<input data-mark="y0" data-form="' + formIndex + '" placeholder="y0" size="4">' +
        '<input data-mark="x1" data-form="' + formIndex + '" placeholder="x1" size="4">' +
        '<input data-mark="y1" data-form="' + formIndex + '" placeholder="y1" size="4">' +
        '<button type="button" class="btn btn-secondary btn-sm" data-add-mark="' + formIndex + '">Add signature box</button></div>';
      return '<div class="glass-panel" style="margin-top:12px;padding:10px;">' +
        '<strong>' + esc(form.filename) + '</strong> <span style="color:#94a3b8;">' + esc(form.kind) + ' · ' + esc(form.role) + '</span>' +
        placed +
        '<table class="data-table"><thead><tr><th>PDF field</th><th>Type</th><th>Canonical</th><th>Suggestion</th></tr></thead><tbody>' +
        (fields || '<tr><td colspan="4">No AcroForm text fields. Place fields manually.</td></tr>') +
        '</tbody></table>' + marks + '</div>';
    }).join('');
    const check = draft.publish_check || {};
    host.innerHTML =
      '<h4 style="margin-top:16px;">Draft ' + esc(draft.surety_id) + '</h4>' +
      '<p style="font-size:12px;">Required before publish: ' + esc((state.required || []).join(', ')) + '</p>' +
      '<p style="font-size:12px;color:' + (check.ok ? '#4ade80' : '#fbbf24') + ';">' +
      (check.ok ? 'Required fields are mapped.' : ('Unmapped: ' + esc((check.missing || []).join(', ') || 'upload a PDF'))) +
      (check.warnings && check.warnings.length ? (' · warnings: ' + esc(check.warnings.join(', '))) : '') +
      '</p>' +
      '<input type="file" id="soPdf" accept="application/pdf" multiple> ' +
      '<button type="button" class="btn btn-secondary" id="soUpload">Upload PDFs</button> ' +
      '<button type="button" class="btn btn-secondary" id="soSave">Save mappings</button> ' +
      '<button type="button" class="btn btn-secondary" id="soPreview">Preview sample</button> ' +
      '<button type="button" class="btn btn-primary" id="soPublish">Publish</button>' +
      forms;
    document.getElementById('soUpload').onclick = uploadPdfs;
    document.getElementById('soSave').onclick = saveMappings;
    document.getElementById('soPreview').onclick = previewSample;
    document.getElementById('soPublish').onclick = publishDraft;
    host.querySelectorAll('[data-add-place]').forEach(function (btn) {
      btn.onclick = function () { addPlaced(Number(btn.getAttribute('data-add-place'))); };
    });
    host.querySelectorAll('[data-add-mark]').forEach(function (btn) {
      btn.onclick = function () { addMark(Number(btn.getAttribute('data-add-mark'))); };
    });
  }

  function collectForms() {
    const draft = state.draft;
    return (draft.forms || []).map(function (form, formIndex) {
      const fields = (form.fields || []).map(function (field, fieldIndex) {
        const sel = document.querySelector('select[data-form="' + formIndex + '"][data-field="' + fieldIndex + '"]');
        return { name: field.name, canonical: sel ? sel.value : (field.canonical || '') };
      });
      return {
        form_id: form.form_id,
        fields: fields,
        placed_fields: form.placed_fields || [],
        signatures: form.signatures || [],
        dates: form.dates || [],
        role: form.role,
      };
    });
  }

  async function uploadPdfs() {
    const input = document.getElementById('soPdf');
    const files = input && input.files ? Array.from(input.files) : [];
    if (!files.length || !state.draft) {
      setStatus('Choose a PDF first.', false);
      return;
    }
    try {
      for (const file of files) {
        const body = new FormData();
        body.append('file', file, file.name);
        const res = await api('/drafts/' + state.draft.version_id + '/forms', { method: 'POST', body: body });
        state.draft = res.version;
      }
      setStatus('Uploaded. Confirm the suggested mappings, then save.', true);
      render();
    } catch (err) {
      setStatus(err.message, false);
    }
  }

  async function saveMappings() {
    if (!state.draft) return;
    try {
      const res = await api('/drafts/' + state.draft.version_id, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ forms: collectForms() }),
      });
      state.draft = res.version;
      render();
      setStatus('Mappings saved.', true);
    } catch (err) {
      setStatus(err.message, false);
    }
  }

  function addPlaced(formIndex) {
    const form = state.draft.forms[formIndex];
    const panel = document.querySelector('[data-add-place="' + formIndex + '"]').parentNode;
    const page = Number(panel.querySelector('[data-place="page"]').value || 0);
    const canonical = panel.querySelector('[data-place="canonical"]').value;
    const rect = ['x0', 'y0', 'x1', 'y1'].map(function (key) {
      return Number(panel.querySelector('[data-place="' + key + '"]').value);
    });
    form.placed_fields = form.placed_fields || [];
    form.placed_fields.push({
      name: 'placed_' + (form.placed_fields.length + 1),
      page: page,
      rect: rect,
      canonical: canonical,
    });
    setStatus('Placed field added locally. Save mappings to keep it.', true);
  }

  function addMark(formIndex) {
    const form = state.draft.forms[formIndex];
    const role = document.querySelector('[data-mark-role="' + formIndex + '"]').value;
    const nums = {};
    document.querySelectorAll('[data-mark][data-form="' + formIndex + '"]').forEach(function (input) {
      nums[input.getAttribute('data-mark')] = Number(input.value);
    });
    form.signatures = form.signatures || [];
    form.signatures.push({
      role: role,
      kind: 'signature',
      page: nums.page || 0,
      rect: [nums.x0, nums.y0, nums.x1, nums.y1],
    });
    setStatus('Signature box added locally. Save mappings to keep it.', true);
  }

  async function previewSample() {
    if (!state.draft) return;
    try {
      await saveMappings();
      const blob = await api('/drafts/' + state.draft.version_id + '/preview', { method: 'POST' });
      const url = URL.createObjectURL(blob);
      window.open(url, '_blank', 'noopener');
      setStatus('Opened a local sample preview. It uses fake data only.', true);
    } catch (err) {
      setStatus(err.message, false);
    }
  }

  async function publishDraft() {
    if (!state.draft) return;
    try {
      await saveMappings();
      const res = await api('/drafts/' + state.draft.version_id + '/publish', { method: 'POST' });
      state.draft = null;
      setStatus('Published ' + res.version.surety_id + ' v' + res.version.version + '.', true);
      await refresh();
    } catch (err) {
      setStatus(err.message, false);
    }
  }

  async function refresh() {
    const catalog = await api('/catalog');
    const list = await api('');
    state.catalog = catalog.fields || [];
    state.required = catalog.required || [];
    state.sureties = list.sureties || [];
    render();
  }

  async function init() {
    try {
      await refresh();
    } catch (err) {
      const root = document.getElementById('suretyOnboardingRoot');
      if (root) {
        root.innerHTML = '<h3>Add surety</h3><p style="color:#fbbf24;">' + esc(err.message) + '</p>';
      }
    }
  }

  window.SLSuretyOnboarding = { init: init };
})();
