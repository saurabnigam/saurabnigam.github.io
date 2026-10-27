#!/usr/bin/env bash
# Publish a self-contained page or small app to Saurabh's public showcase or
# to the private family apps site, by pushing one commit to the site's repo.
#
# Works on macOS bash 3.2 and on bash 5. No GNU-only flags.
set -euo pipefail
export LC_ALL=C

usage() {
  cat <<'EOF'
Usage:
  publish.sh [--dry-run] [--confirm-public] [-m MESSAGE] <public|family> <slug> <source_dir>
  publish.sh --remove [--dry-run] [--confirm-public] <public|family> <slug>
  publish.sh --list <public|family>
  publish.sh -h

Targets:
  public   showcase/<slug>/ in the PUBLIC site. Needs --confirm-public.
  family   apps/<slug>/ in the private family site.

The source directory must contain index.html with a <title>. Publishing the
same slug again replaces the whole folder. Files whose names start with "." are
skipped. Symlinks, files over 25 MiB, more than 500 files, odd file names and
anything that looks like a secret are refused.

Environment (defaults in brackets):
  MY_SITES_GH_TOKEN         fine-grained token for the personal account (required
                            unless --dry-run; if unset, read from the macOS
                            keychain item MY_SITES_GH_TOKEN)
  MY_SITES_PUBLIC_REPO      [saurabnigam/saurabnigam.github.io]
  MY_SITES_PUBLIC_URL       [https://saurabnigam.github.io]
  MY_SITES_FAMILY_REPO      [saurabnigam/family-apps]
  MY_SITES_FAMILY_URL       [https://family-apps.pages.dev]
  MY_SITES_BRANCH           [main]
  MY_SITES_GIT_NAME         [Saurabh Nigam]
  MY_SITES_GIT_EMAIL        [saurabnigam@users.noreply.github.com]
  MY_SITES_REMOTE_URL       test hook: use this git remote instead of GitHub

Exit codes: 0 ok, 1 error, 2 public target without --confirm-public.
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

note() {
  printf '%s\n' "$*" >&2
}

# ---------------------------------------------------------------- arguments

DRY_RUN=0
CONFIRM_PUBLIC=0
MODE=publish
MODE_SET=0
MESSAGE=""
TARGET=""
SLUG=""
SRC_ARG=""
NPOS=0

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --dry-run) DRY_RUN=1 ;;
    --confirm-public) CONFIRM_PUBLIC=1 ;;
    --remove|--list)
      [ "$MODE_SET" -eq 0 ] || die "use only one of --remove and --list"
      MODE=${1#--}
      MODE_SET=1
      ;;
    -m)
      [ $# -ge 2 ] || die "-m needs a message"
      MESSAGE=$2
      shift
      ;;
    -*) die "unknown option: $1 (see -h)" ;;
    *)
      NPOS=$((NPOS + 1))
      case "$NPOS" in
        1) TARGET=$1 ;;
        2) SLUG=$1 ;;
        3) SRC_ARG=$1 ;;
        *) die "too many arguments (see -h)" ;;
      esac
      ;;
  esac
  shift
done

case "$MODE" in
  publish) [ "$NPOS" -eq 3 ] || die "expected: <public|family> <slug> <source_dir> (see -h)" ;;
  remove)  [ "$NPOS" -eq 2 ] || die "expected: --remove <public|family> <slug> (see -h)" ;;
  list)    [ "$NPOS" -eq 1 ] || die "expected: --list <public|family> (see -h)" ;;
esac

# ------------------------------------------------------------------- config

case "$TARGET" in
  public)
    REPO=${MY_SITES_PUBLIC_REPO:-saurabnigam/saurabnigam.github.io}
    SECTION=showcase
    BASE_URL=${MY_SITES_PUBLIC_URL:-https://saurabnigam.github.io}
    ;;
  family)
    REPO=${MY_SITES_FAMILY_REPO:-saurabnigam/family-apps}
    SECTION=apps
    BASE_URL=${MY_SITES_FAMILY_URL:-https://family-apps.pages.dev}
    ;;
  *) die "target must be 'public' or 'family' (got '$TARGET')" ;;
esac
BASE_URL=${BASE_URL%/}
BRANCH=${MY_SITES_BRANCH:-main}
GIT_NAME=${MY_SITES_GIT_NAME:-Saurabh Nigam}
GIT_EMAIL=${MY_SITES_GIT_EMAIL:-saurabnigam@users.noreply.github.com}
REMOTE_OVERRIDE=${MY_SITES_REMOTE_URL:-}

