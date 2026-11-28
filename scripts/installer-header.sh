#!/usr/bin/env bash
# =============================================================================
#  Meridian Bank AI Security Range: single-file installer  (version @VERSION@)
#
#  The range's code is bundled inside this file, so the VM never needs GitHub.
#  On a fresh Ubuntu 24.04 VM (Linode 8 GB recommended), as root:
#
#    1. sudo bash meridian-range-installer.sh          creates range.env next to this file
#    2. nano range.env                                 fill in your settings
#    3. sudo bash meridian-range-installer.sh          installs, or updates an existing install
#
#  Run it again with a newer installer to update; data and secrets are kept.
#  Other uses:
#    sudo bash meridian-range-installer.sh /path/to/range.env    use a settings file elsewhere
#    bash meridian-range-installer.sh --extract DIR              unpack the bundled code only
#    bash meridian-range-installer.sh --version
#
#  Built from https://github.com/kinipradeep/meredian-bank by scripts/make-installer.sh.
# =============================================================================
set -euo pipefail
VERSION="@VERSION@"
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
HERE="$(dirname "$SELF")"

extract() {   # unpack the bundled code into $1
  local line
  line=$(awk '/^__PAYLOAD_BELOW__$/ { print NR + 1; exit }' "$SELF")
  [ -n "$line" ] || { echo "This installer is damaged (no bundled code found). Copy it again."; exit 1; }
  tail -n +"$line" "$SELF" | base64 -d | tar -xz -C "$1" \
    || { echo "This installer is damaged or was cut off while copying. Copy it again."; exit 1; }
}

case "${1:-}" in
  --version) echo "$VERSION"; exit 0 ;;
  --extract) DIR="${2:?usage: $0 --extract DIR}"; mkdir -p "$DIR"; extract "$DIR"; echo "Code unpacked into $DIR"; exit 0 ;;
  -h|--help) sed -n '2,19p' "$SELF"; exit 0 ;;
esac

[ "$(id -u)" = 0 ] || { echo "Run it as root:  sudo bash $0 ${1:-}"; exit 1; }
OS=$( (. /etc/os-release 2>/dev/null && echo "$ID $VERSION_ID") || echo unknown)
[[ "$OS" == ubuntu* ]] || echo "WARNING: written for Ubuntu 24.04; this system is $OS."

TMP=$(mktemp -d /tmp/meridian-range.XXXXXX)
trap 'rm -rf "$TMP"' EXIT
extract "$TMP"

CFG="${1:-}"
if [ -z "$CFG" ]; then
  for c in "$HERE/range.env" "$PWD/range.env" /etc/airange/range.env; do
    [ -f "$c" ] && { CFG="$c"; break; }
  done
fi
if [ -z "$CFG" ]; then
  install -m 600 "$TMP/deploy/range.env.example" "$HERE/range.env"
  cat <<MSG
Created $HERE/range.env

Fill it in (at least ADMIN_EMAIL; add your Groq key to use a real model):
    nano $HERE/range.env
then run this installer again:
    sudo bash $SELF
MSG
  exit 0
fi
[ -f "$CFG" ] || { echo "Settings file not found: $CFG"; exit 1; }

# Keep a root-only copy so later runs (and updates) find the same settings.
install -d -m 755 /etc/airange
if ! [ "$CFG" -ef /etc/airange/range.env ]; then
  install -m 600 -o root -g root "$CFG" /etc/airange/range.env
  chmod 600 "$CFG" || true
fi
echo "Meridian Bank range installer $VERSION"
echo "Settings: $CFG (saved to /etc/airange/range.env)"
bash "$TMP/scripts/install.sh" --config /etc/airange/range.env --source "$TMP"
exit 0
# The bundled code (a base64 tar.gz of the repository) follows; nothing below this line is shell.
__PAYLOAD_BELOW__
