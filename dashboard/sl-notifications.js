/**
 * ShamrockLeads — Notification Center + Premium Calculator
 * Global utilities accessible from any dashboard tab.
 */

// ═══════════════════════════════════════════════════════════════════════════════
// NOTIFICATION CENTER
// ═══════════════════════════════════════════════════════════════════════════════

const SLNotifications = {
  _open: false,
  _pollInterval: null,

  init() {
    // Start polling for unread count every 30 seconds
    this.refreshBadge();
    this._pollInterval = setInterval(() => this.refreshBadge(), 30000);
    // Close on outside click
    document.addEventListener('click', (e) => {
      const panel = document.getElementById('notifPanel');
      const bell = document.querySelector('.notif-bell-wrapper');
      if (this._open && panel && !panel.contains(e.target) && !bell?.contains(e.target)) {
        this.close();
      }
    });
  },

  async refreshBadge() {
    try {
      const r = await fetch('/api/notifications/unread-count');
      const d = await r.json();
      const badge = document.getElementById('notifBadge');
      if (badge) {
        const count = d.unread || 0;
        badge.textContent = count > 99 ? '99+' : count;
        badge.style.display = count > 0 ? 'flex' : 'none';
      }
    } catch (e) { /* silent */ }
  },

  toggle() {
    this._open ? this.close() : this.open();
  },

  async open() {
    const panel = document.getElementById('notifPanel');
    if (!panel) return;
    panel.style.display = 'block';
    this._open = true;
    await this.loadNotifications();
  },

  close() {
    const panel = document.getElementById('notifPanel');
    if (panel) panel.style.display = 'none';
    this._open = false;
  },

  async loadNotifications() {
    const list = document.getElementById('notifList');
    if (!list) return;
    list.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-muted)">Loading...</div>';

    try {
      const r = await fetch('/api/notifications?limit=30');
      const d = await r.json();
      const notifs = d.notifications || [];

      if (notifs.length === 0) {
        list.innerHTML = '<div style="text-align:center;padding:32px;color:var(--text-muted)">✅ All caught up!</div>';
        return;
      }

      list.innerHTML = notifs.map(n => {
        const timeAgo = this._timeAgo(n.created_at);
        const unreadDot = n.read ? '' : '<span style="width:8px;height:8px;border-radius:50%;background:#3b82f6;display:inline-block;margin-right:6px;flex-shrink:0"></span>';
        const priorityBorder = {
          critical: 'border-left:3px solid #dc2626',
          high: 'border-left:3px solid #f59e0b',
          medium: 'border-left:3px solid #3b82f6',
          low: 'border-left:3px solid #6b7280',
        }[n.priority] || '';

        return `
          <div style="padding:10px 12px;margin-bottom:4px;border-radius:8px;background:${n.read ? 'transparent' : 'rgba(59,130,246,0.05)'};${priorityBorder};cursor:pointer;transition:background 0.2s"
               onmouseenter="this.style.background='rgba(255,255,255,0.05)'"
               onmouseleave="this.style.background='${n.read ? 'transparent' : 'rgba(59,130,246,0.05)'}'"
               onclick="SLNotifications.markRead('${n.notification_id}',this)">
            <div style="display:flex;align-items:flex-start;gap:8px">
              <span style="font-size:1.1rem;flex-shrink:0">${n.icon || '☘️'}</span>
              <div style="flex:1;min-width:0">
                <div style="display:flex;align-items:center;gap:4px">
                  ${unreadDot}
                  <span style="font-weight:600;font-size:0.85rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${this._esc(n.title)}</span>
                </div>
                <div style="font-size:0.78rem;color:var(--text-muted);margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${this._esc(n.message)}</div>
                <div style="font-size:0.7rem;color:var(--text-muted);margin-top:4px;opacity:0.7">${timeAgo}</div>
              </div>
              <button onclick="event.stopPropagation();SLNotifications.dismiss('${n.notification_id}',this.closest('[onclick]'))"
                      style="background:none;border:none;color:var(--text-muted);cursor:pointer;font-size:0.9rem;padding:2px 4px;opacity:0.5"
                      onmouseenter="this.style.opacity='1'" onmouseleave="this.style.opacity='0.5'"
                      title="Dismiss">✕</button>
            </div>
          </div>
        `;
      }).join('');
    } catch (e) {
      list.innerHTML = '<div style="text-align:center;padding:20px;color:#ef4444">Failed to load notifications</div>';
    }
  },

  async markRead(id, el) {
    try {
      await fetch(`/api/notifications/${id}/read`, { method: 'POST' });
      if (el) el.style.background = 'transparent';
      this.refreshBadge();
    } catch (e) { /* silent */ }
  },

  async markAllRead() {
    try {
      await fetch('/api/notifications/read-all', { method: 'POST' });
      this.refreshBadge();
      this.loadNotifications();
    } catch (e) { /* silent */ }
  },

  async dismiss(id, el) {
    try {
      await fetch(`/api/notifications/${id}`, { method: 'DELETE' });
      if (el) el.style.display = 'none';
      this.refreshBadge();
    } catch (e) { /* silent */ }
  },

  _timeAgo(iso) {
    if (!iso) return '';
    const diff = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'Just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    return `${days}d ago`;
  },

  _esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = s;
    return d.innerHTML;
  },
};


