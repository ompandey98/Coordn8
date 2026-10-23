#!/usr/bin/env bash
# Coordn8 installer — drops the right adapter into the tool you choose, or all.
# Author: pandeom.
#
#   ./install.sh                 # interactive on a terminal; `all` when not a terminal
#   ./install.sh claude          # Claude Code skill + /coordn8 command (user scope)
#   ./install.sh cursor          # Cursor rule into ./.cursor/rules
#   ./install.sh codex           # Coordn8 block merged into ./AGENTS.md (never overwritten)
#   ./install.sh kiro            # Kiro steering doc into ./.kiro/steering
#   ./install.sh generic         # copy the paste-anywhere prompt here
#   ./install.sh all             # everything applicable
#   ./install.sh uninstall       # remove what Coordn8 installed (add --purge --yes to also
#                                #   delete ~/.coordn8 — it lists what it would delete first)
#   ./install.sh <target> --no-deps --no-voices --no-mcp --no-refs --yes ...
#
# With python3 (>= 3.8) this is a thin wrapper around scripts/install/installer.py, which
# installs the package files AND the dependencies, voices, MCP servers and references, in
# that order, never waits on input, backs up and block-merges any file that is yours, and
# records everything in ~/.coordn8/installed.json so uninstall removes exactly that.
#
# Without python3 a bash fallback installs the adapters only. It still never overwrites a
# file of yours: AGENTS.md (and any existing user file) gets a delimited
#   <!-- COORDN8:BEGIN v<ver> --> ... <!-- COORDN8:END -->
# block appended or replaced, after a backup copy is taken into ~/.coordn8/backups/.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(cat "$REPO_DIR/VERSION" 2>/dev/null || echo '?')"
ADAPTERS="$REPO_DIR/adapters"
INSTALLER="$REPO_DIR/scripts/install/installer.py"

say() { printf '\033[36m[coordn8]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[coordn8]\033[0m %s\n' "$*"; }

have_python() {
  command -v python3 >/dev/null 2>&1 \
    && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1 \
    && [ -f "$INSTALLER" ]
}

target=""
if [ "$#" -gt 0 ] && [ "${1#-}" = "$1" ]; then
  target="$1"
  shift
fi

if [ -z "$target" ]; then
  # Only prompt when we actually have an interactive terminal. In a non-interactive shell
  # (CI, an agent, a piped `npx … | bash`) `read` would hang forever — so default to `all`.
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

case "$target" in
  claude|cursor|codex|kiro|generic|all|uninstall|status) ;;
  *) warn "unknown target '$target' (use claude|cursor|codex|kiro|generic|all|uninstall)"; exit 1;;
esac

# ------------------------------------------------------------------ python path
if have_python; then
  case "$target" in
    uninstall) exec python3 "$INSTALLER" uninstall "$@";;
    status)    exec python3 "$INSTALLER" status "$@";;
    *)         exec python3 "$INSTALLER" init --target "$target" "$@";;
  esac
fi

# --------------------------------------------------------------- bash fallback
warn "python3 (>= 3.8) not found — bash fallback: adapters only (no dependencies, voices or MCP servers)"
if [ "$#" -gt 0 ]; then
  warn "options ignored without python3: $*"
fi

C8_HOME="${COORDN8_HOME:-$HOME/.coordn8}"
STAMP="$(date +%Y%m%d-%H%M%S)-$$"
BEGIN_LINE="<!-- COORDN8:BEGIN v$VERSION -->"
END_LINE="<!-- COORDN8:END -->"
BEGIN_RE='^<!-- COORDN8:BEGIN v.* -->$'
MANIFEST_ROWS=""
BLOCK_ROWS=""

sha_of() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else echo ""; fi
}

json_str() { printf '"%s"' "$(printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')"; }

abspath() { (cd "$(dirname "$1")" && printf '%s/%s' "$(pwd)" "$(basename "$1")"); }

