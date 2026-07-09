#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════════════════════
# bb_watchdog.sh — BlueBubbles Server Watchdog
# Office M1 iMac — macOS 14.4.1 (Sonoma)
#
# WHAT IT DOES:
#   Pings the local BlueBubbles API every 5 minutes (via LaunchAgent).
#   If BB is unresponsive for 3 consecutive checks, it force-kills and
#   relaunches the BlueBubbles Electron app.
#
# INSTALL:
#   1. Copy this script to the iMac:
#      scp scripts/imac/bb_watchdog.sh shamrockbailbonds@imac.shamrockbailbonds.biz:~/bb_watchdog.sh
#      ssh shamrockbailbonds@imac.shamrockbailbonds.biz "chmod +x ~/bb_watchdog.sh"
#
#   2. Install the LaunchAgent plist (see com.shamrock.bb-watchdog.plist):
#      scp scripts/imac/com.shamrock.bb-watchdog.plist \
#          shamrockbailbonds@imac.shamrockbailbonds.biz:~/Library/LaunchAgents/
#      ssh shamrockbailbonds@imac.shamrockbailbonds.biz \
#          "launchctl load ~/Library/LaunchAgents/com.shamrock.bb-watchdog.plist"
#
# LOGS:
#   ~/Library/Logs/bb_watchdog.log
#
# ════════════════════════════════════════════════════════════════════════════════

BB_URL="http://localhost:1234/api/v1/ping"
BB_PASSWORD="${BB_PASSWORD:-${BLUEBUBBLES_PASSWORD:-}}"
if [ -z "$BB_PASSWORD" ]; then echo "Set BB_PASSWORD"; exit 1; fi
BB_APP_NAME="BlueBubbles"
BB_APP_PATH="/Applications/BlueBubbles.app"
LOG_FILE="${HOME}/Library/Logs/bb_watchdog.log"
STATE_FILE="${HOME}/.bb_watchdog_failures"
MAX_FAILURES=3

# ── Logging helper ────────────────────────────────────────────────────────────
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"
}

# ── Read/write failure counter ────────────────────────────────────────────────
get_failures() {
    if [ -f "${STATE_FILE}" ]; then
        cat "${STATE_FILE}"
    else
        echo 0
    fi
}

set_failures() {
    echo "$1" > "${STATE_FILE}"
}

# ── Check if BB is responding ─────────────────────────────────────────────────
check_bb() {
    local http_code
    http_code=$(curl -s -o /dev/null -w "%{http_code}" \
        --max-time 8 \
        "${BB_URL}?password=${BB_PASSWORD}" 2>/dev/null)
    if [ "${http_code}" = "200" ]; then
        return 0
    else
        return 1
    fi
}

# ── Restart BlueBubbles ───────────────────────────────────────────────────────
restart_bb() {
    log "RESTART: Killing BlueBubbles processes..."
    pkill -9 -f "${BB_APP_NAME}" 2>/dev/null || true
    sleep 3

    log "RESTART: Relaunching ${BB_APP_PATH}..."
    if [ -d "${BB_APP_PATH}" ]; then
        open -a "${BB_APP_PATH}"
        log "RESTART: BlueBubbles launched. Waiting 30s for startup..."
        sleep 30

        # Verify it came back up
        if check_bb; then
            log "RESTART: ✅ BlueBubbles is responding after restart"
            set_failures 0
        else
            log "RESTART: ⚠️  BlueBubbles still not responding after restart — will retry next cycle"
        fi
    else
        log "RESTART: ❌ BlueBubbles.app not found at ${BB_APP_PATH}"
    fi
}

# ── Main watchdog logic ───────────────────────────────────────────────────────
main() {
    local failures
    failures=$(get_failures)

    if check_bb; then
        if [ "${failures}" -gt 0 ]; then
            log "HEALTHY: BlueBubbles is responding (was failing — resetting counter)"
        fi
        set_failures 0
    else
        failures=$((failures + 1))
        set_failures "${failures}"
        log "FAILURE ${failures}/${MAX_FAILURES}: BlueBubbles not responding at ${BB_URL}"

        if [ "${failures}" -ge "${MAX_FAILURES}" ]; then
            log "ACTION: Failure threshold reached — initiating restart"
            set_failures 0
            restart_bb
        else
            log "WAITING: Will retry (${failures}/${MAX_FAILURES} failures so far)"
        fi
    fi
}

main
