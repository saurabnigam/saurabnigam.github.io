#!/usr/bin/env bash
# End-to-end test of publish.sh (and install-skill.sh) against a local bare
# repo. No network, no GitHub. Everything lives in a temp dir that contains a
# space, and the temp dir is deleted on exit.
#
#   bash skills/publish-to-my-sites/test_publish.sh
set -euo pipefail

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
PUBLISH="$HERE/publish.sh"
INSTALL="$HERE/../../scripts/install-skill.sh"
REAL_GIT=$(command -v git)

T=$(mktemp -d "${TMPDIR:-/tmp}/publish test.XXXXXX")
trap 'rm -rf "$T"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# publish.sh reads the macOS keychain when MY_SITES_GH_TOKEN is unset. Keep the
# real keychain out of every test run: this stub finds nothing.
mkdir -p "$T/nokc"
printf '#!/bin/sh\nexit 44\n' > "$T/nokc/security"
chmod +x "$T/nokc/security"
export PATH="$T/nokc:$PATH"

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); printf 'ok   - %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf 'FAIL - %s\n' "$1"; }
check() {
  local desc=$1
  shift
  if "$@"; then ok "$desc"; else bad "$desc"; fi
}
eq()    { [ "$1" = "$2" ]; }
has()   { case "$1" in *"$2"*) return 0 ;; esac; return 1; }
lacks() { case "$1" in *"$2"*) return 1 ;; esac; return 0; }

# Run a command, capture stdout+stderr in OUT and the exit code in RC.
OUT=""
RC=0
run() {
  set +e
  OUT=$("$@" 2>&1)
  RC=$?
  set -e
}

# The test's own git calls ignore any configuration on this machine.
rg() { GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 "$REAL_GIT" "$@"; }

# ------------------------------------------------------------------- fixture

BARE="$T/remote repo.git"
REMOTE="file://$BARE"
PUBTMP="$T/pub tmp"
mkdir -p "$PUBTMP"

rg init -q --bare "$BARE"
rg --git-dir "$BARE" symbolic-ref HEAD refs/heads/main
SEED="$T/seed"
rg init -q "$SEED"
rg -C "$SEED" symbolic-ref HEAD refs/heads/main
echo "seed" > "$SEED/README.md"
rg -C "$SEED" add README.md
rg -C "$SEED" -c user.name=Seed -c user.email=seed@example.invalid commit -q -m "initial" > /dev/null 2>&1
rg -C "$SEED" push -q "$BARE" HEAD:refs/heads/main

commits() { rg --git-dir "$BARE" rev-list --count main; }
tree_has() { rg --git-dir "$BARE" ls-tree -r --name-only main | grep -qxF -- "$1"; }
last_meta() { rg --git-dir "$BARE" log -1 --format='%an|%ae|%cn|%ce|%s' main; }

# A work-account lookalike that publish.sh must not pick up.
HOME_DECOY="$T/decoy home"
mkdir -p "$HOME_DECOY"
cat > "$HOME_DECOY/.gitconfig" <<EOF
[user]
	name = Work Person
	email = work.person@example.invalid
[url "ssh://git@invalid.example/"]
	insteadOf = file://
EOF

# Call publish.sh the way an agent would, with decoys in the environment.
pub() {
  env HOME="$HOME_DECOY" TMPDIR="$PUBTMP" \
    GIT_AUTHOR_NAME="Work Person" GIT_AUTHOR_EMAIL="work.person@example.invalid" \
    GIT_COMMITTER_NAME="Work Person" GIT_COMMITTER_EMAIL="work.person@example.invalid" \
    EMAIL="work.person@example.invalid" \
    MY_SITES_REMOTE_URL="$REMOTE" \
    MY_SITES_GIT_NAME="Test Publisher" MY_SITES_GIT_EMAIL="publisher@example.invalid" \
    "$PUBLISH" "$@"
}