# The public site is readable by everyone. Never touch it by accident.
if [ "$TARGET" = public ] && [ "$MODE" != list ] && [ "$CONFIRM_PUBLIC" -ne 1 ]; then
  cat >&2 <<'EOF'
error: refusing to change the PUBLIC site without --confirm-public.
Anyone on the internet can read it. Add --confirm-public only when the human
has explicitly asked for a public page. If they did not, ask which site they
mean, or use the 'family' target.
EOF
  exit 2
fi

SLUG_RE='^[a-z0-9]+(-[a-z0-9]+)*$'
NAME_RE='^[A-Za-z0-9._/-]+$'
REPO_RE='^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$'
BRANCH_RE='^[A-Za-z0-9._/-]+$'
MAX_FILE_BYTES=26214400   # 25 MiB, the Cloudflare Pages per-file limit
MAX_SCAN_BYTES=5242880    # 5 MiB, same as scripts/scan_secrets.py
MAX_FILES=500

[[ $REPO =~ $REPO_RE ]] || die "repository must look like owner/name (got '$REPO')"
[[ $BRANCH =~ $BRANCH_RE ]] || die "unsupported branch name '$BRANCH'"
if [ "$MODE" != list ]; then
  [[ $SLUG =~ $SLUG_RE ]] || die "slug must be lower-case letters, digits and single hyphens (got '$SLUG')"
fi

if [ -n "$REMOTE_OVERRIDE" ]; then
  REMOTE=$REMOTE_OVERRIDE
else
  REMOTE="https://github.com/$REPO.git"
fi
URL="$BASE_URL/$SECTION/$SLUG/"

# ------------------------------------------------------------- temp workspace

