/**
 * Super CRM — Example: Programmatic Defendant Onboarding & Bond Writing
 * 
 * Demonstrates the 4-step sequence to get a new defendant into the Super CRM:
 * 1. Add Powers of Attorney (POAs) to inventory (/api/poa/add)
 * 2. Bulk-assign POAs per charge/case (/api/poa/bulk-assign)
 * 3. Finalize DocuSeal paperwork packet with structured charge breakdown (/api/paperwork/packet/finalize)
 * 4. Extract DocuSeal signing link and construct the iPad in-person signing URL
 * 
 * Usage: Paste directly into the browser DevTools Console while logged into
 * https://leads.shamrockbailbonds.biz (or http://localhost:5050 in dev).
 */

(async () => {
  const j = (u, b) => fetch(u, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(b)
  }).then(async r => ({
    status: r.status,
    data: await r.json().catch(() => ({}))
  }));

  // Step 1: Add POAs to inventory for the surety (e.g. OSI)
  const items = [
    { poa_number: 'OSI-P6-116-26-0015', poa_full: 'OSI-P6-116-26-0015', poa_prefix: 'OSI-P6', surety_id: 'osi', max_bond_value: 6000 },
    { poa_number: 'OSI-P6-116-26-0016', poa_full: 'OSI-P6-116-26-0016', poa_prefix: 'OSI-P6', surety_id: 'osi', max_bond_value: 6000 },
    { poa_number: 'OSI-P3-116-26-0018', poa_full: 'OSI-P3-116-26-0018', poa_prefix: 'OSI-P3', surety_id: 'osi', max_bond_value: 3000 },
    { poa_number: 'OSI-P3-116-26-0019', poa_full: 'OSI-P3-116-26-0019', poa_prefix: 'OSI-P3', surety_id: 'osi', max_bond_value: 3000 },
  ];
  const add = await j('/api/poa/add', { surety_id: 'osi', items });

  // Step 2: Bulk-assign POAs to the defendant / bond case
  const assign = await j('/api/poa/bulk-assign', {
    surety_id: 'osi',
    bond_case_id: '1033474',
    defendant_name: 'SCHMIDT, AARON PAUL',
    assignments: [
      { poa_number: 'OSI-P6-116-26-0015', charge: '893.13-6a', appearance_bond_number: '26CF017605' },
      { poa_number: 'OSI-P6-116-26-0016', charge: '790.23-1a', appearance_bond_number: '26CF017605' },
      { poa_number: 'OSI-P3-116-26-0018', charge: '843.02', appearance_bond_number: '26CF017605' },
      { poa_number: 'OSI-P3-116-26-0019', charge: '893.147-1', appearance_bond_number: '26CF017605' },
    ]
  });

  // Step 3: Finalize DocuSeal paperwork packet with structured charge details
  const fin = await j('/api/paperwork/packet/finalize', {
    booking_number: '1033474',
    county: 'Lee',
    surety_id: 'osi',
    provider: 'docuseal',
    signer_email: 'admin@shamrockbailbonds.biz',
    include_payment_plan: true,
    include_defendant: true,
    send_email: false,
    poa_number: 'OSI-P6-116-26-0015',
    charge_details: [
      { offense_code: '893.13-6a', bond_amount: 5000, case_number: '26CF017605', poa_number: 'OSI-P6-116-26-0015' },
      { offense_code: '790.23-1a', bond_amount: 5000, case_number: '26CF017605', poa_number: 'OSI-P6-116-26-0016' },
      { offense_code: '843.02', bond_amount: 1000, case_number: '26CF017605', poa_number: 'OSI-P3-116-26-0018' },
      { offense_code: '893.147-1', bond_amount: 1000, case_number: '26CF017605', poa_number: 'OSI-P3-116-26-0019' },
    ]
  });

  // Step 4: Extract DocuSeal signing link and build iPad in-person signing URL
  const d = fin.data || {};
  const ds = d.send_results?.docuseal || {};
  const link = d.signing_link || ds.signing_link || ds.sign_links?.[0] || ds.submitters?.[0]?.sign_url || '';
  const ipad = link ? `https://paperwork.shamrockbailbonds.biz/?mode=ipad&link=${encodeURIComponent(link)}` : '';

  const out = {
    add,
    assign,
    finalizeStatus: fin.status,
    packet_id: d.packet_id,
    error: d.error || d.message,
    link,
    ipad
  };

  console.log(out);
  return out;
})();
