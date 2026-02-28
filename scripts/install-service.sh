#!/usr/bin/env bash
#
# Install pixel-hawk as a systemd service.
#
# Supports a split-user setup: a deploy user owns the code and runs
# dependency management, while a dedicated service user (no shell, no
# home) runs the application at runtime.
#
# Detects the current (deploy) user, repo location, and uv path at
# install time to generate a portable service unit. Safe to re-run.
#
# Options:
#   --service-user USER   OS user that runs the service (default: pixel-hawk)
#   --nest DIR            data directory path (default: ./nest)
#
# Usage:
#   bash scripts/install-service.sh
#   bash scripts/install-service.sh --service-user pixel-hawk --nest /var/local/pixel-hawk/nest
#

set -euo pipefail

SERVICE_NAME="pixel-hawk"
SERVICE_USER="pixel-hawk"
NEST_DIR=""

# --- Parse arguments ---
while [[ $# -gt 0 ]]; do
    case "$1" in
        --service-user) SERVICE_USER="$2"; shift 2 ;;
        --nest)         NEST_DIR="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# --- Resolve environment ---
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY_USER="$(whoami)"
DEPLOY_HOME="$(eval echo "~${DEPLOY_USER}")"

if [[ -z "${NEST_DIR}" ]]; then
    NEST_DIR="${REPO_DIR}/nest"
fi

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

# Determine if we're using a separate service user
SAME_USER=false
if [[ "${SERVICE_USER}" == "${DEPLOY_USER}" ]]; then
    SAME_USER=true
fi

echo "=== ${SERVICE_NAME} service installer ==="
echo ""
echo "  repo:          ${REPO_DIR}"
echo "  nest:          ${NEST_DIR}"
echo "  deploy user:   ${DEPLOY_USER}"
echo "  service user:  ${SERVICE_USER}"
echo "  uv:            ${UV}"
echo ""

# --- Step 1: Sync dependencies ---
echo "[1/5] Syncing dependencies with uv..."
cd "${REPO_DIR}"
"${UV}" sync --quiet
echo "  done"

# --- Step 2: Create service user (if separate) ---
if [[ "${SAME_USER}" == false ]]; then
    echo "[2/5] Ensuring service user '${SERVICE_USER}' exists..."
    if id "${SERVICE_USER}" &>/dev/null; then
        echo "  already exists"
    else
        sudo useradd --system --no-create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
        echo "  created"
    fi
else
    echo "[2/5] Service user is deploy user, skipping creation"
fi

# --- Step 3: Create nest directories ---
echo "[3/5] Ensuring nest directory structure..."
sudo mkdir -p "${NEST_DIR}"/{projects,tiles,snapshots,logs,data,rejected}
sudo chown -R "${SERVICE_USER}:" "${NEST_DIR}"
echo "  done"

# --- Step 4: Generate and install systemd unit ---
echo "[4/5] Installing systemd service..."

# Build ReadWritePaths depending on user setup
READ_WRITE_PATHS="${NEST_DIR}"
if [[ "${SAME_USER}" == false ]]; then
    # Service user needs read access to repo .venv, deploy user's uv python installs
    READ_WRITE_PATHS="${NEST_DIR} ${REPO_DIR}/.venv"
fi

# Build environment lines
ENV_LINES="Environment=PYTHONUNBUFFERED=1"
if [[ "${SAME_USER}" == false ]]; then
    ENV_LINES="${ENV_LINES}
Environment=UV_PYTHON_INSTALL_DIR=${DEPLOY_HOME}/.local/share/uv/python"
fi

# Determine ProtectHome setting
if [[ "${SAME_USER}" == false ]]; then
    # Service user has no home; needs read access to deploy user's home for code + uv
    PROTECT_HOME="ProtectHome=false"
else
    PROTECT_HOME="ProtectHome=false"
fi

sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=Pixel Hawk - WPlace paint project change tracker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${REPO_DIR}
ExecStart=${UV} run hawk --nest ${NEST_DIR}
KillSignal=SIGINT
TimeoutStopSec=15
Restart=on-failure
RestartSec=30
${ENV_LINES}
CacheDirectory=${SERVICE_NAME}
Environment=UV_CACHE_DIR=/var/cache/${SERVICE_NAME}

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
${PROTECT_HOME}
ReadWritePaths=${READ_WRITE_PATHS}
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

# --- Step 5: Enable and start ---
echo "[5/5] Enabling and starting service..."
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