WORK=$(mktemp -d "${TMPDIR:-/tmp}/publish-to-my-sites.XXXXXX") || die "cannot create a temp directory"
cleanup() {
  if [ -n "${WORK:-}" ] && [ -d "$WORK" ]; then
    rm -rf "$WORK"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP

# ------------------------------------------------------------ source checks

SRC=""
NFILES=0

validate_source() {
  local entry rel path size
  SRC=$(cd -- "$SRC_ARG" 2>/dev/null && pwd -P) || die "source directory not found: $SRC_ARG"
  if [ -L "$SRC/index.html" ] || [ ! -f "$SRC/index.html" ]; then
    die "$SRC_ARG/index.html is missing"
  fi
  grep -qiE '<title([[:space:]>]|$)' "$SRC/index.html" || die "index.html has no <title>"
  if grep -qiE '<title[^>]*>[[:space:]]*</title>' "$SRC/index.html"; then
    die "index.html has an empty <title>"
  fi

  # NUL-separated so that odd file names cannot split into several entries.
  ( cd "$SRC" && find . -mindepth 1 -name '.*' -prune -o -print0 ) > "$WORK/all.z" \
    || die "cannot list $SRC_ARG"
  : > "$WORK/files.unsorted"
  : > "$WORK/files.big"
  while IFS= read -r -d '' entry <&3; do
    rel=${entry#./}
    path="$SRC/$rel"
    if [ -L "$path" ]; then
      die "symlinks are not allowed: $rel"
    fi
    if ! [[ $rel =~ $NAME_RE ]]; then
      die "unsupported characters in file name (allowed: A-Z a-z 0-9 . _ - /): $rel"
    fi
    if [ -d "$path" ]; then
      continue
    fi
    if [ ! -f "$path" ]; then
      die "not a regular file: $rel"
    fi
    size=$(wc -c < "$path" | tr -d ' ')
    if [ "$size" -gt "$MAX_FILE_BYTES" ]; then
      die "file is larger than 25 MiB: $rel"
    fi
    if [ "$size" -gt "$MAX_SCAN_BYTES" ]; then
      printf '%s\n' "$rel" >> "$WORK/files.big"
    fi
    NFILES=$((NFILES + 1))
    if [ "$NFILES" -gt "$MAX_FILES" ]; then
      die "more than $MAX_FILES files; publish a smaller folder"
    fi
    printf '%s\n' "$rel" >> "$WORK/files.unsorted"
  done 3< "$WORK/all.z"
  sort "$WORK/files.unsorted" > "$WORK/files.lst"
}

# Same patterns as scripts/scan_secrets.py. Format: name, then the ERE.
SECRET_PATTERNS='github-classic-token ghp_[A-Za-z0-9]{36}
github-fine-grained-token github_pat_[A-Za-z0-9_]{22,}
github-oauth-token gh[osu]_[A-Za-z0-9]{36}
aws-access-key-id AKIA[0-9A-Z]{16}
anthropic-api-key sk-ant-[A-Za-z0-9_-]{20,}
openai-style-key sk-[A-Za-z0-9]{32,}
slack-token xox[abprs]-[A-Za-z0-9-]{10,}
private-key -----BEGIN [A-Z ]*PRIVATE KEY-----'

scan_for_secrets() {
  local rel path name re nul hits skipped_big
  hits=0
  skipped_big=0
  while IFS= read -r rel <&4; do
    path="$SRC/$rel"
    # Same rule as scan_secrets.py: a NUL in the first 8 KiB means binary.
    nul=$(head -c 8192 "$path" | tr -d -c '\000' | wc -c | tr -d ' ')
    if [ "$nul" -gt 0 ]; then
      continue
    fi
    if grep -qxF -- "$rel" "$WORK/files.big"; then
      skipped_big=$((skipped_big + 1))
      continue
    fi
    while read -r name re; do
      if grep -qE -e "$re" -- "$path"; then
        note "secret scan: $rel: $name"
        hits=$((hits + 1))
      fi
    done <<EOF
$SECRET_PATTERNS
EOF
  done 4< "$WORK/files.lst"
  if [ "$skipped_big" -gt 0 ]; then
    note "note: $skipped_big file(s) over 5 MiB were not scanned for secrets"
  fi
  if [ "$hits" -gt 0 ]; then
    die "possible secrets found in the source; nothing was published"
  fi
  echo "secret scan: clean ($NFILES files)"
}

# ----------------------------------------------------------------- plan text

print_plan() {
  echo "dry run: nothing is cloned, committed or pushed"
  echo "target:      $TARGET"
  if [ "$TARGET" = public ]; then
    echo "WARNING:     the public site is readable by everyone on the internet"
  fi
  echo "repo:        $REPO"
  echo "branch:      $BRANCH"
  echo "destination: $SECTION/$SLUG/"
  if [ "$MODE" = publish ]; then
    echo "files ($NFILES):"
    sed 's/^/  /' "$WORK/files.lst"
  else
    echo "action:      remove $SECTION/$SLUG/ entirely"
  fi
  echo "url:         $URL"
}

# ---------------------------------------------------------------- git helpers

# Without MY_SITES_GH_TOKEN, read the token from the macOS keychain for this
# run only, so it does not have to live in every shell's environment.
need_token() {
  if [ -z "${MY_SITES_GH_TOKEN:-}" ] && command -v security >/dev/null 2>&1; then
    MY_SITES_GH_TOKEN=$(security find-generic-password -w -s MY_SITES_GH_TOKEN 2>/dev/null || true)
    export MY_SITES_GH_TOKEN
  fi
  if [ -z "$REMOTE_OVERRIDE" ] && [ -z "${MY_SITES_GH_TOKEN:-}" ]; then
    die "no token: MY_SITES_GH_TOKEN is not set and the keychain has no MY_SITES_GH_TOKEN item. Do not fall back to 'gh auth' or the default git login: they are the work account. Create the personal-account token as described in the README of saurabnigam/saurabnigam.github.io (section 'One-time setup', step b), then retry."
  fi
}

# Git runs with no access to the machine's own configuration, so a work
# identity, stored credentials or URL rewrites cannot be picked up.
setup_git_env() {
  mkdir -p "$WORK/home"
  cat > "$WORK/askpass.sh" <<'EOF'
#!/bin/sh
# Prints the token from the environment, and only when git is asking github.com.
# The token is never written to disk.
case "$1" in
  *github.com*) ;;
  *) exit 0 ;;
esac
case "$1" in
  Username*) printf '%s\n' "x-access-token" ;;
  *) printf '%s\n' "${MY_SITES_GH_TOKEN:-}" ;;
esac
EOF
  chmod 700 "$WORK/askpass.sh"
  export GIT_ASKPASS="$WORK/askpass.sh"
  export GIT_TERMINAL_PROMPT=0
  export GIT_CONFIG_NOSYSTEM=1
  export GIT_CONFIG_GLOBAL=/dev/null
  export HOME="$WORK/home"
  export XDG_CONFIG_HOME="$WORK/home/.config"
  unset SSH_ASKPASS GIT_SSH GIT_SSH_COMMAND GIT_CONFIG_COUNT GIT_CONFIG_PARAMETERS \
    GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_OBJECT_DIRECTORY GIT_NAMESPACE \
    GIT_TRACE GIT_TRACE_CURL GIT_TRACE_PACKET GIT_TRACE_SETUP GIT_CURL_VERBOSE \
    GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_AUTHOR_DATE \
    GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL GIT_COMMITTER_DATE EMAIL
}