make_page() { # dir [title]
  mkdir -p "$1"
  printf '<!doctype html><title>%s</title><p>hello</p>\n' "${2:-Test page}" > "$1/index.html"
}

SRC="$T/src dir/my page"
make_page "$SRC" "My page"
echo "body { color: red }" > "$SRC/style.css"

START=$(commits)
check "fixture: bare repo has one commit" eq "$START" 1

# --------------------------------------------------------------------- help

run "$PUBLISH" -h
check "-h exits 0 and prints usage" eq "$RC" 0
check "-h mentions --confirm-public" has "$OUT" "--confirm-public"

run "$PUBLISH" --bogus
check "unknown option exits 1" eq "$RC" 1

run "$PUBLISH" staging my-page "$SRC"
check "unknown target exits 1" eq "$RC" 1

# ------------------------------------------------------------------ dry runs

mkdir -p "$T/nogit"
cat > "$T/nogit/git" <<'EOF'
#!/bin/sh
echo "git was called: $*" >> "$NOGIT_MARK"
exit 99
EOF
chmod +x "$T/nogit/git"
export NOGIT_MARK="$T/nogit.mark"

run env -u MY_SITES_GH_TOKEN -u MY_SITES_REMOTE_URL PATH="$T/nogit:$PATH" TMPDIR="$PUBTMP" \
  "$PUBLISH" --dry-run family my-page "$SRC"
check "dry run (no token, no git) exits 0" eq "$RC" 0
check "dry run prints the repo" has "$OUT" "saurabnigam/family-apps"
check "dry run prints the destination" has "$OUT" "apps/my-page/"
check "dry run lists index.html" has "$OUT" "  index.html"
check "dry run lists style.css" has "$OUT" "  style.css"
check "dry run prints the URL" has "$OUT" "https://family-apps.pages.dev/apps/my-page/"
check "dry run made no git call" test ! -e "$NOGIT_MARK"
check "dry run made no commit" eq "$(commits)" "$START"

run env -u MY_SITES_GH_TOKEN -u MY_SITES_REMOTE_URL PATH="$T/nogit:$PATH" TMPDIR="$PUBTMP" \
  "$PUBLISH" --dry-run --remove family my-page
check "dry run of --remove exits 0 without git" eq "$RC" 0
check "dry run of --remove made no git call" test ! -e "$NOGIT_MARK"

run env -u MY_SITES_GH_TOKEN -u MY_SITES_REMOTE_URL PATH="$T/nogit:$PATH" TMPDIR="$PUBTMP" \
  "$PUBLISH" --dry-run --confirm-public public pub-page "$SRC"
check "public dry run with --confirm-public exits 0" eq "$RC" 0
check "public dry run warns that the site is public" has "$OUT" "readable by everyone"
check "public dry run prints the showcase URL" has "$OUT" "https://saurabnigam.github.io/showcase/pub-page/"

# ------------------------------------------------------- public needs consent

run pub public pub-page "$SRC"
check "public publish without --confirm-public exits 2" eq "$RC" 2
check "...and explains that the human must ask" has "$OUT" "explicitly asked"
run pub --dry-run public pub-page "$SRC"
check "public dry run without --confirm-public exits 2" eq "$RC" 2
run pub --remove public pub-page
check "public remove without --confirm-public exits 2" eq "$RC" 2
check "nothing was pushed by the refused public calls" eq "$(commits)" "$START"

# -------------------------------------------------------------- real publish

run pub family my-page "$SRC"
check "publish to family exits 0" eq "$RC" 0
check "publish prints the URL" has "$OUT" "published: https://family-apps.pages.dev/apps/my-page/"
check "exactly one new commit" eq "$(commits)" $((START + 1))
check "index.html is in the repo" tree_has "apps/my-page/index.html"
check "style.css is in the repo" tree_has "apps/my-page/style.css"
check "the commit touches exactly the two files" \
  eq "$(rg --git-dir "$BARE" diff-tree --no-commit-id --name-only -r main | wc -l | tr -d ' ')" 2
