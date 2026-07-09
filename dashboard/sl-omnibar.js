/**
 * sl-omnibar.js
 * Twenty-CRM style global omnibar and activity feed slide-overs.
 */

window.SLActivityFeed = {
  toggle: function() {
    const drawer = document.getElementById('globalActivityDrawer');
    if (drawer.style.right === '0px') {
      drawer.style.right = '-350px';
    } else {
      drawer.style.display = 'flex';
      setTimeout(() => drawer.style.right = '0px', 10);
    }
  },
  
  addEvent: function(htmlStr) {
    const feed = document.getElementById('globalActivityFeed');
    if (!feed) return;
    const div = document.createElement('div');
    div.style.cssText = 'background:var(--bg-main,#0f172a); border:1px solid var(--border,#334155); border-radius:8px; padding:12px; font-size:0.9rem; color:var(--text,#e2e8f0); animation: slideInRight 0.3s ease forwards;';
    div.innerHTML = htmlStr;
    feed.prepend(div);
    if (feed.children.length > 50) {
      feed.removeChild(feed.lastChild);
    }
  }
};

window.SLOmnibar = {
  isOpen: false,
  results: [],
  selectedIndex: -1,
  
  init: function() {
    this.overlay = document.getElementById('omnibarOverlay');
    this.input = document.getElementById('omnibarInput');
    this.resultsDiv = document.getElementById('omnibarResults');
    
    if (!this.overlay) return;
    
    // Global Keyboard Listeners
    document.addEventListener('keydown', (e) => {
      // Cmd+K or Ctrl+K
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault();
        this.toggle();
      }
      
      // Escape
      if (e.key === 'Escape') {
        if (this.isOpen) this.close();
        if (typeof closeShamrockNotes === 'function') closeShamrockNotes();
        if (typeof closeEditDrawer === 'function') closeEditDrawer();
        const activityDrawer = document.getElementById('globalActivityDrawer');
        if (activityDrawer && activityDrawer.style.right === '0px') {
          SLActivityFeed.toggle();
        }
      }
      
      // Omnibar Navigation
      if (this.isOpen) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          this.selectedIndex = Math.min(this.selectedIndex + 1, this.results.length - 1);
          this.renderSelected();
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          this.selectedIndex = Math.max(this.selectedIndex - 1, 0);
          this.renderSelected();
        } else if (e.key === 'Enter' && this.selectedIndex >= 0) {
          e.preventDefault();
          this.executeResult(this.results[this.selectedIndex]);
        }
      }
    });
    
    // Quick New Intake (N key, if not in input)
    document.addEventListener('keydown', (e) => {
      if (e.key === 'n' && e.target.tagName !== 'INPUT' && e.target.tagName !== 'TEXTAREA') {
        e.preventDefault();
        if (typeof SLIntake !== 'undefined') {
          SL.switchTab(document.querySelector('[data-tab="tabIntake"]'));
        }
      }
    });

    this.overlay.addEventListener('click', (e) => {
      if (e.target === this.overlay) this.close();
    });
    
    let debounceTimer;
    this.input.addEventListener('input', (e) => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => this.search(e.target.value), 300);
    });
  },
  
  toggle: function() {
    this.isOpen ? this.close() : this.open();
  },
  
  open: function() {
    this.isOpen = true;
    this.overlay.style.display = 'flex';
    this.input.value = '';
    this.resultsDiv.innerHTML = '';
    this.results = [];
    this.selectedIndex = -1;
    setTimeout(() => this.input.focus(), 50);
  },
  
  close: function() {
    this.isOpen = false;
    this.overlay.style.display = 'none';
  },
  
  search: async function(query) {
    if (!query) {
      this.resultsDiv.innerHTML = '';
      this.results = [];
      return;
    }
    
    if (query.startsWith('>')) {
      this.renderCommands(query.substring(1).trim().toLowerCase());
      return;
    }
    
    try {
      // Super CRM unified search (falls back to match-manager)
      let res = await fetch(`/api/crm/search?q=${encodeURIComponent(query)}`);
      if (!res.ok) {
        res = await fetch(`/api/match-manager/search?q=${encodeURIComponent(query)}`);
      }
      const data = await res.json();
      this.results = data.results || [];
      this.selectedIndex = this.results.length > 0 ? 0 : -1;
      this.renderResults();
    } catch(e) {
      console.error(e);
    }
  },
  
  renderCommands: function(cmdQuery) {
    const commands = [
      { type: 'command', title: 'New Intake', icon: '📝', action: () => { const b = document.querySelector('[data-tab="tabIntake"]'); if (b) SL.switchTab(b); } },
      { type: 'command', title: 'Active Bonds', icon: '📋', action: () => { const b = document.querySelector('[data-tab="tabActiveBonds"]'); if (b) { SL.switchTab(b); if (typeof loadActiveBonds === 'function') loadActiveBonds(); } } },
      { type: 'command', title: 'Lead Explorer', icon: '🔍', action: () => { const b = document.querySelector('[data-tab="tabLeads"]'); if (b) SL.switchTab(b); } },
      { type: 'command', title: 'Indemnitors', icon: '🛡️', action: () => { const b = document.querySelector('[data-tab="tabIndemnitor"]'); if (b) { SL.switchTab(b); if (window.SLIndemnitor) SLIndemnitor.load(); } } },
      { type: 'command', title: 'Outreach Pipeline', icon: '☘️', action: () => { const b = document.querySelector('[data-tab="tabProspective"]'); if (b) { SL.switchTab(b); if (window.SLProspective) SLProspective.load(); } } },
      { type: 'command', title: 'Open Activity Feed', icon: '⚡️', action: () => SLActivityFeed.toggle() },
      { type: 'command', title: 'Refresh Dashboard', icon: '↻', action: () => { if (window.SL && SL.refresh) SL.refresh(); else location.reload(); } }
    ];
    
    this.results = commands.filter(c => c.title.toLowerCase().includes(cmdQuery));
    this.selectedIndex = this.results.length > 0 ? 0 : -1;
    this.renderResults();
  },
  
  renderResults: function() {
    this.resultsDiv.innerHTML = '';
    if (this.results.length === 0) {
      this.resultsDiv.innerHTML = '<div style="padding:16px;color:#888;text-align:center">No results found</div>';
      return;
    }
    
    this.results.forEach((item, i) => {
      const div = document.createElement('div');
      div.className = `omnibar-result ${i === this.selectedIndex ? 'selected' : ''}`;
      div.style.cssText = `padding:12px; border-radius:8px; cursor:pointer; display:flex; align-items:center; gap:12px; margin-bottom:4px; ${i === this.selectedIndex ? 'background:var(--accent,#3b82f6); color:#fff;' : 'background:transparent; color:var(--text,#e2e8f0);'}`;
      
      let icon = '📄';
      let title = '';
      let sub = '';
      
      if (item.type === 'command') {
        icon = item.icon;
        title = item.title;
        sub = 'Command';
      } else {
        title = item.defendant_name || item.title || 'Unknown';
        if (item.source === 'active_bonds') { icon = '🟢'; sub = `Active Bond • ${item.booking_number || ''} • $${item.bond_amount || 0}`; }
        else if (item.source === 'arrests') { icon = '🚨'; sub = `Lead • ${item.booking_number || ''} • $${item.bond_amount || 0} • ${item.lead_status || ''}`; }
        else if (item.source === 'prospective_bonds') { icon = '🟡'; sub = `Outreach • ${item.booking_number || ''} • ${item.stage || ''}`; }
        else if (item.source === 'indemnitors') { icon = '🛡️'; sub = `Indemnitor • ${item.booking_number || ''} • ${item.stage || ''}`; }
        else if (item.source === 'intake_queue') { icon = '📥'; sub = `Intake • ${item.county || ''} • ${item.stage || ''}`; }
        else if (item.source === 'tasks') { icon = '✅'; sub = `Task • ${item.booking_number || ''} • ${item.stage || ''}`; }
      }
      
      div.innerHTML = `<span style="font-size:1.5rem">${icon}</span><div><div style="font-weight:600">${title}</div><div style="font-size:0.8rem;opacity:0.8">${sub}</div></div>`;
      
      div.onmouseover = () => {
        this.selectedIndex = i;
        this.renderSelected();
      };
      
      div.onclick = () => this.executeResult(item);
      this.resultsDiv.appendChild(div);
    });
  },
  
  renderSelected: function() {
    Array.from(this.resultsDiv.children).forEach((child, i) => {
      if (i === this.selectedIndex) {
        child.style.background = 'var(--accent,#3b82f6)';
        child.style.color = '#fff';
        child.scrollIntoView({ block: 'nearest' });
      } else {
        child.style.background = 'transparent';
        child.style.color = 'var(--text,#e2e8f0)';
      }
    });
  },
  
  executeResult: function(item) {
    this.close();
    if (item.type === 'command') {
      item.action();
      return;
    }
    const src = item.source || item.type || '';
    if (src === 'active_bonds' || item.type === 'bond') {
      const b = document.querySelector('[data-tab="tabActiveBonds"]');
      if (b) SL.switchTab(b);
      if (typeof loadActiveBonds === 'function') loadActiveBonds();
    } else if (src === 'intake_queue' || item.type === 'intake') {
      const b = document.querySelector('[data-tab="tabIntake"]');
      if (b) SL.switchTab(b);
      if (window.SLIntake) SLIntake.load();
    } else if (src === 'indemnitors' || item.type === 'indemnitor') {
      const b = document.querySelector('[data-tab="tabIndemnitor"]');
      if (b) SL.switchTab(b);
      if (window.SLIndemnitor) SLIndemnitor.load();
    } else if (src === 'prospective_bonds' || item.type === 'prospective') {
      const b = document.querySelector('[data-tab="tabProspective"]');
      if (b) SL.switchTab(b);
      if (window.SLProspective) SLProspective.load();
    } else {
      // Leads / tasks / default → defendants notes drawer
      const b = document.querySelector('[data-tab="tabDefendants"]') || document.querySelector('[data-tab="tabLeads"]');
      if (b) SL.switchTab(b);
      if (typeof openShamrockNotes === 'function' && item.booking_number) {
        openShamrockNotes(item.booking_number, { Defendant_Name: item.defendant_name });
      }
    }
  }
};

window.addEventListener('DOMContentLoaded', () => {
  SLOmnibar.init();
});
