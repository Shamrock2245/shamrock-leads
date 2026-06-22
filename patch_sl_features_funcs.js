const fs = require('fs');
const file = '/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/dashboard/sl-features.js';
let content = fs.readFileSync(file, 'utf8');

// I will just add `triggerSignNowPacket` after `triggerSignNowPhase2`
const funcStr = `
async function triggerSignNowPacket() {
  const data = window._bondModalData;
  if (!data) { toast('No bond data', 'error'); return; }
  const snStatus = document.getElementById('sn-status');
  const phaseBadge = document.getElementById('sn-phase-badge');
  const poaInput = document.getElementById('poaInput_0');
  const poaNumber = poaInput ? poaInput.value.trim() : '';
  
  const routingScenario = document.getElementById('routingScenarioSelect').value;
  
  if (routingScenario === 'all_in_one' && !poaNumber) {
    toast('Enter POA number before sending All-in-One packet', 'error'); return;
  }
  
  // Get checked documents for custom manifest
  const checkedDocs = Array.from(document.querySelectorAll('.doc-chk:checked')).map(el => el.value);
  
  if (snStatus) snStatus.textContent = 'Preparing SignNow packet...';
  try {
    let signerEmail = data.lead.indemnitor_email || '';
    let signerName = data.lead.indemnitor_name || '';
    if (!signerEmail) {
      signerEmail = prompt('Enter indemnitor email:') || '';
      if (!signerEmail) { if (snStatus) snStatus.textContent = 'Cancelled.'; return; }
      signerName = prompt('Enter indemnitor full name:') || 'Indemnitor';
    }
    
    // For Phase 1_2, we hit the phase1 endpoint for now. For all_in_one, we hit generate-packet directly.
    // Actually, let's just hit the generate-packet endpoint directly for everything, since the backend handles it.
    // Let's create a new unified endpoint or just use generate-packet.
    
    const payload = {
      intake_id: data.lead._intake_id || '',
      booking_number: data.booking,
      signer_email: signerEmail,
      signer_name: signerName,
      agent_name: 'Brendan O\\'Shaughnahill',
      agent_license: 'P322089',
      surety_id: data.surety || 'osi',
      poa_number: poaNumber,
      routing_scenario: routingScenario,
      custom_manifest: checkedDocs,
      form_data: {
        defendant: data.lead,
        booking_number: data.booking,
        bond_amount: data.bond,
        surety: data.surety,
        charges: data.chargeList,
      }
    };
    
    const r = await fetch(\`\${API}/api/paperwork/generate-packet\`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const result = await r.json();
    if (result.status === 'success') {
      if (snStatus) snStatus.innerHTML = \`✅ Packet sent to \${signerEmail} (\${result.manifest_size || checkedDocs.length} docs). <a href="\${result.signing_link || '#'}" target="_blank" style="color:#60a5fa;text-decoration:underline;margin-left:8px">Open Signing Link</a>\`;
      if (phaseBadge) { phaseBadge.textContent = 'Packet Sent'; phaseBadge.style.background = 'rgba(59,130,246,0.2)'; phaseBadge.style.color = '#60a5fa'; }
      toast('Packet sent', 'success');
    } else {
      if (snStatus) snStatus.textContent = \`❌ \${result.error || 'Packet creation failed'}\`;
      toast(result.error || 'Packet creation failed', 'error');
    }
  } catch(e) {
    if (snStatus) snStatus.textContent = \`❌ Network error: \${e.message}\`;
    toast('Network error', 'error');
  }
}
`;

if(!content.includes('async function triggerSignNowPacket()')) {
  content = content + funcStr;
  fs.writeFileSync(file, content);
  console.log("Function added successfully.");
} else {
  console.log("Function already exists.");
}