check "author and committer are the MY_SITES identity, not the decoy" \
  eq "$(last_meta)" "Test Publisher|publisher@example.invalid|Test Publisher|publisher@example.invalid|publish my-page"
check "no temp directory is left behind" eq "$(ls -A "$PUBTMP" | wc -l | tr -d ' ')" 0

run pub family my-page "$SRC"
check "publishing the same content again exits 0" eq "$RC" 0
check "...and says there are no changes" has "$OUT" "no changes"
check "...and adds no commit" eq "$(commits)" $((START + 1))

rm "$SRC/style.css"
run pub family my-page "$SRC"
check "republish after deleting an asset exits 0" eq "$RC" 0
check "the deleted asset is gone from the repo" bash -c '! "$0" --git-dir "$1" ls-tree -r --name-only main | grep -qxF "apps/my-page/style.css"' "$REAL_GIT" "$BARE"
check "index.html is still there" tree_has "apps/my-page/index.html"
check "republish added one commit" eq "$(commits)" $((START + 2))

# hidden files are skipped, other folders and files are kept
mkdir -p "$SRC/img" "$SRC/.hidden"
echo "x" > "$SRC/img/a.txt"
echo "x" > "$SRC/.DS_Store"
echo "x" > "$SRC/.hidden/y.txt"
run pub family my-page "$SRC"
check "nested file is published" tree_has "apps/my-page/img/a.txt"
check ".DS_Store is not published" bash -c '! "$0" --git-dir "$1" ls-tree -r --name-only main | grep -q "DS_Store"' "$REAL_GIT" "$BARE"
check "hidden folder is not published" bash -c '! "$0" --git-dir "$1" ls-tree -r --name-only main | grep -q "hidden"' "$REAL_GIT" "$BARE"

# custom message, second page, listing
SRC2="$T/src dir/second"
make_page "$SRC2" "Second"
run pub -m "add the second app" family second-app "$SRC2"
check "custom commit message is used" eq "$(last_meta)" "Test Publisher|publisher@example.invalid|Test Publisher|publisher@example.invalid|add the second app"

run pub --list family
check "--list exits 0" eq "$RC" 0
check "--list prints both slugs, sorted" eq "$OUT" "$(printf 'my-page\nsecond-app')"

# --------------------------------------------------------------------- public

before=$(commits)
run pub --confirm-public public pub-page "$SRC2"
check "public publish with --confirm-public exits 0" eq "$RC" 0
check "public URL uses showcase/" has "$OUT" "published: https://saurabnigam.github.io/showcase/pub-page/"
check "public page lands in showcase/" tree_has "showcase/pub-page/index.html"
check "public publish added one commit" eq "$(commits)" $((before + 1))

run env MY_SITES_PUBLIC_URL="https://example.invalid/" TMPDIR="$PUBTMP" \
  "$PUBLISH" --dry-run --confirm-public public pub-page "$SRC2"
check "MY_SITES_PUBLIC_URL overrides the base URL, trailing slash trimmed" \
  has "$OUT" "url:         https://example.invalid/showcase/pub-page/"

run pub --list public
check "--list public needs no --confirm-public" eq "$RC" 0
check "--list public shows the page" eq "$OUT" "pub-page"

# ---------------------------------------------------------------------- remove

before=$(commits)
run pub --remove --confirm-public public pub-page
check "--remove public exits 0" eq "$RC" 0
check "--remove prints removed:" has "$OUT" "removed: https://saurabnigam.github.io/showcase/pub-page/"
check "removed folder is gone" bash -c '! "$0" --git-dir "$1" ls-tree -r --name-only main | grep -q "showcase/pub-page"' "$REAL_GIT" "$BARE"
check "remove commit message" eq "$(last_meta)" "Test Publisher|publisher@example.invalid|Test Publisher|publisher@example.invalid|remove pub-page"
check "remove added one commit" eq "$(commits)" $((before + 1))

