#!/bin/bash
# Warren Node Enrollment Script
# Enrolls a personal device as a Warren proxy node
#
# Usage: ./warren_node_enroll.sh --hub HUB_URL --token TOKEN --name DEVICE_NAME [--version VERSION]
# Example: ./warren_node_enroll.sh --hub wss://5.161.126.32:8000 --token shamrock-residential --name home-pc

set -e

# Parse arguments
HUB_URL=""
TOKEN=""
DEVICE_NAME=""
WARREN_VERSION="v0.4.11"

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
        --version)
            WARREN_VERSION="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Validate arguments
if [[ -z "$HUB_URL" ]] || [[ -z "$TOKEN" ]] || [[ -z "$DEVICE_NAME" ]]; then
    echo "Usage: $0 --hub HUB_URL --token TOKEN --name DEVICE_NAME [--version VERSION]"
    echo ""
    echo "Example:"
    echo "  $0 --hub wss://5.161.126.32:8000 --token shamrock-residential --name home-pc"
    exit 1
fi

echo "🚀 Warren Node Enrollment"
echo "=========================="
echo "Hub: $HUB_URL"
echo "Token: $TOKEN"
echo "Device Name: $DEVICE_NAME"
echo "Version: $WARREN_VERSION"
echo ""

# Detect OS
OS_TYPE=$(uname -s)
ARCH=$(uname -m)

case "$OS_TYPE" in
    Linux)
        if [[ "$ARCH" == "x86_64" ]]; then
            BINARY_NAME="warren-x86_64-unknown-linux-gnu"
        elif [[ "$ARCH" == "aarch64" ]]; then
            BINARY_NAME="warren-aarch64-unknown-linux-gnu"
        else
            echo "❌ Unsupported architecture: $ARCH"
            exit 1
        fi
        ;;
    Darwin)
        if [[ "$ARCH" == "x86_64" ]]; then
            BINARY_NAME="warren-x86_64-apple-darwin"
        elif [[ "$ARCH" == "arm64" ]]; then
            BINARY_NAME="warren-aarch64-apple-darwin"
        else
            echo "❌ Unsupported architecture: $ARCH"
            exit 1
        fi
        ;;
    *)
        echo "❌ Unsupported OS: $OS_TYPE"
        exit 1
        ;;
esac

echo "📦 Detected: $OS_TYPE ($ARCH)"
echo "📥 Downloading Warren binary: $BINARY_NAME"

# Download binary
BINARY_URL="https://github.com/doedja/warren/releases/download/${WARREN_VERSION}/${BINARY_NAME}"
BINARY_PATH="/tmp/warren-node"

if ! curl -fL "$BINARY_URL" -o "$BINARY_PATH"; then
    echo "❌ Failed to download Warren binary"
    exit 1
fi

chmod +x "$BINARY_PATH"
echo "✅ Binary downloaded"

# Create installation directory
INSTALL_DIR="$HOME/.local/bin"
mkdir -p "$INSTALL_DIR"
mv "$BINARY_PATH" "$INSTALL_DIR/warren"

# Add to PATH if needed
if ! echo "$PATH" | grep -q "$INSTALL_DIR"; then
    echo "export PATH=\"\$PATH:$INSTALL_DIR\"" >> "$HOME/.bashrc"
    export PATH="$PATH:$INSTALL_DIR"
    echo "✅ Added $INSTALL_DIR to PATH"
fi

echo ""
echo "✅ Warren Node Enrollment Complete!"
echo ""
echo "🔗 To start the node, run:"
echo ""
echo "  warren node \\"
echo "    --hub $HUB_URL \\"
echo "    --token $TOKEN \\"
echo "    --name $DEVICE_NAME"
echo ""
echo "📝 To run as a background service (Linux/macOS):"
echo ""
echo "  nohup warren node \\"
echo "    --hub $HUB_URL \\"
echo "    --token $TOKEN \\"
echo "    --name $DEVICE_NAME > ~/.warren-node.log 2>&1 &"
echo ""
echo "🔗 Documentation: https://github.com/doedja/warren"
