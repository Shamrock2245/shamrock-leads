#!/bin/bash
# Warren Proxy Hub Deployment Script
# Deploys Warren hub on Hetzner VPS (5.161.126.32)
# 
# Usage: ./warren_hub_deploy.sh [VERSION]
# Example: ./warren_hub_deploy.sh v0.4.11

set -e

WARREN_VERSION="${1:-v0.4.11}"
HETZNER_VPS="5.161.126.32"
WARREN_USER="warren"
WARREN_HOME="/opt/warren"
WARREN_PORT="8000"
WARREN_ADMIN_PORT="8080"

echo "🚀 Warren Proxy Hub Deployment"
echo "================================"
echo "Version: $WARREN_VERSION"
echo "VPS: $HETZNER_VPS"
echo "Hub Port: $WARREN_PORT"
echo "Admin Port: $WARREN_ADMIN_PORT"
echo ""

# Step 1: Download Warren binary
echo "📥 Downloading Warren binary..."
BINARY_URL="https://github.com/doedja/warren/releases/download/${WARREN_VERSION}/warren-x86_64-unknown-linux-gnu"
BINARY_PATH="/tmp/warren-hub"

if ! curl -fL "$BINARY_URL" -o "$BINARY_PATH"; then
    echo "❌ Failed to download Warren binary"
    exit 1
fi

chmod +x "$BINARY_PATH"
echo "✅ Warren binary downloaded"

# Step 2: Create warren user and directories
echo "📁 Setting up directories..."
if ! id "$WARREN_USER" &>/dev/null; then
    sudo useradd -r -s /bin/bash -d "$WARREN_HOME" "$WARREN_USER"
    echo "✅ Created warren user"
fi

sudo mkdir -p "$WARREN_HOME"
sudo chown "$WARREN_USER:$WARREN_USER" "$WARREN_HOME"
sudo cp "$BINARY_PATH" "$WARREN_HOME/warren"
sudo chown "$WARREN_USER:$WARREN_USER" "$WARREN_HOME/warren"
echo "✅ Directories configured"

# Step 3: Create systemd service
echo "⚙️  Creating systemd service..."
sudo tee /etc/systemd/system/warren.service > /dev/null <<EOF
[Unit]
Description=Warren Residential Proxy Hub
Documentation=https://github.com/doedja/warren
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$WARREN_USER
WorkingDirectory=$WARREN_HOME
ExecStart=$WARREN_HOME/warren hub \\
  --listen 0.0.0.0:$WARREN_PORT \\
  --admin 0.0.0.0:$WARREN_ADMIN_PORT
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
SyslogIdentifier=warren

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$WARREN_HOME

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
echo "✅ Systemd service created"

# Step 4: Enable and start service
echo "🔄 Starting Warren hub..."
sudo systemctl enable warren
sudo systemctl start warren

# Wait for service to start
sleep 3

if sudo systemctl is-active --quiet warren; then
    echo "✅ Warren hub started successfully"
else
    echo "❌ Warren hub failed to start"
    sudo systemctl status warren
    exit 1
fi

# Step 5: Configure firewall
echo "🔐 Configuring firewall..."
if command -v ufw &> /dev/null; then
    sudo ufw allow $WARREN_PORT/tcp
    sudo ufw allow $WARREN_ADMIN_PORT/tcp
    echo "✅ Firewall rules added"
fi

# Step 6: Verify deployment
echo ""
echo "✅ Warren Hub Deployment Complete!"
echo ""
echo "📊 Hub Information:"
echo "  Proxy Endpoint: http://warren:PASSWORD@$HETZNER_VPS:$WARREN_PORT"
echo "  Admin Dashboard: http://$HETZNER_VPS:$WARREN_ADMIN_PORT"
echo "  Service Status: $(sudo systemctl is-active warren)"
echo ""
echo "📝 Next Steps:"
echo "  1. Generate a strong password for warren user"
echo "  2. Enroll devices as warren nodes"
echo "  3. Configure ShamrockLeads to use warren proxy"
echo ""
echo "🔗 Documentation: https://github.com/doedja/warren"