# Every git command that talks to the remote goes through here.
gitn() {
  git -c credential.helper= "$@"
}

CLONE=""
fresh_clone() {
  CLONE="$WORK/clone.$1"
  rm -rf "$CLONE"
  if ! gitn clone --quiet --depth 1 --branch "$BRANCH" "$REMOTE" "$CLONE" 2> "$WORK/clone.err"; then
    cat "$WORK/clone.err" >&2
    die "could not clone $REPO (branch $BRANCH)"
  fi
}

refuse_symlinked_target() {
  if [ -L "$CLONE/$SECTION" ] || [ -L "$CLONE/$SECTION/$SLUG" ]; then
    die "$SECTION or $SECTION/$SLUG is a symlink in $REPO; refusing to write through it"
  fi
}

apply_publish() {
  local rel dest
  dest="$CLONE/$SECTION/$SLUG"
  refuse_symlinked_target
  rm -rf "$dest"
  mkdir -p "$dest"
  while IFS= read -r rel; do
    mkdir -p "$dest/$(dirname "$rel")"
    cp "$SRC/$rel" "$dest/$rel"
    chmod 644 "$dest/$rel"
  done < "$WORK/files.lst"
  # -f: what was published is exactly what lands, even if a .gitignore matches.
  git -C "$CLONE" add -A -f -- "$SECTION/$SLUG"
}

apply_remove() {
  refuse_symlinked_target
  if [ ! -d "$CLONE/$SECTION/$SLUG" ]; then
    die "$SECTION/$SLUG does not exist in $REPO"
  fi
  git -C "$CLONE" rm -r -q -- "$SECTION/$SLUG"
}

# Returns 1 when there is nothing to commit.
commit_changes() {
  if git -C "$CLONE" diff --cached --quiet; then
    return 1
  fi
  # A pre-commit hook on this machine (for example gitleaks) may print what it
  # matched. Keep that out of the logs; on failure say so instead.
  if ! git -C "$CLONE" -c user.name="$GIT_NAME" -c user.email="$GIT_EMAIL" \
    -c commit.gpgsign=false commit --quiet -m "$1" > "$WORK/commit.out" 2>&1; then
    die "git commit failed. A pre-commit hook may have flagged the content; its output is withheld because it could contain a secret. Nothing was published."
  fi
  return 0
}

# Clone, change, commit, push. On a rejected push, start again from a fresh
# clone, at most 3 attempts in total.
change_and_push() {
  local apply=$1 msg=$2 done_word=$3 attempt
  attempt=1
  while [ "$attempt" -le 3 ]; do
    fresh_clone "$attempt"
    "$apply"
    if ! commit_changes "$msg"; then
      echo "no changes"
      return 0
    fi
    if gitn -C "$CLONE" push --quiet origin "HEAD:refs/heads/$BRANCH" 2> "$WORK/push.err"; then
      echo "$done_word: $URL"
      return 0
    fi
    if grep -qiE 'rejected|non-fast-forward|fetch first|failed to update ref|failed to push some refs|behind' "$WORK/push.err" \
      && [ "$attempt" -lt 3 ]; then
      note "push rejected (attempt $attempt of 3); retrying from a fresh clone"
      attempt=$((attempt + 1))
      continue
    fi
    cat "$WORK/push.err" >&2
    die "push failed after $attempt attempt(s); nothing was published"
  done
}

# ------------------------------------------------------------------ run modes

case "$MODE" in
  list)
    if [ "$DRY_RUN" -eq 1 ]; then
      echo "dry run: would list the slugs under $SECTION/ in $REPO (branch $BRANCH)"
      exit 0
    fi
    need_token
    setup_git_env
    fresh_clone 1
    if [ -d "$CLONE/$SECTION" ]; then
      find "$CLONE/$SECTION" -mindepth 1 -maxdepth 1 -type d ! -name '.*' -print \
        | sed 's|.*/||' | grep -E "$SLUG_RE" | sort || true
    fi
    ;;

  remove)
    if [ "$DRY_RUN" -eq 1 ]; then
      print_plan
      exit 0
    fi
    need_token
    setup_git_env
    change_and_push apply_remove "${MESSAGE:-remove $SLUG}" removed
    ;;

  publish)
    validate_source
    scan_for_secrets
    if [ "$DRY_RUN" -eq 1 ]; then
      print_plan
      exit 0
    fi
    need_token
    if [ "$TARGET" = public ]; then
      note "publishing to the PUBLIC site: anyone on the internet can read it"
    fi
    setup_git_env
    change_and_push apply_publish "${MESSAGE:-publish $SLUG}" published
    ;;
esac
