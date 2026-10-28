#!/usr/bin/env bash
# Fetch the Sourcegraph `src` CLI into tools/bin/ (not committed: ~63 MB).
#
# The CLI is a static Go binary, so this needs no sudo, no Docker and no
# package manager. It is how ReviewMind performs semantic code navigation
# against sourcegraph.com -- see docs/TOOL_COVERAGE.md for why we do not
# self-host (the sourcegraph/server image is amd64-only).
set -euo pipefail

VERSION="${SRC_CLI_VERSION:-8.0.0}"
DEST="$(cd "$(dirname "$0")/.." && pwd)/tools/bin"

case "$(uname -s)" in
  Linux)  OS=linux ;;
  Darwin) OS=darwin ;;
  *) echo "Unsupported OS: $(uname -s)" >&2; exit 1 ;;
esac

case "$(uname -m)" in
  x86_64|amd64)  ARCH=amd64 ;;
  aarch64|arm64) ARCH=arm64 ;;
  *) echo "Unsupported arch: $(uname -m)" >&2; exit 1 ;;
esac

URL="https://github.com/sourcegraph/src-cli/releases/download/${VERSION}/src-cli_${VERSION}_${OS}_${ARCH}.tar.gz"

echo "Fetching src-cli ${VERSION} for ${OS}/${ARCH}..."
mkdir -p "$DEST"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

curl -fsSL --max-time 300 -o "$TMP/src-cli.tar.gz" "$URL"
tar -xzf "$TMP/src-cli.tar.gz" -C "$DEST"
chmod +x "$DEST/src"

echo "Installed: $DEST/src"
"$DEST/src" version || true

cat <<'EOT'

Usage:
  export SRC_ENDPOINT=https://sourcegraph.com
  # optional, for private code:
  # export SRC_ACCESS_TOKEN=<token>

  .venv/bin/python experiments/sourcegraph_navigation.py
EOT
