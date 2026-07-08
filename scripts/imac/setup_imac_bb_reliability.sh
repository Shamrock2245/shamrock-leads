#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════════
# setup_imac_bb_reliability.sh
# BlueBubbles 24/7 Reliability Setup — Office M1 iMac
#
# WHAT IT DOES:
#   1. Applies the M1 arm64e boot argument fix (prevents Messages.app crashes)
#   2. Installs the cloudflared LaunchDaemon (system-level, survives reboots)
#   3. Removes the old user-level LaunchAgent for cloudflared
#   4. Installs the BB watchdog LaunchAgent (pings BB every 5 min, auto-restarts)
#   5. Installs the BB auto-start LaunchAgent (starts BB on login)
#   6. Verifies the tunnel is working
#
# USAGE (run from your Mac or SSH into the iMac first):
#   ssh shamrockbailbonds@imac.shamrockbailbonds.biz
#   bash ~/Downloads/setup_imac_bb_reliability.sh
#
# PREREQUISITES:
#   - SIP must be disabled (required for nvram boot-args and LaunchDaemon)
#   - cloudflared must be installed at /usr/local/bin/cloudflared
#   - BlueBubbles v1.9.9 must be installed at /Applications/BlueBubbles.app
#   - The tunnel credentials file must exist at:
#       ~/.cloudflared/bd9101bf-39a5-4b7a-97a8-d024c973c769.json
#
# UPGRADE BLUEBUBBLES FIRST:
#   Download v1.9.9 from:
#   https://github.com/BlueBubblesApp/bluebubbles-server/releases/latest
#   Install the DMG before running this script.
#
# ════════════════════════════════════════════════════════════════════════════════

set -euo pipefail

IMAC_USER="shamrockbailbonds"
HOME_DIR="/Users/${IMAC_USER}"
LAUNCH_AGENTS="${HOME_DIR}/Library/LaunchAgents"
LAUNCH_DAEMONS="/Library/LaunchDaemons"
LOGS_DIR="${HOME_DIR}/Library/Logs"
BB_URL="http://localhost:1234/api/v1/ping?password=2245Bail"
CF_TUNNEL_UUID="bd9101bf-39a5-4b7a-97a8-d024c973c769"

log() { echo "[$(date '+%H:%M:%S')] $*"; }
ok()  { echo "[$(date '+%H:%M:%S')] ✅ $*"; }
warn(){ echo "[$(date '+%H:%M:%S')] ⚠️  $*"; }
fail(){ echo "[$(date '+%H:%M:%S')] ❌ $*"; }

echo "════════════════════════════════════════════════════════════"
echo "  Shamrock Bail Bonds — iMac BlueBubbles Reliability Setup"
echo "════════════════════════════════════════════════════════════"
echo ""

# ── Check prerequisites ───────────────────────────────────────────────────────
log "Checking prerequisites..."

if ! command -v cloudflared &>/dev/null; then
    fail "cloudflared not found. Install with: brew install cloudflared"
    exit 1
fi

if [ ! -f "${HOME_DIR}/.cloudflared/${CF_TUNNEL_UUID}.json" ]; then
    fail "Tunnel credentials not found at ~/.cloudflared/${CF_TUNNEL_UUID}.json"
    fail "Run: cloudflared tunnel login  (and re-authenticate)"
    exit 1
fi

if [ ! -d "/Applications/BlueBubbles.app" ]; then
    warn "BlueBubbles.app not found at /Applications/BlueBubbles.app"
    warn "Download v1.9.9 from: https://github.com/BlueBubblesApp/bluebubbles-server/releases/latest"
    warn "Continuing setup — watchdog will still be installed"
fi

ok "Prerequisites OK"

# ── Step 1: M1 arm64e boot argument fix ──────────────────────────────────────
echo ""
log "Step 1: Applying M1 arm64e boot argument fix..."
log "  This prevents the macOS bug that causes Messages.app crashes on M1 Macs."
log "  Requires SIP to be disabled (which it already is for Private API)."

if sudo nvram boot-args 2>/dev/null | grep -q "arm64e_preview_abi"; then
    ok "arm64e_preview_abi already set — skipping"
