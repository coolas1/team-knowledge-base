#!/usr/bin/env bash
# Install the WeKnora CLI (`weknora`) — OPTIONAL.
#
# The core bench pipeline (bootstrap/ingest/health) uses curl + jq, so the CLI
# is not required. It IS needed for: (a) the `weknora` interactive commands and
# (b) `weknora mcp serve`, which the repo's .mcp.json entry spawns for in-session
# WeKnora tooling (mirrors gbrain's `gbrain serve`).
#
# WeKnora ships NO prebuilt CLI binaries (no release assets), so this builds
# from source — the CLI's go.mod requires Go 1.26.0. If Go is missing this
# installs it (to /usr/local/go or $HOME/.local/go). Set GO_VERSION to pin.
#
# Usage:
#   bash scripts/weknora/install-cli.sh
#   GO_VERSION=1.26.0 bash scripts/weknora/install-cli.sh

set -euo pipefail
source "$(dirname "$0")/_preflight.sh"

GO_VERSION="${GO_VERSION:-1.26.0}"
install_prefix="/usr/local"      # for the binary symlink (MCP-spawn PATH)

# Already installed?
if command -v weknora >/dev/null 2>&1 && weknora version >/dev/null 2>&1; then
  write_step_ok "weknora CLI already on PATH ($(command -v weknora))"
  exit 0
fi

# --- resolve a Go toolchain -------------------------------------------------

go_bin=""
if command -v go >/dev/null 2>&1; then
  go_bin="$(command -v go)"
  write_step_ok "found go: $go_bin ($(go version))"
else
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) goarch="amd64" ;;
    aarch64|arm64) goarch="arm64" ;;
    *) write_step_fail "unsupported arch $arch for the Go toolchain"; exit 2 ;;
  esac
  target="${GO_INSTALL_PREFIX:-$HOME/.local/go}"
  write_step "installing Go $GO_VERSION ($goarch) -> $target"
  tmp="$(mktemp -d)"
  url="https://go.dev/dl/go${GO_VERSION}.linux-${goarch}.tar.gz"
  if ! curl -fL --max-time 300 -o "$tmp/go.tgz" "$url"; then
    write_step_fail "failed to download $url (check GO_VERSION against https://go.dev/dl)"
    exit 3
  fi
  rm -rf "$target"
  mkdir -p "$(dirname "$target")"
  tar -C "$(dirname "$target")" -xzf "$tmp/go.tgz"
  # tar produced ./go; rename to $target basename.
  mv "$(dirname "$target")/go" "$target" 2>/dev/null || true
  rm -rf "$tmp"
  go_bin="$target/bin/go"
  write_step_ok "Go installed: $($go_bin version)"
fi
export PATH="$(dirname "$go_bin"):$PATH"

# --- build + install the CLI ------------------------------------------------

write_step "go install github.com/Tencent/WeKnora/cli@latest"
GOPATH="${GOPATH:-$HOME/go}" go install github.com/Tencent/WeKnora/cli@latest
cli_bin="$GOPATH/bin/weknora"
if [[ ! -x "$cli_bin" ]]; then
  write_step_fail "go install did not produce $cli_bin"; exit 4
fi

# Put it where the MCP spawn PATH can find it (same gotcha as gbrain: the
# installer's ~/.bun/bin isn't on the spawn PATH; symlink onto /usr/local/bin).
if [[ ":$PATH:" == *":$install_prefix/bin:"* ]] || [[ -w "$install_prefix/bin" ]]; then
  ln -sfn "$cli_bin" "$install_prefix/bin/weknora"
  write_step_ok "weknora -> $install_prefix/bin/weknora (on MCP spawn PATH)"
else
  write_step_warn "could not write $install_prefix/bin (no permission). weknora is at $cli_bin — add it to PATH, or: sudo ln -sfn $cli_bin $install_prefix/bin/weknora"
fi

weknora version || write_step_warn "weknora version check failed"
write_step_ok "CLI install done. Authenticate via: bash scripts/weknora/connect.sh"
