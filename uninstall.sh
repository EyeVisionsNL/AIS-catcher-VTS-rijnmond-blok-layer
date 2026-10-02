#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
  if ! command -v sudo >/dev/null 2>&1; then
    printf '%s\n' 'sudo is required. Run this script as root.' >&2
    exit 1
  fi
  exec sudo "$SCRIPT_DIR/uninstall.sh" "$@"
fi

PLUGIN_PATH=/etc/AIS-catcher/plugins/vts-sectoren.pjs
PLUGIN_BACKUP="$PLUGIN_PATH.pre-v17.1"
SERVICE_PATH=/etc/systemd/system/ais-catcher-rws-relay.service

systemctl disable --now ais-catcher-rws-relay.service 2>/dev/null || true
rm -f "$SERVICE_PATH"
rm -rf /opt/ais-catcher-rws-relay
systemctl daemon-reload

if [ -f "$PLUGIN_BACKUP" ]; then
  install -d -o root -g root -m 0755 "$(dirname -- "$PLUGIN_PATH")"
  mv -f "$PLUGIN_BACKUP" "$PLUGIN_PATH"
  printf '%s\n' 'Previous AIS-Catcher plugin restored.'
elif [ -f "$PLUGIN_PATH" ] && head -n 1 "$PLUGIN_PATH" | grep -q 'v17.1'; then
  rm -f "$PLUGIN_PATH"
  printf '%s\n' 'v17.1 AIS-Catcher plugin removed.'
fi

if systemctl cat ais-catcher.service >/dev/null 2>&1; then
  systemctl restart ais-catcher.service
fi

printf '%s\n' 'RWS relay service removed.'
