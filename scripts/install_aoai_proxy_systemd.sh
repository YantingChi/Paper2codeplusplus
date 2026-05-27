#!/usr/bin/env bash
# Installs the local Azure OpenAI proxy and systemd service.
# Sample usage:
# sudo bash scripts/install_aoai_proxy_systemd.sh
# sudoedit /etc/aoai-proxy.env
# sudo systemctl enable --now aoai-proxy
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROXY_SOURCE="${PROXY_SOURCE:-$REPO_ROOT/tools/aoai_proxy.py}"
SERVICE_SOURCE="${SERVICE_SOURCE:-$REPO_ROOT/deploy/systemd/aoai-proxy.service}"
INSTALL_DIR="${INSTALL_DIR:-/opt/aoai-proxy}"
INSTALLED_PROXY="${INSTALLED_PROXY:-$INSTALL_DIR/aoai_proxy.py}"
SYSTEMD_SERVICE_PATH="${SYSTEMD_SERVICE_PATH:-/etc/systemd/system/aoai-proxy.service}"
ENV_FILE="${ENV_FILE:-/etc/aoai-proxy.env}"
CURRENT_STEP="initialization"

on_error() {
    local status=$?
    local failed_command=${BASH_COMMAND:-unknown}
    echo "[install_aoai_proxy_systemd] ERROR during ${CURRENT_STEP}: exit status ${status}" >&2
    echo "[install_aoai_proxy_systemd] Last command: ${failed_command}" >&2
    exit "$status"
}
trap on_error ERR

# Validate privileges and source files before installing anything.
CURRENT_STEP="preflight"
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "Error: run this installer as root, for example: sudo bash scripts/install_aoai_proxy_systemd.sh" >&2
    exit 1
fi
if [[ ! -f "$PROXY_SOURCE" ]]; then
    echo "Error: proxy source not found: $PROXY_SOURCE" >&2
    exit 1
fi
if [[ ! -f "$SERVICE_SOURCE" ]]; then
    echo "Error: service source not found: $SERVICE_SOURCE" >&2
    exit 1
fi

# Install the proxy script and service unit with root ownership.
CURRENT_STEP="install files"
install -o root -g root -m 755 -d "$INSTALL_DIR"
install -o root -g root -m 755 "$PROXY_SOURCE" "$INSTALLED_PROXY"
install -o root -g root -m 644 "$SERVICE_SOURCE" "$SYSTEMD_SERVICE_PATH"

# Create a locked-down env template when one is not already present.
CURRENT_STEP="create env template"
if [[ ! -e "$ENV_FILE" ]]; then
    install -o root -g root -m 600 /dev/null "$ENV_FILE"
    tee "$ENV_FILE" >/dev/null <<'EOF'
# Consumed by /etc/systemd/system/aoai-proxy.service.
# Example override:
#   AOAI_PROXY_PORT=8788
AOAI_PROXY_UPSTREAM_ENDPOINT=https://your-resource.openai.azure.com/
AOAI_PROXY_UPSTREAM_API_KEY=replace-me
AOAI_PROXY_LISTEN_HOST=127.0.0.1
AOAI_PROXY_PORT=8787
AOAI_PROXY_TIMEOUT_SECONDS=600
EOF
    chmod 600 "$ENV_FILE"
fi

# Reload systemd so the installed service is available.
CURRENT_STEP="systemd reload"
systemctl daemon-reload

cat <<EOF
Installed AOAI proxy files:
  proxy:   $INSTALLED_PROXY
  service: $SYSTEMD_SERVICE_PATH
  env:     $ENV_FILE

Next steps:
  sudoedit $ENV_FILE
  sudo systemctl enable --now aoai-proxy
  curl http://127.0.0.1:8787/healthz
EOF
