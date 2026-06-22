const SLPaperwork = {
  load: async function() {
    console.log("Loading Paperwork Config...");
    try {
      const res = await fetch('/api/paperwork/config');
      if (!res.ok) throw new Error("Failed to fetch paperwork config");
      const data = await res.json();
      
      this.renderDocRules(data.doc_rules);
      this.renderTable('tablePaperworkOsi', data.template_map.osi);
      this.renderTable('tablePaperworkPalmetto', data.template_map.palmetto);
    } catch (err) {
      console.error(err);
      SL.flash("Error loading paperwork config: " + err.message, "error");
    }
  },

  renderDocRules: function(rules) {
    const el = document.getElementById('paperworkDocRules');
    if (!el) return;
    el.textContent = JSON.stringify(rules, null, 2);
  },

  renderTable: function(tableId, templates) {
    const tbody = document.querySelector(`#${tableId} tbody`);
    if (!tbody) return;
    tbody.innerHTML = "";
    
    if (!templates) {
      tbody.innerHTML = "<tr><td colspan='4'>No templates found</td></tr>";
      return;
    }

    // Convert object to array
    const entries = Object.entries(templates).map(([key, tpl]) => {
      return { key, ...tpl };
    });

    entries.forEach(t => {
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><strong>${t.key}</strong></td>
        <td>${t.label || t.name || 'N/A'}</td>
        <td style="font-family:monospace;font-size:11px;">${t.template_id}</td>
        <td><span class="badge ${this.getBadgeClass(t.rule)}">${t.rule}</span></td>
      `;
      tbody.appendChild(tr);
    });
  },

  getBadgeClass: function(rule) {
    switch(rule) {
      case "per-indemnitor": return "bg-blue";
      case "per-charge": return "bg-orange";
      case "per-person": return "bg-purple";
      case "shared": return "bg-green";
      case "print-only": return "bg-gray";
      default: return "";
    }
  }
};
