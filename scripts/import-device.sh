#!/usr/bin/env bash
# Copy new recordings from a plugged-in recorder into the master inbox.
#
# The always-on web service runs as a launchd agent, which macOS TCC blocks from
# reading removable volumes (/Volumes/*) — so the panel can't auto-discover the
# device. Run this from a Terminal (which has disk access) to stage new files into
# the inbox; the panel then sees them under the inbox source.
#
#   ./scripts/import-device.sh                 # auto-pick the mounted recorder volume
#   ./scripts/import-device.sh "/Volumes/NO NAME"   # explicit source volume
#
# Only files whose basename is not already in the inbox are copied (idempotent).
set -euo pipefail

INBOX="${PCN_INBOX:-$HOME/PersonalContextNode/inbox}"

# Resolve the source volume: explicit arg, else first /Volumes entry matching the
# recorder naming patterns (DJI* / MIC* / "NO NAME").
src="${1:-}"
if [ -z "$src" ]; then
  for v in /Volumes/*; do
    name="$(basename "$v")"
    case "$name" in
      DJI*|MIC*|"NO NAME") src="$v"; break ;;
    esac
  done
fi
[ -n "$src" ] && [ -d "$src" ] || { echo "no recorder volume found (pass one explicitly: $0 '/Volumes/NAME')" >&2; exit 1; }

mkdir -p "$INBOX"
echo "Source: $src"
echo "Inbox:  $INBOX"

copied=0 skipped=0
while IFS= read -r f; do
  base="$(basename "$f")"
  if [ -e "$INBOX/$base" ]; then
    skipped=$((skipped+1))
  else
    cp -n "$f" "$INBOX/$base" && { echo "  + $base"; copied=$((copied+1)); }
  fi
done < <(find "$src" -type f \( -iname '*.wav' -o -iname '*.WAV' \) 2>/dev/null)

echo "Done: copied=$copied skipped(existing)=$skipped  →  inbox now has $(ls "$INBOX"/*.wav 2>/dev/null | wc -l | tr -d ' ') wav files."
echo "Next: open http://127.0.0.1:8765/app/ and import the inbox, or run:"
echo "  uv run pcn ingest import --source-dir \"$INBOX\" --config config/local.toml"