run pub --remove family my-page
check "--remove family exits 0" eq "$RC" 0
check "family folder is gone" bash -c '! "$0" --git-dir "$1" ls-tree -r --name-only main | grep -q "apps/my-page/"' "$REAL_GIT" "$BARE"
check "the other family app is untouched" tree_has "apps/second-app/index.html"

before=$(commits)
run pub --remove family my-page
check "removing a missing folder exits 1" eq "$RC" 1
check "...and says it does not exist" has "$OUT" "does not exist"
check "...and adds no commit" eq "$(commits)" "$before"

# -------------------------------------------------------------------- secrets

before=$(commits)
TOKEN_FAKE="ghp_""aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fH3jL5"
SECRET_SRC="$T/src dir/leaky"
make_page "$SECRET_SRC" "Leaky"
printf 'const t = "%s";\n' "$TOKEN_FAKE" > "$SECRET_SRC/app.js"
run pub family leaky "$SECRET_SRC"
check "a source containing a GitHub token aborts" eq "$RC" 1
check "...names the file" has "$OUT" "app.js"
check "...names the pattern" has "$OUT" "github-classic-token"
check "...never prints the whole token" lacks "$OUT" "$TOKEN_FAKE"
check "...never prints even the first 12 characters" lacks "$OUT" "ghp_aB3dE5gH"
check "...pushes nothing" eq "$(commits)" "$before"

KEY_SRC="$T/src dir/keyed"
make_page "$KEY_SRC" "Keyed"
printf -- '-----BEGIN %s-----\nabc\n' "OPENSSH PRIVATE KEY" > "$KEY_SRC/note.txt"
run pub family keyed "$KEY_SRC"
check "a private key header aborts" eq "$RC" 1
check "...names the private-key pattern" has "$OUT" "private-key"

ENV_SRC="$T/src dir/with-env"
make_page "$ENV_SRC" "Env"
printf 'TOKEN=%s\n' "$TOKEN_FAKE" > "$ENV_SRC/.env"
run pub family with-env "$ENV_SRC"
check "a secret only in a skipped dot-file does not block the publish" eq "$RC" 0
check ".env is not published" bash -c '! "$0" --git-dir "$1" ls-tree -r --name-only main | grep -q "\.env"' "$REAL_GIT" "$BARE"

# ----------------------------------------------------------------- validation

before=$(commits)
expect_refusal() { # description, expected text, args...
  local desc=$1 text=$2
  shift 2
  run pub "$@"
  check "$desc: exits 1" eq "$RC" 1
  check "$desc: message mentions '$text'" has "$OUT" "$text"
}

expect_refusal "slug with underscore" "slug" family Bad_Slug "$SRC"
expect_refusal "slug with capital" "slug" family BadSlug "$SRC"
expect_refusal "missing source dir" "not found" family ok-slug "$T/no such dir"

NOINDEX="$T/src dir/noindex"
mkdir -p "$NOINDEX" && echo x > "$NOINDEX/a.txt"
expect_refusal "no index.html" "index.html" family noindex "$NOINDEX"

NOTITLE="$T/src dir/notitle"
mkdir -p "$NOTITLE" && echo '<p>hi</p>' > "$NOTITLE/index.html"
expect_refusal "no <title>" "<title>" family notitle "$NOTITLE"

EMPTYTITLE="$T/src dir/emptytitle"
mkdir -p "$EMPTYTITLE" && echo '<title> </title>' > "$EMPTYTITLE/index.html"
expect_refusal "empty <title>" "empty" family emptytitle "$EMPTYTITLE"

LINKSRC="$T/src dir/linked"
make_page "$LINKSRC" "Linked"
ln -s "$SRC/index.html" "$LINKSRC/link.html"
expect_refusal "symlink in source" "symlink" family linked "$LINKSRC"

