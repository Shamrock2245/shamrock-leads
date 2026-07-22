#!/usr/bin/env bash
# Install frpc on macOS (office iMac) and register a launch agent/daemon.
# Usage:
#   ./install-frpc-macos.sh /path/to/frpc.toml           # system LaunchDaemon (sudo)
#   ./install-frpc-macos.sh /path/to/frpc.toml --user    # user LaunchAgent (no sudo)
# Requires: curl; sudo only for default system install

set -euo pipefail

FRP_VERSION="${FRP_VERSION:-0.61.1}"
ARCH="$(uname -m)"
case "$ARCH" in
  arm64|aarch64) FRP_ARCH="darwin_arm64" ;;
  x86_64)        FRP_ARCH="darwin_amd64" ;;
  *) echo "Unsupported arch: $ARCH"; exit 1 ;;
esac

CONFIG_SRC="${1:-}"
MODE="${2:-}"
if [[ -z "$CONFIG_SRC" || ! -f "$CONFIG_SRC" ]]; then
  echo "Usage: $0 /path/to/frpc.toml [--user]"
  echo "Copy frpc.bluebubbles.toml.example, set token, then re-run."
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
URL="https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}/frp_${FRP_VERSION}_${FRP_ARCH}.tar.gz"
echo "Downloading $URL"
curl -fsSL "$URL" -o "$TMP/frp.tgz"
tar -xzf "$TMP/frp.tgz" -C "$TMP"
FRPC_SRC="$TMP/frp_${FRP_VERSION}_${FRP_ARCH}/frpc"

if [[ "$MODE" == "--user" ]]; then
  BIN_DIR="${HOME}/bin"
  CFG_DIR="${HOME}/Library/Application Support/frp"
  LOG_DIR="${HOME}/Library/Logs"
  mkdir -p "$BIN_DIR" "$CFG_DIR" "$LOG_DIR"
  cp "$FRPC_SRC" "$BIN_DIR/frpc"
  chmod +x "$BIN_DIR/frpc"
  cp "$CONFIG_SRC" "$CFG_DIR/frpc.toml"
  chmod 600 "$CFG_DIR/frpc.toml"

  PLIST="${HOME}/Library/LaunchAgents/com.shamrock.frpc.plist"
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.shamrock.frpc</string>
  <key>ProgramArguments</key>
  <array>
    <string>${BIN_DIR}/frpc</string>
    <string>-c</string>
    <string>${CFG_DIR}/frpc.toml</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/frpc.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/frpc.err</string>
</dict>
</plist>
PLIST

  UID_NUM="$(id -u)"
  launchctl bootout "gui/${UID_NUM}/com.shamrock.frpc" 2>/dev/null || true
  launchctl bootstrap "gui/${UID_NUM}" "$PLIST"
  launchctl enable "gui/${UID_NUM}/com.shamrock.frpc" 2>/dev/null || true
  launchctl kickstart -k "gui/${UID_NUM}/com.shamrock.frpc"

  echo ""
  echo "✅ frpc installed (user LaunchAgent)"
  echo "   Config: $CFG_DIR/frpc.toml"
  echo "   Logs:   $LOG_DIR/frpc.log"
else
  BIN_DIR="/usr/local/bin"
  CFG_DIR="/usr/local/etc/frp"
  sudo mkdir -p "$BIN_DIR" "$CFG_DIR"
  sudo cp "$FRPC_SRC" "$BIN_DIR/frpc"
  sudo chmod +x "$BIN_DIR/frpc"
  sudo cp "$CONFIG_SRC" "$CFG_DIR/frpc.toml"
  sudo chmod 600 "$CFG_DIR/frpc.toml"

  PLIST="/Library/LaunchDaemons/com.shamrock.frpc.plist"
  sudo tee "$PLIST" >/dev/null <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.shamrock.frpc</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/frpc</string>
    <string>-c</string>
    <string>/usr/local/etc/frp/frpc.toml</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>/var/log/frpc.log</string>
  <key>StandardErrorPath</key>
  <string>/var/log/frpc.err</string>
</dict>
</plist>
PLIST

  sudo launchctl bootout system "$PLIST" 2>/dev/null || true
  sudo launchctl bootstrap system "$PLIST"
  sudo launchctl enable system/com.shamrock.frpc
  sudo launchctl kickstart -k system/com.shamrock.frpc

  echo ""
  echo "✅ frpc installed (system LaunchDaemon)"
  echo "   Config: $CFG_DIR/frpc.toml"
  echo "   Logs:   /var/log/frpc.log"
fi

echo "   Test local BB: curl -sS 'http://127.0.0.1:1234/api/v1/ping'"
echo "   From VPS BB:   curl -sS 'http://127.0.0.1:12434/api/v1/ping'"
echo "   SSH via frp:   ssh -p 12222 shamrockbailbonds@178.156.179.237"
