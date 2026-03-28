#!/usr/bin/env bash
set -euo pipefail

APP_USER="${APP_USER:-deadlinebot}"
APP_GROUP="${APP_GROUP:-$APP_USER}"
APP_ROOT="${APP_ROOT:-/opt/deadline-bot}"
APP_DIR="${APP_DIR:-$APP_ROOT/app}"
VENV_DIR="${VENV_DIR:-$APP_ROOT/.venv}"
STATE_DIR="${STATE_DIR:-/var/lib/deadline-bot}"
ENV_DIR="${ENV_DIR:-/etc/deadline-bot}"
ENV_FILE="${ENV_FILE:-$ENV_DIR/deadline-bot.env}"
SERVICE_NAME="${SERVICE_NAME:-deadline-bot}"
SERVICE_SOURCE="${SERVICE_SOURCE:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/deadline-bot.service}"
SUDOERS_FILE="/etc/sudoers.d/${APP_USER}-${SERVICE_NAME}"
SYSTEMCTL_BIN="/usr/bin/systemctl"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script as root: sudo bash deploy/install_service.sh"
  exit 1
fi

apt-get update
apt-get install -y python3 python3-venv rsync

if ! id -u "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --shell /bin/bash "${APP_USER}"
fi

mkdir -p "${APP_ROOT}" "${APP_DIR}" "${STATE_DIR}" "${ENV_DIR}"
chown -R "${APP_USER}:${APP_GROUP}" "${APP_ROOT}" "${STATE_DIR}"
install -d -m 700 -o "${APP_USER}" -g "${APP_GROUP}" "/home/${APP_USER}/.ssh"

if [[ ! -d "${VENV_DIR}" ]]; then
  sudo -u "${APP_USER}" python3 -m venv "${VENV_DIR}"
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  cat > "${ENV_FILE}" <<EOF
TOKEN=replace_me
CHANNEL_ID=@replace_me
WHITELIST_USER_IDS=123456789
DEADLINES_STORAGE_PATH=${STATE_DIR}/deadlines.json
EOF
  chmod 600 "${ENV_FILE}"
fi

install -m 644 "${SERVICE_SOURCE}" "/etc/systemd/system/${SERVICE_NAME}.service"

cat > "${SUDOERS_FILE}" <<EOF
${APP_USER} ALL=(root) NOPASSWD: ${SYSTEMCTL_BIN} restart ${SERVICE_NAME}, ${SYSTEMCTL_BIN} status ${SERVICE_NAME}, ${SYSTEMCTL_BIN} is-active ${SERVICE_NAME}
EOF
chmod 440 "${SUDOERS_FILE}"

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"

echo "Server bootstrap is complete."
echo "Next steps:"
echo "1. Edit ${ENV_FILE}"
echo "2. Add an SSH key to /home/${APP_USER}/.ssh/authorized_keys"
echo "3. Run the first deploy from GitHub Actions or manually"