SPACESRC="$T/src dir/spaced"
make_page "$SPACESRC" "Spaced"
echo x > "$SPACESRC/my file.txt"
expect_refusal "space in a file name" "unsupported characters" family spaced "$SPACESRC"

UNISRC="$T/src dir/unicode"
make_page "$UNISRC" "Unicode"
echo x > "$UNISRC/caf$(printf '\303\251').txt"
expect_refusal "non-ASCII file name" "unsupported characters" family unicode "$UNISRC"

BIGSRC="$T/src dir/big"
make_page "$BIGSRC" "Big"
dd if=/dev/null of="$BIGSRC/blob.bin" bs=1 seek=$((25 * 1024 * 1024 + 1)) 2>/dev/null
expect_refusal "file over 25 MiB" "25 MiB" family big "$BIGSRC"
dd if=/dev/null of="$BIGSRC/blob.bin" bs=1 seek=$((25 * 1024 * 1024)) 2>/dev/null
run pub --dry-run family big "$BIGSRC"
check "file of exactly 25 MiB is accepted" eq "$RC" 0

MANYSRC="$T/src dir/many"
make_page "$MANYSRC" "Many"
mkdir -p "$MANYSRC/f"
i=0
while [ "$i" -lt 500 ]; do
  : > "$MANYSRC/f/f$i.txt"
  i=$((i + 1))
done
expect_refusal "more than 500 files" "500" family many "$MANYSRC"
rm "$MANYSRC/f/f0.txt"
run pub --dry-run family many "$MANYSRC"
check "exactly 500 files is accepted" eq "$RC" 0

check "no validation failure pushed anything" eq "$(commits)" "$before"

# ------------------------------------------------------------------ missing token

before=$(commits)
mkdir -p "$T/nogit2"
cp "$T/nogit/git" "$T/nogit2/git"
rm -f "$NOGIT_MARK"
run env -u MY_SITES_GH_TOKEN -u MY_SITES_REMOTE_URL PATH="$T/nogit2:$PATH" TMPDIR="$PUBTMP" \
  "$PUBLISH" family my-page "$SRC"
check "missing token exits 1" eq "$RC" 1
check "...names MY_SITES_GH_TOKEN" has "$OUT" "MY_SITES_GH_TOKEN"
check "...points at the README" has "$OUT" "README"
check "...makes no git call" test ! -e "$NOGIT_MARK"

# ---------------------------------------------------- token and git isolation

# A shim in front of git records every call, then runs the real git.
mkdir -p "$T/shim"
cat > "$T/shim/git" <<'EOF'
#!/bin/sh
{
  echo "--- call"
  for a in "$@"; do printf 'arg: %s\n' "$a"; done
  echo "env GIT_TERMINAL_PROMPT=${GIT_TERMINAL_PROMPT:-unset}"
  echo "env GIT_CONFIG_GLOBAL=${GIT_CONFIG_GLOBAL:-unset}"
  case "$HOME" in "$SHIM_DECOY_HOME") echo "env HOME=decoy" ;; *) echo "env HOME=isolated" ;; esac
} >> "$SHIM_LOG"
if [ -n "${GIT_ASKPASS:-}" ]; then
  work=$(dirname "$GIT_ASKPASS")
  grep -rIl -F -- "$SHIM_TOKEN" "$work" >> "$SHIM_LEAKS" 2>/dev/null || true
  "$GIT_ASKPASS" "Password for 'https://github.com': " > "$SHIM_PW"
  "$GIT_ASKPASS" "Username for 'https://github.com': " > "$SHIM_USER"
  "$GIT_ASKPASS" "Password for 'https://evil.example.invalid': " > "$SHIM_OTHER"
