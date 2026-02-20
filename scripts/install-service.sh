#!/usr/bin/env bash
#
# Install pixel-hawk as a systemd service.
#
# Detects the current user, repo location, and uv path at install time
# to generate a portable service unit. Safe to re-run (idempotent).
#
# Usage:
#   bash scripts/install-service.sh
#

set -euo pipefail

SERVICE_NAME="pixel-hawk"

# --- Resolve environment ---
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
NEST_DIR="${REPO_DIR}/nest"
RUN_USER="$(whoami)"
RUN_GROUP="$(id -gn)"
RUN_HOME="$(eval echo "~${RUN_USER}")"

UV="$(command -v uv 2>/dev/null || true)"
if [[ -z "${UV}" ]]; then
    echo "ERROR: uv not found on PATH"
    echo "Install it: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

if [[ ! -f "${REPO_DIR}/pyproject.toml" ]]; then
    echo "ERROR: pyproject.toml not found in ${REPO_DIR}"
    echo "Run this script from inside the pixel-hawk repo."
    exit 1
fi

echo "=== ${SERVICE_NAME} service installer ==="
echo ""
echo "  repo:  ${REPO_DIR}"
echo "  nest:  ${NEST_DIR}"
echo "  user:  ${RUN_USER}"
echo "  uv:    ${UV}"
echo ""

# --- Step 1: Sync dependencies ---
echo "[1/4] Syncing dependencies with uv..."
cd "${REPO_DIR}"
"${UV}" sync --quiet
echo "  done"

# --- Step 2: Create nest directories ---
echo "[2/4] Ensuring nest directory structure..."
mkdir -p "${NEST_DIR}"/{projects,tiles,snapshots,logs,data,rejected}
echo "  done"

# --- Step 3: Generate and install systemd unit ---
echo "[3/4] Installing systemd service..."

sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=Pixel Hawk - WPlace paint project change tracker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${REPO_DIR}
ExecStart=${UV} run hawk --nest ${NEST_DIR}
KillSignal=SIGINT
TimeoutStopSec=15
Restart=on-failure
RestartSec=30
Environment=PYTHONUNBUFFERED=1

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=false
ReadWritePaths=${NEST_DIR} ${REPO_DIR}/.venv ${RUN_HOME}/.local/share/uv ${RUN_HOME}/.cache/uv
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictRealtime=true
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
echo "  done"

# --- Step 4: Enable and start ---
echo "[4/4] Enabling and starting service..."
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"
echo "  done"

echo ""
echo "=== Installation complete ==="
echo ""
echo "  status:   sudo systemctl status ${SERVICE_NAME}"
echo "  journal:  sudo journalctl -u ${SERVICE_NAME} -f"
echo "  logs:     tail -f ${NEST_DIR}/logs/pixel-hawk.log"
echo "  restart:  sudo systemctl restart ${SERVICE_NAME}"
echo "  stop:     sudo systemctl stop ${SERVICE_NAME}"
echo ""

sudo systemctl status "${SERVICE_NAME}" --no-pager || true
