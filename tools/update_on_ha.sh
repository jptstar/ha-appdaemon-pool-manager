#!/bin/sh
# Update Pool Manager inside Home Assistant's AppDaemon app folder from a git tag.
#
# Run it in a shell on the Home Assistant host (Terminal & SSH, or the Studio Code
# Server terminal). The Marketplace no longer manages AppDaemon apps, so this is the
# supported way to update. It does NOT restart AppDaemon: restart the app yourself
# once it prints "OK".
#
#   sh update_on_ha.sh v0.10.20
#
# Layout assumed (override with the environment variables below):
#   APPS_DIR   /config/appdaemon/apps/ha-appdaemon-pool-manager   flat folder AppDaemon loads
#   SRC_DIR    /config/appdaemon/pool-manager-src                 git clone, outside app_dir
#   BACKUP_DIR /config/appdaemon/pool-manager-backups             one copy per update
set -eu

TAG="${1:?usage: update_on_ha.sh <tag, e.g. v0.10.20>}"
APPS_DIR="${APPS_DIR:-/config/appdaemon/apps/ha-appdaemon-pool-manager}"
SRC_DIR="${SRC_DIR:-/config/appdaemon/pool-manager-src}"
BACKUP_DIR="${BACKUP_DIR:-/config/appdaemon/pool-manager-backups}"
REPO_URL="${REPO_URL:-https://github.com/jptstar/ha-appdaemon-pool-manager.git}"

[ -d "$APPS_DIR" ] || { echo "APPS_DIR not found: $APPS_DIR" >&2; exit 1; }

if [ ! -d "$SRC_DIR/.git" ]; then
    git clone --quiet "$REPO_URL" "$SRC_DIR"
fi
git -C "$SRC_DIR" fetch --quiet --tags --force origin
git -C "$SRC_DIR" checkout --quiet --detach "refs/tags/$TAG"
SRC_PY="$SRC_DIR/apps/pool_manager"
[ -f "$SRC_PY/filtration_piscine.py" ] || { echo "no filtration_piscine.py in $TAG" >&2; exit 1; }

# Refuse to install code that does not even compile.
python3 -m py_compile "$SRC_PY"/*.py
find "$SRC_PY" -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true

STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$BACKUP_DIR"
cp -a "$APPS_DIR" "$BACKUP_DIR/$STAMP"
echo "backup: $BACKUP_DIR/$STAMP"

# Copy the module files; keep anything else in the folder (local files are not touched).
for f in "$SRC_PY"/*.py; do
    cp "$f" "$APPS_DIR/$(basename "$f")"
done
echo "installed $TAG ($(git -C "$SRC_DIR" rev-parse --short HEAD)) into $APPS_DIR"
echo "OK - now restart the AppDaemon app. To roll back: copy $BACKUP_DIR/$STAMP back over $APPS_DIR."
