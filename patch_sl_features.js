const fs = require('fs');
const file = '/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/dashboard/sl-features.js';
let content = fs.readFileSync(file, 'utf8');

const oldSignnowSection = `    <div class="wb-section" id="signnowSection">
      <div class="wb-section-label" style="display:flex;align-items:center;gap:8px">📝 SignNow Packet <span id="sn-phase-badge" style="font-size:11px;padding:2px 8px;border-radius:10px;background:var(--panel);color:var(--muted)">Not Sent</span></div>
      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
        <button class="btn-export" id="btnPhase1" onclick="triggerSignNowPhase1()" style="background:rgba(59,130,246,0.15);color:#60a5fa">📨 Send Phase 1 (Indemnitor)</button>
        <button class="btn-export" id="btnPhase2" onclick="triggerSignNowPhase2()" style="background:rgba(34,197,94,0.15);color:var(--success)" disabled>📨 Send Phase 2 (Post-Approval)</button>
      </div>
      <div id="sn-status" style="margin-top:8px;font-size:12px;color:var(--muted)"></div>
      <div style="margin-top:12px;display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted)">
        <input type="checkbox" id="bypassPhase1Chk" onchange="document.getElementById('btnPhase2').disabled = !this.checked">
        <label for="bypassPhase1Chk">Indemnitor will fill out within 48 hours (Bypass Phase 1 restriction)</label>
      </div>
    </div>`;

const newSignnowSection = `    <div class="wb-section" id="signnowSection">
      <div class="wb-section-label" style="display:flex;align-items:center;gap:8px">📝 Configure Paperwork & SignNow Packet <span id="sn-phase-badge" style="font-size:11px;padding:2px 8px;border-radius:10px;background:var(--panel);color:var(--muted)">Not Sent</span></div>
      
      <div style="margin-top:8px;font-size:13px">
        <label style="display:block;margin-bottom:4px;font-weight:600">Routing Scenario</label>
        <select id="routingScenarioSelect" style="width:100%;padding:8px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text)">
          <option value="phase1_2">Phase 1 (Indemnitor First) -> Phase 2 (Defendant & Agent Later)</option>
          <option value="all_in_one">All-in-One (Indemnitor -> Defendant -> Agent Sequential)</option>
          <option value="kiosk">Kiosk Mode (Side-by-Side In Person)</option>
        </select>
      </div>

      <div style="margin-top:12px;font-size:13px">
        <label style="display:block;margin-bottom:4px;font-weight:600">Select Forms to Include</label>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;background:var(--bg);padding:10px;border-radius:6px;border:1px solid var(--border)">
          <label><input type="checkbox" class="doc-chk" value="paperwork-header" checked> Cover Sheet / Header</label>
          <label><input type="checkbox" class="doc-chk" value="faq-cosigners" checked> FAQ (Co-Signers)</label>
          <label><input type="checkbox" class="doc-chk" value="faq-defendants" checked> FAQ (Defendants)</label>
          <label><input type="checkbox" class="doc-chk" value="indemnity-agreement" checked> Indemnity Agreement</label>
          <label><input type="checkbox" class="doc-chk" value="promissory-note" checked> Promissory Note</label>
          <label><input type="checkbox" class="doc-chk" value="defendant-application" checked> Defendant Application</label>
          <label><input type="checkbox" class="doc-chk" value="disclosure-form" checked> Disclosure Form</label>
          <label><input type="checkbox" class="doc-chk" value="master-waiver" checked> Master Waiver</label>
          <label><input type="checkbox" class="doc-chk" value="ssa-release" checked> SSA Release</label>
          <label><input type="checkbox" class="doc-chk" value="surety-terms" checked> Surety Terms</label>
          <label><input type="checkbox" class="doc-chk" value="collateral-receipt" checked> Collateral Receipt</label>
          <label><input type="checkbox" class="doc-chk" value="payment-plan" checked> Payment Plan</label>
          <!-- Appearance bonds are handled automatically -->
        </div>
      </div>

      <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">
        <button class="btn-export" id="btnSendPacket" onclick="triggerSignNowPacket()" style="background:rgba(59,130,246,0.15);color:#60a5fa;flex:1">📨 Send SignNow Packet</button>
      </div>
      <div id="sn-status" style="margin-top:8px;font-size:12px;color:var(--muted)"></div>
    </div>`;

if(content.includes(oldSignnowSection)) {
  content = content.replace(oldSignnowSection, newSignnowSection);
  fs.writeFileSync(file, content);
  console.log("Section replaced successfully.");
} else {
  console.log("Could not find the section to replace.");
}