// ═══════════════════════════════════════════════════════════════════════════════
// PREMIUM CALCULATOR
// ═══════════════════════════════════════════════════════════════════════════════

const SLCalc = {
  // Surety rate config (mirrors backend)
  _rates: {
    osi: { premium: 0.10, surety: 0.075, buf: 0.05 },
    palmetto: { premium: 0.10, surety: 0.10, buf: 0.05 },
  },

  open() {
    const modal = document.getElementById('calcModal');
    if (modal) {
      modal.style.display = 'flex';
      const input = document.getElementById('calcBondAmount');
      if (input) {
        input.focus();
        if (input.value) this.calculate();
      }
    }
  },

  close() {
    const modal = document.getElementById('calcModal');
    if (modal) modal.style.display = 'none';
  },

  calculate() {
    const bond = parseFloat(document.getElementById('calcBondAmount')?.value || 0);
    const surety = document.getElementById('calcSurety')?.value || 'osi';
    const results = document.getElementById('calcResults');

    if (bond <= 0 || !results) {
      if (results) results.style.display = 'none';
      return;
    }

    const rates = this._rates[surety] || this._rates.osi;
    const premium = Math.max(bond * rates.premium, 100);
    const suretyOwed = premium * rates.surety;
    const buf = premium * rates.buf;
    const agent = premium - suretyOwed - buf;

    document.getElementById('calcPremium').textContent = `$${premium.toLocaleString('en-US', {minimumFractionDigits: 2})}`;
    document.getElementById('calcAgent').textContent = `$${agent.toLocaleString('en-US', {minimumFractionDigits: 2})}`;
    document.getElementById('calcSuretyOwed').textContent = `$${suretyOwed.toLocaleString('en-US', {minimumFractionDigits: 2})}`;
    document.getElementById('calcBUF').textContent = `$${buf.toLocaleString('en-US', {minimumFractionDigits: 2})}`;
    results.style.display = 'block';
  },
};


// ═══════════════════════════════════════════════════════════════════════════════
// KEYBOARD SHORTCUTS
// ═══════════════════════════════════════════════════════════════════════════════

document.addEventListener('keydown', (e) => {
  // Cmd+E or Ctrl+E — Toggle Notifications
  if ((e.metaKey || e.ctrlKey) && e.key === 'e') {
    e.preventDefault();
    SLNotifications.toggle();
  }
  // Cmd+K or Ctrl+K — Premium Calculator
  if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
    e.preventDefault();
    const modal = document.getElementById('calcModal');
    if (modal && modal.style.display === 'flex') {
      SLCalc.close();
    } else {
      SLCalc.open();
    }
  }
  // Escape — Close modals
  if (e.key === 'Escape') {
    SLCalc.close();
    SLNotifications.close();
  }
});

// ═══════════════════════════════════════════════════════════════════════════════
// AUTO-INIT
// ═══════════════════════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
  SLNotifications.init();
});
