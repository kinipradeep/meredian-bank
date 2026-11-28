#!/usr/bin/env bash
# Build dist/meridian-range-installer.sh: one file containing the range's code + the installer.
#   scripts/make-installer.sh [git-ref]      (default: HEAD; only committed files are bundled)
# Copy the result and a filled-in range.env to the VM and run:  sudo bash meridian-range-installer.sh
set -euo pipefail
cd "$(dirname "$0")/.."
REF="${1:-HEAD}"
SHA=$(git rev-parse --short "$REF")
VERSION="$SHA ($(git log -1 --format=%cs "$REF"))"
OUT=dist/meridian-range-installer.sh
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
git archive --format=tar "$REF" | tar -x -C "$WORK"
printf '%s %s\n' "$VERSION" "$(git log -1 --format=%s "$REF")" > "$WORK/VERSION"
mkdir -p dist
sed "s|@VERSION@|$VERSION|g" scripts/installer-header.sh > "$OUT"
tar -czf - -C "$WORK" . | base64 -w 76 >> "$OUT"
chmod 755 "$OUT"
cp deploy/range.env.example dist/range.env.example
# Self-check: the bundle unpacks and contains the installer.
CHECK=$(mktemp -d); bash "$OUT" --extract "$CHECK" >/dev/null && [ -f "$CHECK/scripts/install.sh" ] && rm -rf "$CHECK"
echo "Built $OUT ($(du -h "$OUT" | cut -f1)), version $VERSION"