fi
exec "$REAL_GIT" "$@"
EOF
chmod +x "$T/shim/git"
export REAL_GIT
export SHIM_LOG="$T/shim.log" SHIM_LEAKS="$T/shim.leaks" SHIM_PW="$T/shim.pw" SHIM_USER="$T/shim.user" SHIM_OTHER="$T/shim.other"
export SHIM_TOKEN="TESTTOKEN_not_a_real_secret_0123456789" SHIM_DECOY_HOME="$HOME_DECOY"

SRC3="$T/src dir/tok"
make_page "$SRC3" "Tok"
run env PATH="$T/shim:$PATH" MY_SITES_GH_TOKEN="$SHIM_TOKEN" \
  HOME="$HOME_DECOY" TMPDIR="$PUBTMP" MY_SITES_REMOTE_URL="$REMOTE" \
  MY_SITES_GIT_NAME="Test Publisher" MY_SITES_GIT_EMAIL="publisher@example.invalid" \
  "$PUBLISH" family tok-page "$SRC3"
check "publish through the git shim exits 0" eq "$RC" 0
LOG=$(cat "$SHIM_LOG")
check "the token is not in any git argument" lacks "$LOG" "$SHIM_TOKEN"
check "the token is not in the script output" lacks "$OUT" "$SHIM_TOKEN"
check "the token is not in any file of the work area" test ! -s "$SHIM_LEAKS"
check "clone and push both clear credential.helper" \
  eq "$(grep -c '^arg: credential.helper=$' "$SHIM_LOG")" 2
check "git ran with GIT_TERMINAL_PROMPT=0" has "$LOG" "env GIT_TERMINAL_PROMPT=0"
check "git ran with the global config switched off" has "$LOG" "env GIT_CONFIG_GLOBAL=/dev/null"
check "git never saw the caller's HOME" lacks "$LOG" "env HOME=decoy"
check "the askpass helper answers a password prompt with the token" eq "$(cat "$SHIM_PW")" "$SHIM_TOKEN"
check "the askpass helper answers a username prompt with the repo owner" eq "$(cat "$SHIM_USER")" "saurabnigam"
check "the remote URL is passed as given (no credentials in it)" has "$LOG" "arg: $REMOTE"
check "the askpass helper gives the token to no other host" eq "$(tr -d '\n' < "$SHIM_OTHER")" ""

# With MY_SITES_GH_TOKEN unset, the token comes from the keychain for this run.
mkdir -p "$T/kc"
cat > "$T/kc/security" <<'EOF'
#!/bin/sh
[ "$1" = find-generic-password ] || exit 44
printf '%s\n' "$SHIM_TOKEN"
EOF
chmod +x "$T/kc/security"
rm -f "$SHIM_PW" "$SHIM_LOG"
SRC4="$T/src dir/kc"
make_page "$SRC4" "Keychain"
run env -u MY_SITES_GH_TOKEN PATH="$T/kc:$T/shim:$PATH" \
  HOME="$HOME_DECOY" TMPDIR="$PUBTMP" MY_SITES_REMOTE_URL="$REMOTE" \
  MY_SITES_GIT_NAME="Test Publisher" MY_SITES_GIT_EMAIL="publisher@example.invalid" \
  "$PUBLISH" family kc-page "$SRC4"
check "keychain token: publish exits 0" eq "$RC" 0
check "keychain token reaches the askpass helper" eq "$(cat "$SHIM_PW")" "$SHIM_TOKEN"

# The username follows the owner of the target repo.
rm -f "$SHIM_USER"
run env PATH="$T/shim:$PATH" MY_SITES_GH_TOKEN="$SHIM_TOKEN" MY_SITES_FAMILY_REPO="someone/else-apps" \
  HOME="$HOME_DECOY" TMPDIR="$PUBTMP" MY_SITES_REMOTE_URL="$REMOTE" \
  MY_SITES_GIT_NAME="Test Publisher" MY_SITES_GIT_EMAIL="publisher@example.invalid" \
  "$PUBLISH" family owner-page "$SRC4"
