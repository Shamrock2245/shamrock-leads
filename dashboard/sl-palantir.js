/*
 * Shamrock Palantir Command HUD Controller
 * Read-only intelligence interactions backed by existing CRM endpoints.
 * No bond, paperwork, signature, payment, outreach, or record mutation behavior exists here.
 */
(function () {
  'use strict';

  const API = window.API || '';
  const ADMIN_KEY = window.OSINT_ADMIN_KEY || '';
  const $ = id => document.getElementById(id);
  const toast = (message, type) => { if (window.SL?.toast) SL.toast(message, type); };
  const headers = () => ({
    'Content-Type': 'application/json',
    ...(ADMIN_KEY ? { 'X-Admin-Key': ADMIN_KEY } : {}),
  });

  let _activeSubtab = 'graph';
  let _currentGraph = null;
  let _selectedNodeId = null;
  let _leafletMap = null;
  let _mapReady = false;
  let _mapMarkers = new Map();
  let _osirisFeeds = [];

  window.SLPalantir = {
    init,
    switchSubtab,
    resolveGraph,
    toggleLayer,
    selectNode,
    refreshOsiris,
    focusOsirisFeed,
    runBreachLookup,
    generateDossierPrompt,
  };

  function _esc(value) {
    if (value === null || value === undefined) return '';
    const node = document.createElement('div');
    node.textContent = String(value);
    return node.innerHTML;
  }

  function _typeClass(type) {
    const allowed = new Set(['company', 'property', 'indemnitor', 'phone', 'email', 'bond', 'relative', 'note', 'defendant']);
    return allowed.has(type) ? `type-${type}` : 'type-record';
  }

  function _statePanel({ kicker, title, copy, state = '' }) {
    return `
      <div class="palantir-state-panel ${state ? `is-${state}` : ''}">
        <div>
          <span class="palantir-state-kicker">${_esc(kicker)}</span>
          <h4 class="palantir-state-title">${_esc(title)}</h4>
          <p class="palantir-state-copy">${_esc(copy)}</p>
        </div>
      </div>`;
  }

  function _setInspectorPrompt() {
    const inspector = $('palantirNodeInspector');
    if (!inspector) return;
    inspector.innerHTML = `
      <div class="palantir-inspector-orb">STANDBY</div>
      <p class="palantir-inspector-hint">Resolve a subject, then select a reactor node to inspect its CRM-backed provenance and relationship confidence.</p>`;
  }

  function init() {
    switchSubtab(_activeSubtab, { skipDossierPrompt: true });
    const input = $('palantirSubjectInput');
    if (input && !input.dataset.palantirBound) {
      input.addEventListener('keydown', event => {
        if (event.key === 'Enter') resolveGraph();
      });
      input.dataset.palantirBound = 'true';
    }

    if (!_currentGraph) {
      const canvas = $('palantirGraphCanvas');
      if (canvas) {
        canvas.innerHTML = `<div class="palantir-reactor-empty">${_statePanel({
          kicker: 'Reactor // standby',
          title: 'Acquire one verified CRM target',
          copy: 'Enter an exact subject reference to render its recorded relationships as an interactive reactor.',
        })}</div>`;
      }
      _setInspectorPrompt();
    }
  }

  function switchSubtab(subtab, options = {}) {
    _activeSubtab = subtab;
    document.querySelectorAll('.palantir-subtab-btn').forEach(button => {
      const active = button.dataset.subtab === subtab;
      button.classList.toggle('active', active);
      button.setAttribute('aria-pressed', String(active));
    });

    ['graph', 'osiris', 'spectra', 'dossier'].forEach(name => {
      const view = $('palantirView' + name.charAt(0).toUpperCase() + name.slice(1));
      if (!view) return;
      const active = name === subtab;
      view.classList.toggle('active', active);
      view.style.display = active ? 'block' : 'none';
      view.setAttribute('aria-hidden', String(!active));
    });

    if (subtab === 'osiris') {
      _initOsirisMap();
      refreshOsiris();
    }
    if (subtab === 'dossier' && !_currentGraph && !options.skipDossierPrompt) {
      _renderDossierTargetPrompt();
    }
  }

  function _activeLayers() {
    return {
      company: $('palantirLayerLLC')?.checked !== false,
      property: $('palantirLayerProperty')?.checked !== false,
      relative: $('palantirLayerRelatives')?.checked !== false,
    };
  }

  function _visibleNodes() {
    if (!_currentGraph) return [];
    const layers = _activeLayers();
    return (_currentGraph.nodes || []).filter(node => {
      if (node.type === 'company') return layers.company;
      if (node.type === 'property') return layers.property;
      if (node.type === 'relative') return layers.relative;
      return true;
    });
  }

  async function resolveGraph() {
    const input = ($('palantirSubjectInput')?.value || '').trim();
    const subjectType = $('palantirSubjectType')?.value || 'defendant';
    const canvas = $('palantirGraphCanvas');
    const stats = $('palantirGraphStats');

    if (!input) {
      if (canvas) canvas.innerHTML = `<div class="palantir-reactor-empty">${_statePanel({
        kicker: 'Reactor // target required',
        title: 'A subject reference is required',
        copy: 'Enter an exact name, booking number, or CRM record ID. The reactor cannot create an identity from partial data.',
      })}</div>`;
      _setInspectorPrompt();
      toast('Enter an exact subject reference before initializing the reactor', 'info');
      return;
    }

    if (canvas) canvas.innerHTML = `<div class="palantir-reactor-empty">${_statePanel({
      kicker: 'Reactor // resolving live CRM',
      title: 'Mapping recorded relationships',
      copy: 'Retrieving CRM-backed nodes and edges. Unverified or unavailable records will not be invented.',
      state: 'loading',
    })}</div>`;
    if (stats) stats.textContent = 'RESOLVING';

    try {
      const response = await fetch(`${API}/api/palantir/graph/${encodeURIComponent(input)}?subject_type=${encodeURIComponent(subjectType)}`, {
        headers: headers(),
        credentials: 'same-origin',
      });
      if (!response.ok) {
        if (canvas) canvas.innerHTML = `<div class="palantir-reactor-empty">${_statePanel({
          kicker: 'Reactor // request unavailable',
          title: 'CRM relationship map unavailable',
          copy: `The request returned status ${response.status}. No relationship has been inferred.`,
          state: 'error',
        })}</div>`;
        if (stats) stats.textContent = `HTTP ${response.status}`;
        _setInspectorPrompt();
        return;
      }
      _currentGraph = await response.json();
      _selectedNodeId = (_currentGraph.nodes || []).find(node => String(node.id).startsWith('subj_'))?.id || _currentGraph.nodes?.[0]?.id || null;
      _renderGraph();
    } catch (error) {
      if (canvas) canvas.innerHTML = `<div class="palantir-reactor-empty">${_statePanel({
        kicker: 'Reactor // network interruption',
        title: 'CRM relationship map unreachable',
        copy: `Network error: ${error.message}`,
        state: 'error',
      })}</div>`;
      if (stats) stats.textContent = 'OFFLINE';
      _setInspectorPrompt();
    }
  }

  function toggleLayer(layer) {
    if (!_currentGraph) {
      toast(`The ${layer} layer will apply when a CRM target is resolved`, 'info');
      return;
    }
    _renderGraph();
  }

  function _layoutNodes(nodes) {
    const root = nodes.find(node => String(node.id).startsWith('subj_')) || nodes[0];
    const others = nodes.filter(node => node.id !== root?.id);
    const positions = new Map();
    if (root) positions.set(root.id, { x: 50, y: 50, root: true });
    const count = others.length;
    others.forEach((node, index) => {
      const angle = (-Math.PI / 2) + ((Math.PI * 2 * index) / Math.max(count, 1));
      const radiusX = count > 5 ? 39 : 34;
      const radiusY = count > 5 ? 37 : 33;
      positions.set(node.id, {
        x: 50 + Math.cos(angle) * radiusX,
        y: 50 + Math.sin(angle) * radiusY,
        root: false,
      });
    });
    return { root, positions };
  }

  function _renderGraph() {
    const canvas = $('palantirGraphCanvas');
    const stats = $('palantirGraphStats');
    if (!canvas || !_currentGraph) return;

    const allNodes = _currentGraph.nodes || [];
    const nodes = _visibleNodes();
    const edges = (_currentGraph.edges || []).filter(edge => nodes.some(node => node.id === edge.source) && nodes.some(node => node.id === edge.target));
    const warnings = _currentGraph.warnings || [];
    const mode = _currentGraph.data_mode || (_currentGraph.subject_found ? 'live' : 'empty');

    if (stats) {
      const label = mode === 'live' ? 'LIVE CRM' : mode === 'empty' ? 'NO MATCH' : String(mode).toUpperCase();
      stats.textContent = `${nodes.length} NODES // ${edges.length} LINKS // ${label}`;
    }

    if (!nodes.length) {
      const copy = allNodes.length
        ? 'All available records are currently hidden by the selected signal-layer filters.'
        : 'No matching CRM record was found. Enter an exact subject reference; the reactor will not infer an identity or relationship.';
      canvas.innerHTML = `<div class="palantir-reactor-empty">${_statePanel({
        kicker: allNodes.length ? 'Reactor // layers filtered' : 'Reactor // no verified target',
        title: allNodes.length ? 'No visible signals in current layers' : 'No recorded CRM relationship map',
        copy,
      })}</div>`;
      _setInspectorPrompt();
      return;
    }

    const { root, positions } = _layoutNodes(nodes);
    if (!positions.has(_selectedNodeId)) _selectedNodeId = root?.id || nodes[0].id;
    const selected = nodes.find(node => node.id === _selectedNodeId) || root || nodes[0];
    const warningMarkup = warnings.length
      ? `<div class="palantir-graph-warning">${warnings.map(warning => _esc(warning)).join('<br>')}</div>`
      : '';

    const edgeMarkup = edges.map(edge => {
      const source = positions.get(edge.source);
      const target = positions.get(edge.target);
      if (!source || !target) return '';
      const selectedEdge = edge.source === selected.id || edge.target === selected.id;
      return `<line class="${selectedEdge ? 'is-selected' : ''}" x1="${source.x}%" y1="${source.y}%" x2="${target.x}%" y2="${target.y}%"></line>`;
    }).join('');

    const nodeMarkup = nodes.filter(node => node.id !== root?.id).map(node => {
      const position = positions.get(node.id);
      const selectedClass = node.id === selected.id ? 'is-selected' : '';
      return `
        <button type="button" class="palantir-orbit-node ${_typeClass(node.type)} ${selectedClass}" style="left:${position.x}%;top:${position.y}%" onclick="SLPalantir.selectNode('${_esc(node.id)}')" aria-pressed="${node.id === selected.id}">
          <span class="palantir-node-dot"></span>
          <span class="palantir-node-copy"><span class="palantir-node-name">${_esc(node.label)}</span><span class="palantir-node-type">${_esc(node.type)} // ${node.verified === false ? 'unverified' : 'verified'}</span></span>
        </button>`;
    }).join('');

    const coreLabel = root?.label || 'CRM SUBJECT';
    canvas.innerHTML = `
      ${warningMarkup}
      <svg class="palantir-reactor-svg" aria-hidden="true" viewBox="0 0 100 100" preserveAspectRatio="none">${edgeMarkup}</svg>
      <button type="button" class="palantir-reactor-core" onclick="SLPalantir.selectNode('${_esc(root?.id || selected.id)}')" aria-label="Inspect primary CRM subject">
        <span class="palantir-reactor-core-text">${_esc(coreLabel)}<small>${_esc(root?.type || 'subject')} // ${root?.verified === false ? 'unverified' : 'verified'}</small></span>
      </button>
      ${nodeMarkup}`;
    _renderInspector(selected, edges);
  }

  function selectNode(nodeId) {
    if (!_currentGraph) return;
    const visible = _visibleNodes();
    const node = visible.find(item => item.id === nodeId);
    if (!node) return;
    _selectedNodeId = node.id;
    _renderGraph();
  }

  function _renderInspector(node, edges) {
    const inspector = $('palantirNodeInspector');
    if (!inspector || !node) return;
    const connected = edges.filter(edge => edge.source === node.id || edge.target === node.id);
    const confidence = connected.length
      ? `${Math.round((connected.reduce((total, edge) => total + Number(edge.confidence || 0), 0) / connected.length) * 100)}%`
      : '—';
    const metadata = node.metadata || {};
    const allowedMeta = ['collection', 'booking_number', 'status', 'poa', 'surety', 'relationship', 'verified_deed'];
    const metaRows = allowedMeta.filter(key => metadata[key] !== undefined && metadata[key] !== '').map(key => `
      <div class="palantir-inspector-row"><span>${_esc(key.replaceAll('_', ' '))}</span><strong>${_esc(metadata[key])}</strong></div>`).join('');
    const verified = node.verified !== false;

    inspector.innerHTML = `
      <div class="palantir-inspector-orb">${_esc(String(node.type || 'record').slice(0, 7).toUpperCase())}</div>
      <h4 class="palantir-inspector-name">${_esc(node.label)}</h4>
      <p class="palantir-inspector-subtitle">${_esc(node.subtitle || 'CRM record without additional display detail.')}</p>
      <div class="palantir-inspector-rows">
        <div class="palantir-inspector-row"><span>Record type</span><strong>${_esc(node.type || 'record')}</strong></div>
        <div class="palantir-inspector-row"><span>Provenance</span><strong class="${verified ? 'is-verified' : 'is-unverified'}">${verified ? 'VERIFIED' : 'UNVERIFIED'} // ${_esc(node.source || 'unknown')}</strong></div>
        <div class="palantir-inspector-row"><span>Risk state</span><strong>${_esc(node.risk_level || 'low')}</strong></div>
        <div class="palantir-inspector-row"><span>Link confidence</span><strong>${confidence}</strong></div>
        <div class="palantir-inspector-row"><span>Connected signals</span><strong>${connected.length}</strong></div>
        ${metaRows}
      </div>`;
  }

  function _initOsirisMap() {
    const container = $('osirisMapContainer');
    if (!container || _mapReady) return;

    if (typeof L !== 'undefined') {
      container.innerHTML = '';
      try {
        _leafletMap = L.map('osirisMapContainer', { zoomControl: true }).setView([26.6406, -81.8723], 10);
        L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
          maxZoom: 19,
          subdomains: 'abcd',
          attribution: '© OpenStreetMap © CARTO',
        }).addTo(_leafletMap);
        _mapReady = true;
        setTimeout(() => _leafletMap?.invalidateSize(), 300);
      } catch (error) {
        console.warn('OSIRIS map unavailable:', error);
      }
      return;
    }

    container.innerHTML = `
      <div class="palantir-map-fallback">
        <div>
          <span class="palantir-state-kicker">OSIRIS // map unavailable</span>
          <h4 class="palantir-state-title">The map surface could not initialize</h4>
          <p class="palantir-state-copy">The active signal stream remains available. No location signal is fabricated while the map library is unavailable.</p>
        </div>
      </div>`;
    _mapReady = true;
  }

  async function refreshOsiris() {
    const list = $('osirisFeedList');
    const status = $('osirisFeedStatus');
    const county = ($('osirisCountyFilter')?.value || '').trim();
    if (!list) return;

    list.innerHTML = '<div class="palantir-empty"><div class="empty-icon">REFRESHING FIELD GRID</div><div class="empty-text">Loading current CRM-backed signals.</div></div>';
    if (status) status.textContent = 'Refreshing';

    try {
      const query = county ? `?county=${encodeURIComponent(county)}` : '';
      const response = await fetch(`${API}/api/palantir/situation-room/feeds${query}`, { headers: headers(), credentials: 'same-origin' });
      if (!response.ok) {
        list.innerHTML = `<div class="palantir-empty"><div class="empty-icon">FIELD GRID UNAVAILABLE</div><div class="empty-text">The request returned status ${response.status}. No field signal is inferred.</div></div>`;
        if (status) status.textContent = `HTTP ${response.status}`;
        return;
      }
      _osirisFeeds = await response.json();
      _renderOsirisFeeds();
      if (status) status.textContent = `${_osirisFeeds.length} Signals`;
    } catch (error) {
      list.innerHTML = '<div class="palantir-empty"><div class="empty-icon">NETWORK INTERRUPTION</div><div class="empty-text">The field grid could not be reached. No signal is inferred.</div></div>';
      if (status) status.textContent = 'Offline';
    }
  }

  function _renderOsirisFeeds() {
    const list = $('osirisFeedList');
    if (!list) return;

    if (_leafletMap && typeof L !== 'undefined') {
      _mapMarkers.forEach(marker => _leafletMap.removeLayer(marker));
      _mapMarkers = new Map();
    }

    if (!_osirisFeeds.length) {
      list.innerHTML = '<div class="palantir-empty"><div class="empty-icon">NO ACTIVE SIGNALS</div><div class="empty-text">No CRM-backed OSIRIS signals are active for the selected field grid.</div></div>';
      return;
    }

    list.innerHTML = _osirisFeeds.map(feed => {
      const severity = ['danger', 'warning'].includes(feed.severity) ? feed.severity : '';
      const badge = feed.severity === 'danger' ? 'HIGH' : feed.severity === 'warning' ? 'ALERT' : 'INFO';
      const reference = feed.demo ? ' // MAP REFERENCE' : ' // LIVE CRM';
      const markerColor = feed.severity === 'danger' ? '#ff5e7a' : feed.severity === 'warning' ? '#ffd166' : '#62e7ff';

      if (_leafletMap && typeof L !== 'undefined' && Number.isFinite(Number(feed.lat)) && Number.isFinite(Number(feed.lng))) {
        const marker = L.circleMarker([feed.lat, feed.lng], { radius: 8, fillColor: markerColor, color: '#ecfbff', weight: 2, opacity: 1, fillOpacity: .86 }).addTo(_leafletMap);
        marker.bindPopup(`<div class="palantir-map-popup-title">${_esc(feed.title)}</div><div class="palantir-map-popup-copy">${_esc(feed.description)}</div><div class="palantir-map-popup-source">${_esc(feed.source)}${reference}</div>`);
        _mapMarkers.set(feed.id, marker);
      }

      return `
        <button type="button" class="palantir-feed-card ${severity}" data-feed-id="${_esc(feed.id)}" onclick="SLPalantir.focusOsirisFeed('${_esc(feed.id)}')">
          <span class="palantir-feed-topline"><span class="palantir-feed-title">${_esc(feed.title)}</span><span class="palantir-feed-badge ${severity}">${badge}</span></span>
          <span class="palantir-feed-description">${_esc(feed.description)}</span>
          <span class="palantir-feed-source">${_esc(feed.source)}${reference}</span>
        </button>`;
    }).join('');
  }

  function focusOsirisFeed(feedId) {
    const feed = _osirisFeeds.find(item => item.id === feedId);
    if (!feed) return;
    document.querySelectorAll('#tabPalantir .palantir-feed-card').forEach(card => card.classList.toggle('is-focused', card.dataset.feedId === feedId));
    const marker = _mapMarkers.get(feedId);
    if (marker && _leafletMap) {
      _leafletMap.flyTo(marker.getLatLng(), Math.max(_leafletMap.getZoom(), 11), { duration: .65 });
      marker.openPopup();
    }
  }

  function _setSpectraStatus({ level = 'wait', title, detail }) {
    const target = $('spectraScanStatus');
    if (!target) return;
    const statusLabel = level === 'high' ? 'HIGH' : level === 'medium' ? 'MED' : level === 'low' ? 'LOW' : level === 'unavailable' ? 'GATED' : level === 'loading' ? 'SCAN' : 'WAIT';
    const riskClass = ['low', 'medium', 'high'].includes(level) ? `is-${level}` : '';
    target.innerHTML = `<div class="palantir-risk-dial ${riskClass}"><span>${statusLabel}</span></div><div class="palantir-scan-copy"><strong>${_esc(title)}</strong><small>${_esc(detail)}</small></div>`;
  }

  async function runBreachLookup() {
    const query = ($('spectraQueryInput')?.value || '').trim();
    const results = $('spectraBreachResult');
    if (!query) {
      toast('Enter an email address or username before running SPECTRA', 'info');
      return;
    }

    if (results) {
      results.style.display = 'block';
      results.innerHTML = '<div class="palantir-result-note is-unavailable">Contacting the configured SPECTRA provider. Results remain unavailable until the provider responds.</div>';
    }
    _setSpectraStatus({ level: 'loading', title: 'Live scan in progress', detail: 'Querying the configured provider; no result is assumed.' });

    try {
      const isEmail = query.includes('@');
      const isPhone = query.replace(/\D/g, '').length >= 10;
      const payload = { email: isEmail ? query : null, phone: isPhone ? query : null, username: (!isEmail && !isPhone) ? query : null };
      const response = await fetch(`${API}/api/palantir/spectra/breach-lookup`, { method: 'POST', headers: headers(), credentials: 'same-origin', body: JSON.stringify(payload) });
      if (!response.ok) {
        if (results) results.innerHTML = `<div class="palantir-result-note is-danger">The SPECTRA request returned status ${response.status}.</div>`;
        _setSpectraStatus({ level: 'unavailable', title: 'Provider request unavailable', detail: `The provider returned status ${response.status}.` });
        return;
      }

      const result = await response.json();
      const providerUnavailable = result.data_mode === 'unavailable';
      const riskLevel = result.found ? (result.risk_impact === 'high' ? 'high' : 'medium') : providerUnavailable ? 'unavailable' : 'low';
      _setSpectraStatus({ level: riskLevel, title: result.found ? `${result.total_breaches} signal${Number(result.total_breaches) === 1 ? '' : 's'} returned` : providerUnavailable ? 'Provider unavailable' : 'No signal returned', detail: result.message || 'No additional provider detail returned.' });

      if (!result.found || !(result.breaches || []).length) {
        if (results) results.innerHTML = `<div class="palantir-result-note ${providerUnavailable ? 'is-unavailable' : ''}">${_esc(result.message || 'No known infostealer records were returned by the configured source.')}</div>`;
        _renderGeotagEmpty();
        return;
      }

      if (results) {
        results.innerHTML = `
          <div class="palantir-result-note is-danger">The provider returned ${_esc(result.total_breaches)} infostealer signal${Number(result.total_breaches) === 1 ? '' : 's'}.</div>
          ${result.breaches.map(breach => `
            <article class="spectra-breach-card">
              <div class="spectra-breach-title"><span>${_esc(breach.breach_name)} (${_esc(breach.domain)})</span><span>${_esc(breach.breach_date)}</span></div>
              <div class="palantir-result-copy">${_esc(breach.description)}</div>
              <div class="palantir-breach-fields">Exposed fields: ${_esc((breach.compromised_data || []).join(', '))}</div>
            </article>`).join('')}`;
      }
      _renderGeotagEmpty();
    } catch (error) {
      if (results) results.innerHTML = `<div class="palantir-result-note is-danger">Network error: ${_esc(error.message)}</div>`;
      _setSpectraStatus({ level: 'unavailable', title: 'Provider network interruption', detail: 'No breach result was inferred after the network interruption.' });
    }
  }

  function _renderGeotagEmpty() {
    const cluster = $('spectraGeotagCluster');
    if (!cluster) return;
    cluster.innerHTML = '<div class="palantir-empty"><div class="empty-icon">NO VERIFIED GEOTAGS</div><div class="empty-text">The completed provider lookup did not return verified geotag data. SPECTRA does not infer locations.</div></div>';
  }

  function _renderDossierTargetPrompt() {
    const container = $('palantirDossierContent');
    if (!container) return;
    container.innerHTML = _statePanel({
      kicker: 'Intelligence brief // target required',
      title: 'Resolve a CRM subject first',
      copy: 'Select an exact defendant or indemnitor in the Entity Reactor before compiling a data-bounded intelligence brief.',
    });
  }

  async function generateDossierPrompt() {
    const input = ($('palantirSubjectInput')?.value || '').trim();
    const type = $('palantirSubjectType')?.value || 'defendant';
    const container = $('palantirDossierContent');
    switchSubtab('dossier', { skipDossierPrompt: true });

    if (!input) {
      _renderDossierTargetPrompt();
      toast('Resolve an exact CRM subject before compiling an intelligence brief', 'info');
      return;
    }

    if (container) container.innerHTML = _statePanel({
      kicker: 'Intelligence brief // compiling',
      title: 'Compiling CRM-bounded findings',
      copy: 'The brief will explicitly preserve any missing, unavailable, or unverified data state.',
      state: 'loading',
    });

    try {
      const response = await fetch(`${API}/api/palantir/dossier/generate`, {
        method: 'POST', headers: headers(), credentials: 'same-origin', body: JSON.stringify({ subject_id: input, subject_type: type }),
      });
      if (!response.ok) {
        if (container) container.innerHTML = _statePanel({ kicker: 'Intelligence brief // unavailable', title: 'CRM brief request failed', copy: `The request returned status ${response.status}. No finding was inferred.`, state: 'error' });
        return;
      }
      _renderDossier(await response.json());
    } catch (error) {
      if (container) container.innerHTML = _statePanel({ kicker: 'Intelligence brief // network interruption', title: 'CRM brief unreachable', copy: `Network error: ${error.message}`, state: 'error' });
    }
  }

  function _renderDossier(dossier) {
    const container = $('palantirDossierContent');
    if (!container || !dossier) return;
    const score = dossier.risk_score;
    const hasScore = score !== null && score !== undefined;
    const scoreClass = !hasScore ? 'is-unknown' : score > 70 ? 'is-high-risk' : '';
    const findings = dossier.key_findings || [];
    const warnings = dossier.warnings || [];
    const proximity = dossier.threat_proximity || [];
    const mode = dossier.data_mode || (dossier.subject_found ? 'live' : 'empty');

    container.innerHTML = `
      <article class="palantir-dossier-paper">
        <div class="palantir-dossier-topline">
          <div>
            <span class="palantir-eyebrow">Shamrock // CRM-bounded intelligence brief</span>
            <h3>Subject Intelligence Brief</h3>
            <div class="palantir-subject-line">Subject: ${_esc(dossier.subject_name)} (${_esc(dossier.subject_id)})</div>
            <div class="palantir-dossier-meta">BRIEF ${_esc(dossier.dossier_id)} // GENERATED ${_esc(dossier.generated_at)} // MODE ${_esc(mode)}</div>
          </div>
          <div class="palantir-score"><div class="palantir-score-value ${scoreClass}">${hasScore ? `${_esc(score)} / 100` : 'N/A'}</div><div class="palantir-score-label">${hasScore ? 'CRM linkage score' : 'Insufficient data'}</div></div>
        </div>
        <div class="palantir-dossier-summary"><strong>Executive summary:</strong> ${_esc(dossier.summary)}</div>
        ${warnings.length ? `<div class="palantir-dossier-warning">${warnings.map(warning => _esc(warning)).join('<br>')}</div>` : ''}
        <div class="palantir-dossier-grid">
          <section class="palantir-dossier-box"><div class="palantir-dossier-box-title is-green">Recorded findings</div><ul class="palantir-dossier-list">${findings.length ? findings.map(finding => `<li>${_esc(finding)}</li>`).join('') : '<li>No linked CRM findings yet.</li>'}</ul></section>
          <section class="palantir-dossier-box"><div class="palantir-dossier-box-title">Spatial proximity</div>${proximity.length ? proximity.map(item => `<div class="palantir-proximity">${_esc(item.name)}: <strong>${_esc(item.distance_miles)} miles</strong></div>`).join('') : '<div class="palantir-proximity-empty">No proximity intelligence is attached. Live field-grid signals remain separate.</div>'}</section>
        </div>
        <div class="palantir-recommendation">Underwriting recommendation: ${_esc(dossier.recommendation)}</div>
      </article>`;
  }
})();
