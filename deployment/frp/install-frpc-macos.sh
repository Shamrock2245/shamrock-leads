#!/usr/bin/env bash
# Install frpc on macOS (office iMac) and register a LaunchDaemon.
# Usage:
#   ./install-frpc-macos.sh /path/to/frpc.toml
# Requires: curl, sudo for LaunchDaemon

set -euo pipefail

FRP_VERSION="${FRP_VERSION:-0.61.1}"
ARCH="$(uname -m)"
case "$ARCH" in
  arm64|aarch64) FRP_ARCH="darwin_arm64" ;;
  x86_64)        FRP_ARCH="darwin_amd64" ;;
  *) echo "Unsupported arch: $ARCH"; exit 1 ;;
esac

CONFIG_SRC="${1:-}"
if [[ -z "$CONFIG_SRC" || ! -f "$CONFIG_SRC" ]]; then
  echo "Usage: $0 /path/to/frpc.toml"
  echo "Copy frpc.bluebubbles.toml.example, set token, then re-run."
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
URL="https://github.com/fatedier/frp/releases/download/v${FRP_VERSION}/frp_${FRP_VERSION}_${FRP_ARCH}.tar.gz"
echo "Downloading $URL"
curl -fsSL "$URL" -o "$TMP/frp.tgz"
tar -xzf "$TMP/frp.tgz" -C "$TMP"
BIN_DIR="/usr/local/bin"
CFG_DIR="/usr/local/etc/frp"
sudo mkdir -p "$BIN_DIR" "$CFG_DIR"
sudo cp "$TMP/frp_${FRP_VERSION}_${FRP_ARCH}/frpc" "$BIN_DIR/frpc"
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
echo "✅ frpc installed and started"
echo "   Config: $CFG_DIR/frpc.toml"
echo "   Logs:   /var/log/frpc.log"
echo "   Test:   curl -sS http://127.0.0.1:1234/api/v1/ping"
echo "   From VPS: curl -sS http://127.0.0.1:12434/api/v1/ping  (or public :12434)"