check "another repo owner: publish exits 0" eq "$RC" 0
check "another repo owner: the username is that owner" eq "$(cat "$SHIM_USER")" "someone"
check "keychain token is not in the output" lacks "$OUT" "$SHIM_TOKEN"
check "keychain token is not in any git argument" lacks "$(cat "$SHIM_LOG")" "$SHIM_TOKEN"

# Without the test hook, the remote is the GitHub URL. A git that only records
# its arguments and fails keeps this check offline.
mkdir -p "$T/argsgit"
cat > "$T/argsgit/git" <<'EOF'
#!/bin/sh
for a in "$@"; do printf 'arg: %s\n' "$a"; done >> "$ARGS_LOG"
exit 1
EOF
chmod +x "$T/argsgit/git"
export ARGS_LOG="$T/args.log"
run env -u MY_SITES_REMOTE_URL PATH="$T/argsgit:$PATH" MY_SITES_GH_TOKEN="$SHIM_TOKEN" TMPDIR="$PUBTMP" \
  "$PUBLISH" family default-url "$SRC3"
ARGS=$(cat "$ARGS_LOG")
check "default remote: clone fails cleanly with exit 1" eq "$RC" 1
check "default remote is the plain GitHub URL" has "$ARGS" "arg: https://github.com/saurabnigam/family-apps.git"
check "default remote clones the main branch, shallow" has "$ARGS" "arg: --depth"
check "default remote: the token is not in any git argument" lacks "$ARGS" "$SHIM_TOKEN"
check "default remote: the token is not in the output" lacks "$OUT" "$SHIM_TOKEN"
rm -f "$ARGS_LOG"
run env -u MY_SITES_REMOTE_URL PATH="$T/argsgit:$PATH" MY_SITES_GH_TOKEN="$SHIM_TOKEN" TMPDIR="$PUBTMP" \
  MY_SITES_FAMILY_REPO="someone/else-apps" MY_SITES_BRANCH="trunk" \
  "$PUBLISH" family default-url "$SRC3"
ARGS=$(cat "$ARGS_LOG")
check "MY_SITES_FAMILY_REPO changes the clone URL" has "$ARGS" "arg: https://github.com/someone/else-apps.git"
check "MY_SITES_BRANCH changes the branch" has "$ARGS" "arg: trunk"

# ------------------------------------------------------------ push rejections

cat > "$BARE/hooks/pre-receive" <<EOF
#!/bin/sh
if [ ! -f "$T/rejected-once" ]; then
  : > "$T/rejected-once"
  echo "refusing the first push on purpose" >&2
  exit 1
fi
exit 0
EOF
chmod +x "$BARE/hooks/pre-receive"
before=$(commits)
SRC4="$T/src dir/retry"
make_page "$SRC4" "Retry"
run pub family retry-page "$SRC4"
check "a rejected push is retried and then succeeds" eq "$RC" 0
check "...and says it retried" has "$OUT" "retrying"
check "...and lands exactly one commit" eq "$(commits)" $((before + 1))

cat > "$BARE/hooks/pre-receive" <<'EOF'
#!/bin/sh
echo "refusing every push on purpose" >&2
exit 1
EOF
before=$(commits)
rm -f "$SHIM_LOG"
SRC5="$T/src dir/always"
make_page "$SRC5" "Always"
run env PATH="$T/shim:$PATH" HOME="$HOME_DECOY" TMPDIR="$PUBTMP" MY_SITES_REMOTE_URL="$REMOTE" \
  MY_SITES_GIT_NAME="Test Publisher" MY_SITES_GIT_EMAIL="publisher@example.invalid" \
  "$PUBLISH" family always-page "$SRC5"
