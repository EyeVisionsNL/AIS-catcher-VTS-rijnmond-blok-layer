#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ "$(id -u)" -ne 0 ]; then
  if ! command -v sudo >/dev/null 2>&1; then
    printf '%s\n' 'sudo is required. Run this script as root.' >&2
    exit 1
  fi
  exec sudo "$SCRIPT_DIR/install.sh" "$@"
fi

PLUGIN_SOURCE="$SCRIPT_DIR/vts-sectoren-rws-v17.1.pjs"
PROXY_SOURCE="$SCRIPT_DIR/rws_proxy.py"
SERVICE_SOURCE="$SCRIPT_DIR/ais-catcher-rws-relay.service"
PLUGIN_DIR=/etc/AIS-catcher/plugins
PLUGIN_PATH="$PLUGIN_DIR/vts-sectoren.pjs"
PLUGIN_BACKUP="$PLUGIN_PATH.pre-v17.1"
RELAY_DIR=/opt/ais-catcher-rws-relay
SERVICE_PATH=/etc/systemd/system/ais-catcher-rws-relay.service

for file in "$PLUGIN_SOURCE" "$PROXY_SOURCE" "$SERVICE_SOURCE"; do
  if [ ! -f "$file" ]; then
    printf 'Required package file is missing: %s\n' "$file" >&2
    exit 1
  fi
done

if ! head -n 1 "$PLUGIN_SOURCE" | grep -q 'v17.1'; then
  printf '%s\n' 'The plugin file is not the expected v17.1 release.' >&2
  exit 1
fi

if [ ! -x /usr/bin/python3 ]; then
  printf '%s\n' 'Python 3 is required at /usr/bin/python3.' >&2
  exit 1
fi
if ! command -v systemctl >/dev/null 2>&1; then
  printf '%s\n' 'systemd is required to install the RWS relay service.' >&2
  exit 1
fi

install -d -o root -g root -m 0755 "$PLUGIN_DIR" "$RELAY_DIR"
if [ -f "$PLUGIN_PATH" ] && [ ! -e "$PLUGIN_BACKUP" ]; then
  cp -a "$PLUGIN_PATH" "$PLUGIN_BACKUP"
  printf 'Previous plugin saved as %s\n' "$PLUGIN_BACKUP"
fi

install -o root -g root -m 0644 "$PLUGIN_SOURCE" "$PLUGIN_PATH"
install -o root -g root -m 0644 "$PROXY_SOURCE" "$RELAY_DIR/rws_proxy.py"
install -o root -g root -m 0644 "$SERVICE_SOURCE" "$SERVICE_PATH"

systemctl daemon-reload
systemctl enable --now ais-catcher-rws-relay.service
systemctl restart ais-catcher.service

printf '\n%s\n' 'Installed v17.1 plugin and local RWS relay.'
systemctl --no-pager --full status ais-catcher-rws-relay.service
