#!/bin/bash
# Warren Node Enrollment Script
# Enrolls a personal device as a Warren proxy node (residential exit).
#
# Preferred (join code from hub logs / admin dashboard):
#   ./warren_node_enroll.sh --join warren1.XXXX
#
# Manual flags:
#   ./warren_node_enroll.sh --hub 178.156.179.237:7000 --token ENROLL_TOKEN --name home-pc
#
# Optional:
#   --version v0.4.11
#   --insecure          # skip TLS fingerprint pin (dev only)
#   --install-service   # register boot service (warren node install)

set -euo pipefail

HUB_URL=""
TOKEN=""
DEVICE_NAME=""
JOIN_CODE=""
WARREN_VERSION="v0.4.11"
INSECURE=0
INSTALL_SERVICE=0

while [[ $# -gt 0 ]]; do
    case $1 in
        --hub)
            HUB_URL="$2"
            shift 2
            ;;
        --token)
            TOKEN="$2"
            shift 2
            ;;
        --name)
            DEVICE_NAME="$2"
            shift 2
            ;;
        --join)
            JOIN_CODE="$2"
            shift 2
            ;;
        --version)
            WARREN_VERSION="$2"
            shift 2
            ;;
        --insecure)
            INSECURE=1
            shift
            ;;
        --install-service)
            INSTALL_SERVICE=1
            shift
            ;;
        -h|--help)
            sed -n '2,16p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

if [[ -z "$JOIN_CODE" ]]; then
    if [[ -z "$HUB_URL" || -z "$TOKEN" ]]; then
        echo "Usage:"
        echo "  $0 --join warren1.XXXX [--name DEVICE_NAME]"
        echo "  $0 --hub HOST:7000 --token ENROLL_TOKEN --name DEVICE_NAME"
        exit 1
    fi
fi

echo "🚀 Warren Node Enrollment"
echo "=========================="
if [[ -n "$JOIN_CODE" ]]; then
    echo "Mode: join code"
else
    echo "Hub:    $HUB_URL"
    echo "Token:  (hidden)"
fi
echo "Name:   ${DEVICE_NAME:-auto}"
echo "Version:$WARREN_VERSION"
echo ""

# Detect OS / arch → release asset
OS_TYPE="$(uname -s)"
ARCH="$(uname -m)"
case "$OS_TYPE" in
    Linux)
        case "$ARCH" in
            x86_64) ASSET="linux-x86_64" ;;
            aarch64|arm64) ASSET="linux-arm64" ;;
            *) echo "❌ Unsupported arch: $ARCH"; exit 1 ;;
        esac
        ;;
    Darwin)
        case "$ARCH" in
            x86_64) ASSET="macos-x86_64" ;;
            arm64|aarch64) ASSET="macos-arm64" ;;
            *) echo "❌ Unsupported arch: $ARCH"; exit 1 ;;
        esac
        ;;
    *)
        echo "❌ Unsupported OS: $OS_TYPE"
        echo "On Windows use: https://github.com/doedja/warren/releases"
        exit 1
        ;;
esac

ARCHIVE_URL="https://github.com/doedja/warren/releases/download/${WARREN_VERSION}/warren-${WARREN_VERSION}-${ASSET}.tar.gz"
echo "📦 Detected: $OS_TYPE ($ARCH)"
echo "📥 Downloading: $ARCHIVE_URL"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

if ! curl -fL "$ARCHIVE_URL" -o "$TMP_DIR/warren.tar.gz"; then
    echo "❌ Failed to download Warren archive"
    exit 1
fi

tar -xzf "$TMP_DIR/warren.tar.gz" -C "$TMP_DIR"
BINARY_PATH="$(find "$TMP_DIR" -type f -name warren | head -1)"
if [[ -z "$BINARY_PATH" ]]; then
    echo "❌ warren binary not found in archive"
    exit 1
fi
chmod +x "$BINARY_PATH"

INSTALL_DIR="${HOME}/.local/bin"
mkdir -p "$INSTALL_DIR"
cp "$BINARY_PATH" "$INSTALL_DIR/warren"
echo "✅ Installed to $INSTALL_DIR/warren"

if ! echo "$PATH" | grep -q "$INSTALL_DIR"; then
    SHELL_RC="${HOME}/.zshrc"
    [[ -f "${HOME}/.bashrc" && ! -f "$SHELL_RC" ]] && SHELL_RC="${HOME}/.bashrc"
    echo "export PATH=\"\$PATH:$INSTALL_DIR\"" >> "$SHELL_RC"
    export PATH="$PATH:$INSTALL_DIR"
    echo "✅ Added $INSTALL_DIR to PATH via $SHELL_RC"
fi

# Build node args
NODE_ARGS=()
if [[ -n "$JOIN_CODE" ]]; then
    NODE_ARGS+=(--join "$JOIN_CODE")
else
    # Strip wss:// or https:// if user pasted a full URL
    HUB_CLEAN="${HUB_URL#wss://}"
    HUB_CLEAN="${HUB_CLEAN#ws://}"
    HUB_CLEAN="${HUB_CLEAN#https://}"
    HUB_CLEAN="${HUB_CLEAN#http://}"
    NODE_ARGS+=(--hub "$HUB_CLEAN" --token "$TOKEN" --tls)
fi
if [[ -n "$DEVICE_NAME" ]]; then
    NODE_ARGS+=(--name "$DEVICE_NAME")
fi
if [[ "$INSECURE" -eq 1 ]]; then
    NODE_ARGS+=(--insecure)
fi

echo ""
if [[ "$INSTALL_SERVICE" -eq 1 ]]; then
    echo "🔧 Registering boot service (warren node install)..."
    "$INSTALL_DIR/warren" node install "${NODE_ARGS[@]}"
    echo "✅ Node service installed. Check status with:"
    echo "   systemctl --user status warren-node   # or launchctl on macOS"
else
    echo "🔗 Start the node (foreground):"
    echo ""
    echo "  warren node run ${NODE_ARGS[*]}"
    echo ""
    echo "Or register as a boot service:"
    echo ""
    echo "  warren node install ${NODE_ARGS[*]}"
    echo ""
    echo "Background one-shot:"
    echo ""
    echo "  nohup warren node run ${NODE_ARGS[*]} > ~/.warren-node.log 2>&1 &"
fi

echo ""
echo "🔗 Docs: https://github.com/doedja/warren"
echo "         docs/APE_INTEGRATION_GUIDE.md"