check "a push that is always rejected exits 1" eq "$RC" 1
check "...after exactly 3 clone attempts" eq "$(grep -c '^arg: clone$' "$SHIM_LOG")" 3
check "...pushes nothing" eq "$(commits)" "$before"
check "...leaves no temp directory behind" eq "$(ls -A "$PUBTMP" | wc -l | tr -d ' ')" 0
rm -f "$BARE/hooks/pre-receive"

# ------------------------------------------------------------ default identity

run env -u MY_SITES_GIT_NAME -u MY_SITES_GIT_EMAIL HOME="$HOME_DECOY" TMPDIR="$PUBTMP" \
  GIT_AUTHOR_NAME="Work Person" GIT_COMMITTER_NAME="Work Person" EMAIL="work.person@example.invalid" \
  MY_SITES_REMOTE_URL="$REMOTE" "$PUBLISH" family default-id "$SRC4"
check "default identity publish exits 0" eq "$RC" 0
check "default identity is the noreply address, never the global one" \
  eq "$(last_meta)" "Saurabh Nigam|saurabnigam@users.noreply.github.com|Saurabh Nigam|saurabnigam@users.noreply.github.com|publish default-id"

# -------------------------------------------------------------- install-skill

if [ -f "$INSTALL" ]; then
  D="$T/skills dest"
  run "$INSTALL" --dest "$D"
  check "install --dest exits 0" eq "$RC" 0
  check "installed SKILL.md exists" test -f "$D/publish-to-my-sites/SKILL.md"
  check "installed publish.sh is executable" test -x "$D/publish-to-my-sites/publish.sh"
  check "installed copy is a real directory, not a symlink" test ! -L "$D/publish-to-my-sites"
  echo "stale" > "$D/publish-to-my-sites/stale.txt"
  echo "changed" >> "$D/publish-to-my-sites/SKILL.md"
  run "$INSTALL" --dest "$D"
  check "install is idempotent and replaces the copy" eq "$RC" 0
  check "...stale files are gone" test ! -e "$D/publish-to-my-sites/stale.txt"
  check "...edits are overwritten" cmp -s "$HERE/SKILL.md" "$D/publish-to-my-sites/SKILL.md"
  run "$INSTALL" --uninstall --dest "$D"
  check "uninstall --dest exits 0" eq "$RC" 0
  check "...the folder is gone" test ! -e "$D/publish-to-my-sites"
  run "$INSTALL" --uninstall --dest "$D"
  check "uninstall twice is fine" eq "$RC" 0

  FAKEHOME="$T/fake home"
  run env HOME="$FAKEHOME" "$INSTALL" --only claude
  check "--only claude installs under ~/.claude/skills" test -f "$FAKEHOME/.claude/skills/publish-to-my-sites/publish.sh"
  check "--only claude leaves ~/.agents alone" test ! -e "$FAKEHOME/.agents"
  run env HOME="$FAKEHOME" "$INSTALL"
  check "default installs under ~/.agents/skills too" test -x "$FAKEHOME/.agents/skills/publish-to-my-sites/publish.sh"
  run env HOME="$FAKEHOME" "$INSTALL" --uninstall
  check "default uninstall removes both" test ! -e "$FAKEHOME/.claude/skills/publish-to-my-sites"
  check "...and the other" test ! -e "$FAKEHOME/.agents/skills/publish-to-my-sites"

  run "$INSTALL" --only bogus
  check "--only with a bad value exits 1" eq "$RC" 1
  run "$INSTALL" --dest "$HERE/.."
  check "installing onto the source folder is refused" eq "$RC" 1
  check "...and the source folder still has its files" test -f "$HERE/publish.sh"
  run "$INSTALL" -h
  check "install-skill -h exits 0" eq "$RC" 0
else
  echo "skip - scripts/install-skill.sh not found next to this skill (installed copy?)"
fi

# --------------------------------------------------------------------- result

echo
echo "passed: $PASS, failed: $FAIL"
if [ "$FAIL" -ne 0 ]; then
  exit 1
fi
