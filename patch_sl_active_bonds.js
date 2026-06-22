const fs = require('fs');
const file = '/Users/brendan/Desktop/shamrock-active-software/shamrock-leads/dashboard/sl-active-bonds.js';
let content = fs.readFileSync(file, 'utf8');

const targetStr = `          ${b.status !== 'exonerated' ? \`<button class="btn-export" style="font-size:10px;padding:3px 8px;background:#22c55e;color:#fff" onclick="exonerateFromActiveBonds('\${bkSafe}','\${nameSafe}')">✅ Exonerate</button>\` : ''}`;

const newBtnStr = `          <button class="btn-export" style="font-size:10px;padding:3px 8px;background:#10b981;color:#fff" onclick="fileBondToDrive('\${bkSafe}')">📁 File to Drive</button>`;

if (content.includes(targetStr) && !content.includes('fileBondToDrive')) {
  content = content.replace(targetStr, targetStr + '\n' + newBtnStr);
}

const funcStr = `
async function fileBondToDrive(bookingNumber) {
  toast('Fetching document from SignNow and uploading to Drive...', 'info');
  try {
    const r = await fetch(\`\${API}/api/file-to-drive/\${encodeURIComponent(bookingNumber)}\`, {
      method: 'POST'
    });
    const result = await r.json();
    if (result.status === 'success') {
      toast('Bond filed to Drive successfully', 'success');
      window.open(result.drive_link, '_blank');
    } else {
      toast(result.error || 'Failed to file to drive', 'error');
    }
  } catch(e) {
    toast('Network error while filing to drive', 'error');
  }
}
`;

if(!content.includes('function fileBondToDrive')) {
  content = content + funcStr;
  fs.writeFileSync(file, content);
  console.log("Button and function added.");
} else {
  console.log("Already added.");
}