else
    sudo nvram boot-args=-arm64e_preview_abi
    ok "arm64e_preview_abi boot argument applied"
    warn "A REBOOT IS REQUIRED for this fix to take effect."
    warn "After this script completes, run: sudo reboot"
fi

# ── Step 2: Install cloudflared as LaunchDaemon (system-level) ───────────────
echo ""
log "Step 2: Installing cloudflared as system-level LaunchDaemon..."

# Write the config.yml if it doesn't exist
if [ ! -f "${HOME_DIR}/.cloudflared/config.yml" ]; then
    log "  Writing cloudflared config.yml..."
    mkdir -p "${HOME_DIR}/.cloudflared"
    cat > "${HOME_DIR}/.cloudflared/config.yml" << CFCONFIG
tunnel: ${CF_TUNNEL_UUID}
credentials-file: ${HOME_DIR}/.cloudflared/${CF_TUNNEL_UUID}.json
ingress:
  - hostname: bb.shamrockbailbonds.biz
    service: http://localhost:1234
  - hostname: imac.shamrockbailbonds.biz
    service: ssh://localhost:22
  - service: http_status:404
CFCONFIG
    ok "cloudflared config.yml written"
fi

# Write the LaunchDaemon plist
DAEMON_PLIST="${LAUNCH_DAEMONS}/com.shamrock.cloudflared-tunnel.plist"
sudo tee "${DAEMON_PLIST}" > /dev/null << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.shamrock.cloudflared-tunnel</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/cloudflared</string>
        <string>tunnel</string>
        <string>--config</string>
        <string>${HOME_DIR}/.cloudflared/config.yml</string>
        <string>run</string>
        <string>${CF_TUNNEL_UUID}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>/var/log/cloudflared-tunnel.log</string>
    <key>StandardErrorPath</key>
    <string>/var/log/cloudflared-tunnel-error.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>${HOME_DIR}</string>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
PLIST

sudo chown root:wheel "${DAEMON_PLIST}"
sudo chmod 644 "${DAEMON_PLIST}"

# Unload if already loaded
sudo launchctl unload "${DAEMON_PLIST}" 2>/dev/null || true
sudo launchctl load "${DAEMON_PLIST}"
ok "cloudflared LaunchDaemon installed and started"

# ── Step 3: Remove old user-level LaunchAgent for cloudflared ────────────────
echo ""
log "Step 3: Removing old user-level cloudflared LaunchAgent..."

OLD_AGENT="${LAUNCH_AGENTS}/com.cloudflare.bluebubbles-tunnel.plist"
if [ -f "${OLD_AGENT}" ]; then
    launchctl unload "${OLD_AGENT}" 2>/dev/null || true
    rm -f "${OLD_AGENT}"
    ok "Old LaunchAgent removed"
else
    ok "Old LaunchAgent not found — nothing to remove"
fi

# ── Step 4: Install BB watchdog LaunchAgent ───────────────────────────────────
echo ""
log "Step 4: Installing BlueBubbles watchdog LaunchAgent..."

mkdir -p "${LOGS_DIR}"

# Write the watchdog script
cat > "${HOME_DIR}/bb_watchdog.sh" << 'WATCHDOG'
#!/usr/bin/env bash
BB_URL="http://localhost:1234/api/v1/ping?password=2245Bail"
BB_APP_PATH="/Applications/BlueBubbles.app"
LOG_FILE="${HOME}/Library/Logs/bb_watchdog.log"
STATE_FILE="${HOME}/.bb_watchdog_failures"
MAX_FAILURES=3

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"; }
get_failures() { [ -f "${STATE_FILE}" ] && cat "${STATE_FILE}" || echo 0; }
set_failures() { echo "$1" > "${STATE_FILE}"; }

check_bb() {
    local code
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 "${BB_URL}" 2>/dev/null)
    [ "${code}" = "200" ]
}

restart_bb() {
    log "RESTART: Killing BlueBubbles..."
    pkill -9 -f "BlueBubbles" 2>/dev/null || true
    sleep 3
    log "RESTART: Relaunching..."
    open -a "${BB_APP_PATH}"
    sleep 30
    if check_bb; then
        log "RESTART: ✅ BlueBubbles responding after restart"
        set_failures 0
    else
        log "RESTART: ⚠️  Still not responding — will retry next cycle"
    fi
}

