/**
 * ShamrockLeads — Role-Based Access Control (RBAC)
 * =================================================
 * Loads GET /api/session/me and enforces sub-agent vs God-Admin visibility.
 *
 * window.SL_RBAC = {
 *   role, email, agentName, licenseNumber,
 *   isGodAdmin, isSubAgent, loaded,
 *   canAccessApi, loadSession, filterBondsClientSide
 * }
 *
 * Sub-agents are HIDDEN from: Accounting, Revenue (agency), Scraper Health,
 * OSINT, ALPR, Settings/Hygiene, Reports, Social, FTA, Intelligence suite, etc.
 * Sub-agents KEEP: Bond Desk, Active Bonds (own), Calendar, POA (own), iMessage.
 */
(function () {
  'use strict';

  const RBAC = {
    role: 'god_admin',
    email: '',
    agentName: '',
    licenseNumber: '',
    isGodAdmin: true,
    isSubAgent: false,
    blockedTabs: [],
    blockedApiPrefixes: [],
    loaded: false,
  };

  /**
   * Actual data-tab values from index.html sidebar (must match exactly).
   * Plus a few non-tab buttons identified by selectors.
   */
  const SUB_AGENT_HIDDEN_DATA_TABS = [
    'tabAnalytics',       // agency Revenue
    'tabAccounting',
    'tabFTA',
    'tabAutomations',
    'tabPaperwork',
    'tabReports',
    'tabPortal',
    'tabMultiState',
    'tabBondIntel',
    'tabHealth',          // Scraper Health
    'tabAdminHygiene',
    'tabIntelligence',
    'tabOSINT',
    'tabALPR',
    'tabEnrichment',
    'tabAlphaIntel',
    'tabLegalNLP',
    'tabSocial',
  ];

  // Extra selectors for buttons without data-tab (Social ↗, etc.)
  const SUB_AGENT_HIDDEN_SELECTORS = [
    'button.sidebar-btn[title*="Social Command Center"]',
    'button.sidebar-btn[title*="Postiz"]',
    '.god-admin-only',
  ];

  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
  }

  async function loadSession() {
    try {
      const resp = await fetch('/api/session/me', { credentials: 'same-origin' });
      if (!resp.ok) {
        console.warn('[RBAC] Session fetch failed:', resp.status);
        return;
      }
      const data = await resp.json();
      if (!data.success) return;

      RBAC.role = data.role || 'god_admin';
      RBAC.email = data.email || '';
      RBAC.agentName = data.agent_name || '';
      RBAC.licenseNumber = data.license_number || '';
      RBAC.isGodAdmin = data.is_god_admin === true || data.role === 'god_admin' || data.role === 'admin';
      RBAC.isSubAgent = data.is_sub_agent === true || data.role === 'sub_agent';
      RBAC.blockedTabs = data.blocked_tabs || [];
      RBAC.blockedApiPrefixes = data.blocked_api_prefixes || [];
      RBAC.loaded = true;

      console.log(
        `[RBAC] ${RBAC.role}` +
          (RBAC.isGodAdmin ? ' (God-Admin)' : ` — ${RBAC.agentName} / ${RBAC.licenseNumber}`)
      );

      updateIdentityBadge();
      if (RBAC.isSubAgent) {
        enforceSubAgentRestrictions();
      } else {
        // God-admin: show Staff tab
        document.querySelectorAll('.god-admin-only').forEach((el) => {
          el.style.display = '';
        });
      }
    } catch (err) {
      console.error('[RBAC] Session load error:', err);
    }
  }

  function enforceSubAgentRestrictions() {
    SUB_AGENT_HIDDEN_DATA_TABS.forEach((tabVal) => {
      document.querySelectorAll(`[data-tab="${tabVal}"]`).forEach((navItem) => {
        navItem.style.display = 'none';
        navItem.setAttribute('data-rbac-hidden', '1');
      });
      const panel = document.getElementById(tabVal);
      if (panel) {
        panel.style.display = 'none';
        panel.setAttribute('data-rbac-hidden', '1');
      }
    });

    SUB_AGENT_HIDDEN_SELECTORS.forEach((sel) => {
      document.querySelectorAll(sel).forEach((el) => {
        el.style.display = 'none';
        el.setAttribute('data-rbac-hidden', '1');
      });
    });

    document.querySelectorAll('.god-admin-only').forEach((el) => {
      el.style.display = 'none';
    });
    document.querySelectorAll('.sub-agent-only').forEach((el) => {
      el.style.display = '';
    });

    // Identity banner
    const mainContent = document.querySelector('.main-content') || document.querySelector('main');
    if (mainContent && !document.getElementById('subAgentBanner')) {
      const banner = document.createElement('div');
      banner.id = 'subAgentBanner';
      banner.style.cssText =
        'background:linear-gradient(135deg,rgba(0,210,106,0.12),rgba(0,184,92,0.04));' +
        'border:1px solid rgba(0,210,106,0.25);border-radius:12px;padding:10px 18px;' +
        'margin:12px 16px 0;display:flex;align-items:center;gap:12px;font-size:13px;';
      banner.innerHTML =
        `<span style="font-size:18px">🏷️</span>` +
        `<div><strong style="color:#00d26a">${escapeHtml(RBAC.agentName)}</strong>` +
        `<span style="color:#8899aa;margin-left:8px">License ${escapeHtml(RBAC.licenseNumber)}</span>` +
        `<span style="color:#667788;margin-left:8px;font-size:11px">· Sub-Agent (scoped access)</span></div>`;
      mainContent.insertBefore(banner, mainContent.firstChild);
    }

    console.log('[RBAC] Sub-agent UI restrictions applied');
  }

  function updateIdentityBadge() {
    const badge = document.getElementById('userIdentityBadge');
    if (!badge) return;
    if (RBAC.isGodAdmin) {
      badge.innerHTML = '👑 <span style="color:#ffd700">God-Admin</span>';
    } else {
      badge.innerHTML = `🏷️ <span style="color:#00d26a">${escapeHtml(RBAC.agentName || 'Sub-Agent')}</span>`;
    }
  }

  function canAccessApi(path) {
    if (RBAC.isGodAdmin) return true;
    const p = String(path || '');
    return !(RBAC.blockedApiPrefixes || []).some(
      (prefix) => p === prefix || p.startsWith(prefix + '/') || p.startsWith(prefix)
    );
  }

  /** Client-side belt-and-suspenders filter for bond arrays already fetched. */
  function filterBondsClientSide(bonds) {
    if (!RBAC.isSubAgent || !Array.isArray(bonds)) return bonds || [];
    const lic = (RBAC.licenseNumber || '').toUpperCase();
    const name = (RBAC.agentName || '').toLowerCase();
    return bonds.filter((b) => {
      const fields = [
        b.agent_license,
        b.writing_agent,
        b.writing_agent_license,
        b.license_number,
        b.agent_name,
        b.writing_agent_name,
      ]
        .filter(Boolean)
        .map((x) => String(x));
      if (lic && fields.some((f) => f.toUpperCase() === lic)) return true;
      if (name && fields.some((f) => f.toLowerCase() === name)) return true;
      return false;
    });
  }

  function isGodAdmin() {
    return !!RBAC.isGodAdmin;
  }

  window.SL_RBAC = RBAC;
  window.SL_RBAC.loadSession = loadSession;
  window.SL_RBAC.canAccessApi = canAccessApi;
  window.SL_RBAC.isGodAdmin = isGodAdmin;
  window.SL_RBAC.filterBondsClientSide = filterBondsClientSide;
  window.SL_RBAC.enforceSubAgentRestrictions = enforceSubAgentRestrictions;

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadSession);
  } else {
    loadSession();
  }
})();
