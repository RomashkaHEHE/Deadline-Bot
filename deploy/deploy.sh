#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/deadline-bot/app}"
VENV_DIR="${VENV_DIR:-/opt/deadline-bot/.venv}"
SERVICE_NAME="${SERVICE_NAME:-deadline-bot}"
RELEASE_ARCHIVE="${RELEASE_ARCHIVE:-/tmp/deadline-bot-release.tar.gz}"

if [[ ! -f "${RELEASE_ARCHIVE}" ]]; then
  echo "Release archive not found: ${RELEASE_ARCHIVE}"
  exit 1
fi

mkdir -p "${APP_DIR}"

TMP_DIR="$(mktemp -d)"
cleanup() {
  rm -rf "${TMP_DIR}"
  rm -f "${RELEASE_ARCHIVE}" || true
}
trap cleanup EXIT

tar -xzf "${RELEASE_ARCHIVE}" -C "${TMP_DIR}"

rsync -a --delete \
  --exclude '.env' \
  --exclude '__pycache__' \
  --exclude '.venv' \
  "${TMP_DIR}/" "${APP_DIR}/"

if [[ ! -d "${VENV_DIR}" ]]; then
  python3 -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/python" -m pip install -r "${APP_DIR}/requirements.txt"
"${VENV_DIR}/bin/python" -m compileall "${APP_DIR}/app.py" "${APP_DIR}/bot_messages.py" "${APP_DIR}/tools.py"

sudo /usr/bin/systemctl restart "${SERVICE_NAME}"
sudo /usr/bin/systemctl is-active "${SERVICE_NAME}" >/dev/null

echo "Deploy finished successfully."