failures=$(get_failures)
if check_bb; then
    [ "${failures}" -gt 0 ] && log "HEALTHY: Responding again (was failing)"
    set_failures 0
else
    failures=$((failures + 1))
    set_failures "${failures}"
    log "FAILURE ${failures}/${MAX_FAILURES}: Not responding at ${BB_URL}"
    if [ "${failures}" -ge "${MAX_FAILURES}" ]; then
        log "ACTION: Threshold reached — restarting"
        set_failures 0
        restart_bb
    fi
fi
WATCHDOG

chmod +x "${HOME_DIR}/bb_watchdog.sh"

# Write the LaunchAgent plist
WATCHDOG_PLIST="${LAUNCH_AGENTS}/com.shamrock.bb-watchdog.plist"
cat > "${WATCHDOG_PLIST}" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.shamrock.bb-watchdog</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>${HOME_DIR}/bb_watchdog.sh</string>
    </array>
    <key>StartInterval</key>
    <integer>300</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>${LOGS_DIR}/bb_watchdog_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${LOGS_DIR}/bb_watchdog_stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
        <key>HOME</key>
        <string>${HOME_DIR}</string>
    </dict>
</dict>
</plist>
PLIST

launchctl unload "${WATCHDOG_PLIST}" 2>/dev/null || true
launchctl load "${WATCHDOG_PLIST}"
ok "BB watchdog LaunchAgent installed (runs every 5 minutes)"

# ── Step 5: Install BB auto-start LaunchAgent ─────────────────────────────────
echo ""
log "Step 5: Installing BlueBubbles auto-start LaunchAgent..."

AUTOSTART_PLIST="${LAUNCH_AGENTS}/com.shamrock.bluebubbles-autostart.plist"
cat > "${AUTOSTART_PLIST}" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.shamrock.bluebubbles-autostart</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/open</string>
        <string>-a</string>
        <string>BlueBubbles</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>${LOGS_DIR}/bb_autostart_stdout.log</string>
    <key>StandardErrorPath</key>
    <string>${LOGS_DIR}/bb_autostart_stderr.log</string>
</dict>
</plist>
PLIST

launchctl unload "${AUTOSTART_PLIST}" 2>/dev/null || true
launchctl load "${AUTOSTART_PLIST}"
ok "BB auto-start LaunchAgent installed"

# ── Step 6: Verify tunnel ─────────────────────────────────────────────────────
echo ""
log "Step 6: Verifying Cloudflare tunnel..."
sleep 5

if curl -s --max-time 10 "https://bb.shamrockbailbonds.biz/api/v1/ping?password=2245Bail" | grep -q "200\|pong\|true"; then
    ok "Tunnel is working — bb.shamrockbailbonds.biz is reachable"
else
    warn "Tunnel not responding yet — this may take 10-30s after daemon start"
    warn "Verify manually: curl 'https://bb.shamrockbailbonds.biz/api/v1/ping?password=2245Bail'"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════"
echo "  ✅ iMac BlueBubbles Reliability Setup Complete"
echo ""
echo "  What was installed:"
echo "    • cloudflared LaunchDaemon (system-level, auto-restart)"
echo "    • BB watchdog LaunchAgent (every 5 min, auto-restart on failure)"
echo "    • BB auto-start LaunchAgent (starts BB on login)"
echo "    • M1 arm64e boot argument fix"
echo ""
echo "  Logs:"
echo "    Cloudflared:  /var/log/cloudflared-tunnel.log"
echo "    BB Watchdog:  ~/Library/Logs/bb_watchdog.log"
echo ""
echo "  IMPORTANT: If the arm64e fix was just applied, REBOOT NOW:"
echo "    sudo reboot"
echo ""
echo "  After reboot, verify:"
echo "    curl 'https://bb.shamrockbailbonds.biz/api/v1/ping?password=2245Bail'"
echo "════════════════════════════════════════════════════════════"
