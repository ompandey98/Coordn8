#!/usr/bin/env bash
# Coordn8 installer — drops the right adapter into the tool you choose, or all.
# Author: pandeom.  Zero-dependency (bash + coreutils).
#
#   ./install.sh                 # interactive: pick a target
#   ./install.sh claude          # Claude Code skill + /coordn8 command (user scope)
#   ./install.sh cursor          # Cursor rule into ./.cursor/rules
#   ./install.sh codex           # AGENTS.md into current dir
#   ./install.sh kiro            # Kiro steering doc into ./.kiro/steering
#   ./install.sh generic         # copy the paste-anywhere prompt here
#   ./install.sh all             # everything applicable
#
# It always regenerates adapters from PLAYBOOK.md first, so you install the
# CURRENT brain — never a stale copy.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(cat "$REPO_DIR/VERSION" 2>/dev/null || echo '?')"
ADAPTERS="$REPO_DIR/adapters"

say() { printf '\033[36m[coordn8]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[coordn8]\033[0m %s\n' "$*"; }

regen() {
  if command -v python3 >/dev/null 2>&1; then
    python3 "$REPO_DIR/build/generate_adapters.py" >/dev/null
    say "adapters regenerated from PLAYBOOK.md (v$VERSION)"
  else
    warn "python3 not found — installing whatever adapters are already present"
  fi
}

install_claude() {
  # MONOLITHIC install: the full brain is embedded directly in SKILL.md and the
  # /coordn8 command file (never a slim pointer — that would be inferior to the
  # raw prompt). We also delete any leftovers from the short-lived split layout
  # (a sibling PLAYBOOK.md / coordn8-brain.md) so an upgrade never leaves a
  # stale, half-working install behind.
  local dst="${CLAUDE_HOME:-$HOME/.claude}"
  mkdir -p "$dst/skills/coordn8" "$dst/commands"
  rm -f "$dst/skills/coordn8/PLAYBOOK.md" "$dst/commands/coordn8-brain.md"
  cp "$ADAPTERS/claude-code/SKILL.md"            "$dst/skills/coordn8/SKILL.md"
  cp "$ADAPTERS/claude-code/commands/coordn8.md" "$dst/commands/coordn8.md"
  say "Claude Code: skill -> $dst/skills/coordn8/  |  command -> /coordn8"
}

install_cursor() {
  local dst="$PWD/.cursor/rules"
  mkdir -p "$dst"
  cp "$ADAPTERS/cursor/coordn8.mdc" "$dst/coordn8.mdc"
  say "Cursor: rule -> $dst/coordn8.mdc  (invoke with @coordn8)"
}

install_codex() {
  cp "$ADAPTERS/codex/AGENTS.md" "$PWD/AGENTS.md"
  say "Codex: -> $PWD/AGENTS.md"
}

install_kiro() {
  local dst="$PWD/.kiro/steering"
  mkdir -p "$dst"
  cp "$ADAPTERS/kiro/coordn8.md" "$dst/coordn8.md"
  say "Kiro: steering -> $dst/coordn8.md"
}

install_generic() {
  cp "$ADAPTERS/generic/COORDN8_PROMPT.md" "$PWD/COORDN8_PROMPT.md"
  say "Generic: paste-anywhere prompt -> $PWD/COORDN8_PROMPT.md"
}

uninstall_all() {
  local claude_home="${CLAUDE_HOME:-$HOME/.claude}"
  rm -rf "$claude_home/skills/coordn8"
  rm -f "$claude_home/commands/coordn8.md"
  # Older Coordn8 layouts (pre-2026-08) shipped a sibling `coordn8-brain.md`
  # under `commands/`. Remove it if present so an upgrade doesn't leave a
  # stray `/coordn8-brain` slash command floating around.
  rm -f "$claude_home/commands/coordn8-brain.md"
  rm -f "$PWD/.cursor/rules/coordn8.mdc"
  rm -f "$PWD/.kiro/steering/coordn8.md"
  rm -f "$PWD/COORDN8_PROMPT.md"
  # AGENTS.md may hold the user's own content — only remove if it's ours.
  if [ -f "$PWD/AGENTS.md" ] && grep -q 'COORDN8:BRAIN:BEGIN' "$PWD/AGENTS.md" 2>/dev/null; then
    rm -f "$PWD/AGENTS.md"
    say "removed Coordn8-generated AGENTS.md"
  elif [ -f "$PWD/AGENTS.md" ]; then
    warn "left AGENTS.md in place (it doesn't look Coordn8-generated — remove by hand if you want)"
  fi
  say "uninstalled: Claude skill/command (user scope) + Cursor/Kiro/generic files in this dir"
}

target="${1:-}"
if [ -z "$target" ]; then
  # Only prompt when we actually have an interactive terminal. In a
  # non-interactive shell (CI, an agent, a piped `npx … | bash`) `read` would
  # hang forever waiting for input that never comes — so default to `all`.
  if [ -t 0 ]; then
    echo "Coordn8 v$VERSION installer — where do you want it?"
    echo "  1) Claude Code   2) Cursor   3) Codex   4) Kiro   5) Generic   6) All   7) Uninstall"
    read -rp "choice [1-7]: " c
    case "$c" in
      1) target=claude;; 2) target=cursor;; 3) target=codex;;
      4) target=kiro;; 5) target=generic;; 6) target=all;; 7) target=uninstall;;
      *) warn "unknown choice"; exit 1;;
    esac
  else
    say "non-interactive shell detected — installing ALL adapters (pass a target to narrow)"
    target=all
  fi
fi

if [ "$target" = "uninstall" ]; then
  uninstall_all
  exit 0
fi

regen
case "$target" in
  claude) install_claude;;
  cursor) install_cursor;;
  codex) install_codex;;
  kiro) install_kiro;;
  generic) install_generic;;
  all) install_claude; install_cursor; install_codex; install_kiro; install_generic;;
  *) warn "unknown target '$target' (use claude|cursor|codex|kiro|generic|all|uninstall)"; exit 1;;
esac

say "done. Open a FRESH session at your repo root, then invoke Coordn8."
