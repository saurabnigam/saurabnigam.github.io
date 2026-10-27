#!/usr/bin/env bash
# Copy the publish-to-my-sites skill into the agent skill folders.
# Copies, never symlinks, so a skill folder keeps working if this repo moves.
set -euo pipefail

SKILL=publish-to-my-sites

usage() {
  cat <<'EOF'
Usage: scripts/install-skill.sh [--only claude|agents] [--uninstall] [--dest DIR] [-h]

Installs skills/publish-to-my-sites/ into:
  ~/.claude/skills/publish-to-my-sites/
  ~/.agents/skills/publish-to-my-sites/

  --only claude|agents   install into just one of the two
  --uninstall            remove the installed copies instead
  --dest DIR             use DIR/publish-to-my-sites/ as the only target
                         (for tests; cannot be combined with --only)
  -h                     show this help

Running it again replaces the installed copy.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

ONLY=""
UNINSTALL=0
DEST=""
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --uninstall) UNINSTALL=1 ;;
    --only)
      [ $# -ge 2 ] || die "--only needs claude or agents"
      ONLY=$2
      shift
      ;;
    --dest)
      [ $# -ge 2 ] || die "--dest needs a directory"
      DEST=$2
      shift
      ;;
    *) die "unknown argument: $1 (see -h)" ;;
  esac
  shift
done

case "$ONLY" in
  ""|claude|agents) ;;
  *) die "--only must be 'claude' or 'agents' (got '$ONLY')" ;;
esac
if [ -n "$DEST" ] && [ -n "$ONLY" ]; then
  die "--dest and --only cannot be combined"
fi

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SRC=$(cd -- "$SCRIPT_DIR/../skills/$SKILL" 2>/dev/null && pwd -P) \
  || die "skill folder not found: $SCRIPT_DIR/../skills/$SKILL"
[ -f "$SRC/SKILL.md" ] && [ -f "$SRC/publish.sh" ] || die "$SRC must contain SKILL.md and publish.sh"

install_into() {
  local parent=$1 real_parent target
  mkdir -p "$parent"
  real_parent=$(cd -- "$parent" && pwd -P)
  target="$parent/$SKILL"
  if [ "$real_parent/$SKILL" = "$SRC" ]; then
    die "$target is the source folder itself; choose another destination"
  fi
  rm -rf "$target"
  mkdir -p "$target"
  cp -R "$SRC"/. "$target"/
  chmod +x "$target/publish.sh"
  echo "installed: $target"
}

uninstall_from() {
  local target="$1/$SKILL"
  if [ -e "$target" ] || [ -L "$target" ]; then
    rm -rf "$target"
    echo "removed: $target"
  else
    echo "not installed: $target"
  fi
}

run_for() {
  if [ "$UNINSTALL" -eq 1 ]; then
    uninstall_from "$1"
  else
    install_into "$1"
  fi
}

if [ -n "$DEST" ]; then
  run_for "$DEST"
else
  [ -n "${HOME:-}" ] || die "HOME is not set"
  if [ -z "$ONLY" ] || [ "$ONLY" = claude ]; then
    run_for "$HOME/.claude/skills"
  fi
  if [ -z "$ONLY" ] || [ "$ONLY" = agents ]; then
    run_for "$HOME/.agents/skills"
  fi
fi