backup_file() {  # copy a file into ~/.coordn8/backups/<stamp>/ before it is changed
  local f="$1" abs mangled dest
  [ -f "$f" ] || return 0
  abs="$(abspath "$f")"
  mangled="$(printf '%s' "${abs#/}" | sed -e 's#/#__#g')"
  dest="$C8_HOME/backups/$STAMP/$mangled"
  mkdir -p "$C8_HOME/backups/$STAMP"
  cp -p "$f" "$dest"
  say "backed up $f -> $dest"
}

note_file() { MANIFEST_ROWS="$MANIFEST_ROWS$(json_str "$(abspath "$1")"): $(json_str "$(sha_of "$1")"),"; }
note_block() { BLOCK_ROWS="$BLOCK_ROWS{\"file\": $(json_str "$(abspath "$1")"), \"style\": \"html\", \"target\": \"$2\"},"; }

ends_without_newline() { [ -s "$1" ] && [ -n "$(tail -c 1 "$1")" ]; }

merge_block() {  # merge_block <file> <content-file> <target>: insert/replace ONLY our block
  local f="$1" content="$2" tgt="$3" blk tmp
  blk="$(mktemp)"; tmp="$(mktemp)"
  { printf '%s\n' "$BEGIN_LINE"; cat "$content"; ends_without_newline "$content" && printf '\n'; printf '%s\n' "$END_LINE"; } > "$blk"
  if [ ! -s "$f" ]; then
    mkdir -p "$(dirname "$f")"
    cp "$blk" "$f"
  elif grep -q "$BEGIN_RE" "$f"; then
    if ! awk -v b="$BEGIN_RE" 'found==0 && $0 ~ b {found=1; next} found==1 && $0=="<!-- COORDN8:END -->" {ok=1; exit} END {exit ok?0:1}' "$f"; then
      warn "$f has a COORDN8:BEGIN marker with no matching END — refusing to guess; left untouched"
      rm -f "$blk" "$tmp"; return 1
    fi
    backup_file "$f"
    awk -v b="$BEGIN_RE" -v blockfile="$blk" '
      done==0 && inb==0 && $0 ~ b { inb=1; while ((getline l < blockfile) > 0) print l; next }
      inb==1 && $0=="<!-- COORDN8:END -->" { inb=0; done=1; next }
      inb==1 { next }
      { print }' "$f" > "$tmp"
    cat "$tmp" > "$f"
  else
    backup_file "$f"
    { ends_without_newline "$f" && printf '\n'; printf '\n'; cat "$blk"; } >> "$f"
  fi
  rm -f "$blk" "$tmp"
  note_block "$f" "$tgt"
}

put_owned() {  # put_owned <src> <dst>: a package-owned file; back up anything different first
  local src="$1" dst="$2"
  mkdir -p "$(dirname "$dst")"
  if [ -f "$dst" ] && ! cmp -s "$src" "$dst"; then backup_file "$dst"; fi
  cp "$src" "$dst"
  note_file "$dst"
}

put_project() {  # put_project <src> <dst> <target>: replace only if absent or ours, else merge
  local src="$1" dst="$2" tgt="$3"
  if [ ! -e "$dst" ]; then put_owned "$src" "$dst"; return; fi
  if grep -q "$BEGIN_RE" "$dst"; then merge_block "$dst" "$src" "$tgt"; return; fi
  if grep -q 'COORDN8:BRAIN:BEGIN' "$dst"; then put_owned "$src" "$dst"; return; fi
  say "$dst is your file — merging a Coordn8 block instead of replacing it"
  merge_block "$dst" "$src" "$tgt"
}

install_claude() {
  # MONOLITHIC install: the full brain is embedded directly in SKILL.md and the /coordn8
  # command file (never a slim pointer). Leftovers from the short-lived split layout are removed.
  local dst="${CLAUDE_HOME:-$HOME/.claude}"
  mkdir -p "$dst/skills/coordn8" "$dst/commands"
  rm -f "$dst/skills/coordn8/PLAYBOOK.md" "$dst/commands/coordn8-brain.md"
  put_owned "$ADAPTERS/claude-code/SKILL.md"            "$dst/skills/coordn8/SKILL.md"
  put_owned "$ADAPTERS/claude-code/commands/coordn8.md" "$dst/commands/coordn8.md"
  say "Claude Code: skill -> $dst/skills/coordn8/  |  command -> /coordn8"
}

install_cursor() {
  put_project "$ADAPTERS/cursor/coordn8.mdc" "$PWD/.cursor/rules/coordn8.mdc" cursor
  say "Cursor: rule -> $PWD/.cursor/rules/coordn8.mdc  (invoke with @coordn8)"
}

install_codex() {
  local f="$PWD/AGENTS.md"
  # A file that is nothing but an older Coordn8 adapter (no block, our first line) is ours:
  # back it up and rewrite it as the block form. Anything else is the user's: merge only.
  if [ -f "$f" ] && ! grep -q "$BEGIN_RE" "$f" && grep -q 'COORDN8:BRAIN:BEGIN' "$f" \
     && [ "$(head -n 1 "$f")" = "$(head -n 1 "$ADAPTERS/codex/AGENTS.md")" ]; then
    backup_file "$f"
    rm -f "$f"
  fi
  merge_block "$f" "$ADAPTERS/codex/AGENTS.md" codex
  say "Codex: Coordn8 block -> $f  (your own content, if any, is untouched)"
}

install_kiro() {
  put_project "$ADAPTERS/kiro/coordn8.md" "$PWD/.kiro/steering/coordn8.md" kiro
  say "Kiro: steering -> $PWD/.kiro/steering/coordn8.md"
}

install_generic() {
  put_project "$ADAPTERS/generic/COORDN8_PROMPT.md" "$PWD/COORDN8_PROMPT.md" generic
  say "Generic: paste-anywhere prompt -> $PWD/COORDN8_PROMPT.md"
}

remove_block() {  # remove_block <file>: drop our block (+ the blank separator); delete if empty
  local f="$1" tmp
  [ -f "$f" ] && grep -q "$BEGIN_RE" "$f" || return 1
  backup_file "$f"
  tmp="$(mktemp)"
  awk -v b="$BEGIN_RE" '
    { lines[NR]=$0 }
    END {
      s=0; e=0
      for (i=1;i<=NR;i++) { if (s==0 && lines[i] ~ b) s=i; else if (s>0 && lines[i]=="<!-- COORDN8:END -->") { e=i; break } }
      if (s==0 || e==0) { for (i=1;i<=NR;i++) print lines[i]; exit }
      last=s-1
      if (e==NR && last>=1 && lines[last] ~ /^[ \t\r]*$/) last=last-1
      for (i=1;i<=last;i++) print lines[i]
      for (i=e+1;i<=NR;i++) print lines[i]
    }' "$f" > "$tmp"
  if [ -z "$(tr -d ' \t\r\n' < "$tmp")" ]; then rm -f "$f"; else cat "$tmp" > "$f"; fi
  rm -f "$tmp"
  return 0
}

uninstall_all() {
  local claude_home="${CLAUDE_HOME:-$HOME/.claude}" f
  for f in "$claude_home/skills/coordn8/SKILL.md" "$claude_home/commands/coordn8.md" \
           "$PWD/.cursor/rules/coordn8.mdc" "$PWD/.kiro/steering/coordn8.md" "$PWD/COORDN8_PROMPT.md"; do
    [ -f "$f" ] || continue
    if grep -q "$BEGIN_RE" "$f"; then remove_block "$f" && say "removed the Coordn8 block from $f"
    elif grep -q 'COORDN8:BRAIN:BEGIN' "$f"; then rm -f "$f"; say "removed $f"
    else warn "left $f in place (it does not look like a Coordn8 file)"; fi
  done
  rm -f "$claude_home/skills/coordn8/PLAYBOOK.md" "$claude_home/commands/coordn8-brain.md"
  rmdir "$claude_home/skills/coordn8" 2>/dev/null || true
  f="$PWD/AGENTS.md"
  if [ -f "$f" ]; then
    if grep -q "$BEGIN_RE" "$f"; then remove_block "$f" && say "removed the Coordn8 block from AGENTS.md (your text kept)"
    elif grep -q 'COORDN8:BRAIN:BEGIN' "$f" && [ "$(head -n 1 "$f")" = "$(head -n 1 "$ADAPTERS/codex/AGENTS.md")" ]; then
      rm -f "$f"; say "removed Coordn8-generated AGENTS.md"
    else warn "left AGENTS.md in place (it doesn't look Coordn8-generated)"; fi
  fi
  rm -f "$C8_HOME/installed.json"
  say "uninstalled: Claude skill/command (user scope) + Cursor/Kiro/Codex/generic files in this dir. ~/.coordn8 data kept."
}

write_manifest() {
  local prev="(none)" f="$C8_HOME/installed.json"
  local pv=""
  if [ -f "$f" ]; then
    pv="$(sed -n 's/^ *"version": *"\([^"]*\)".*/\1/p' "$f" | head -n 1)"
  fi
  [ -n "$pv" ] && prev="v$pv"
  mkdir -p "$C8_HOME"
  {
    printf '{\n "schema": 1,\n "name": "coordn8",\n "installer": "bash-fallback",\n'
    printf ' "version": %s,\n "commit": "unknown",\n "date": %s,\n' "$(json_str "$VERSION")" "$(json_str "$(date +%Y-%m-%dT%H:%M:%S%z)")"
    printf ' "manifest": {%s},\n "blocks": [%s],\n "json_keys": [],\n "created_dirs": []\n}\n' "${MANIFEST_ROWS%,}" "${BLOCK_ROWS%,}"
  } > "$f.tmp.$$"
  mv "$f.tmp.$$" "$f"
  say "installed $prev → v$VERSION  (recorded in $f)"
}

if [ "$target" = "uninstall" ]; then
  uninstall_all
  exit 0
fi
if [ "$target" = "status" ]; then
  warn "status needs python3"; exit 2
fi

case "$target" in
  claude) install_claude;;
  cursor) install_cursor;;
  codex) install_codex;;
  kiro) install_kiro;;
  generic) install_generic;;
  all) install_claude; install_cursor; install_codex; install_kiro; install_generic;;
esac
write_manifest

say "done. Open a FRESH session at your repo root, then invoke Coordn8."
