# saurabnigam.github.io

Saurabh Nigam's public site on GitHub Pages.

- `/` is the resume (`index.html`, plain HTML).
- `/showcase/` lists small, self-contained pages and apps, one folder each, at `/showcase/<slug>/`.

Everything here is public. Never commit secrets or personal data (see `llms.txt` for the full list).

A sibling private repo, `saurabnigam/family-apps`, serves family-only apps behind a login. Both repos share the same build scripts and the same publish skill (the skill lives only in this repo).

## Layout

```
index.html                  the resume (edit by hand only)
showcase/index.html         listing page; the list between the markers is generated
showcase/<slug>/index.html  one published page, plus optional assets in the same folder
llms.txt                    rules for any agent that publishes here
scripts/build_site.py       copies the site to dist/ and generates listings and index.json
scripts/scan_secrets.py     fails the build if a file looks like it contains a secret
scripts/install-skill.sh    copies the publish skill into ~/.claude and ~/.agents
skills/publish-to-my-sites/ SKILL.md, publish.sh and test_publish.sh
tests/                      unit tests for the two Python scripts
.github/workflows/deploy.yml  test, scan, check, build, deploy to GitHub Pages
.nojekyll                   keeps Jekyll off if Pages is ever switched back to "deploy from branch"
_config.yml                 old Jekyll config; not used by the Actions deployment
```

`dist/` is the build output. It is ignored by git.

## How publishing works

```
agent or you
   |  publish.sh public <slug> <folder> --confirm-public
   v
publish.sh   validates the folder, scans for secrets, clones the repo
   |         (token via GIT_ASKPASS, never in a URL or a file)
   |  one commit: showcase/<slug>/ replaced as a whole
   v
GitHub repo, branch main
   |  push triggers .github/workflows/deploy.yml
   v
unit tests -> scan_secrets.py -> build_site.py --check -> build_site.py --out dist
   |
   v
GitHub Pages  https://saurabnigam.github.io/showcase/<slug>/   (1 to 3 minutes)
```

Page contract: one self-contained `index.html` per folder with `<title>`, `<meta name="description">` and `<meta name="created" content="YYYY-MM-DD">`. Add `<meta name="draft" content="true">` to publish a page without listing it. Details are in `llms.txt` and `skills/publish-to-my-sites/SKILL.md`.

Publishing the same slug again replaces the whole folder. To remove a page: `publish.sh --remove --confirm-public public <slug>`.

## One-time setup

Do these once, in this order.

### (a) Switch Pages to GitHub Actions

Settings → Pages → Build and deployment → Source: **GitHub Actions**.

- The custom domain is set in the same Settings → Pages screen. A `CNAME` file is not used by Actions deployments, so do not add one.
- The old Jekyll setup (`_config.yml`) stops being used. The resume is plain HTML, so nothing changes visually.
- The first deploy runs when this branch is merged to `main`, or by hand from the Actions tab (`workflow_dispatch`).

### (b) Create a token on the PERSONAL account

This laptop's default `gh` / git login is a work account. Never use it for these repos. Use a separate token that only the publish script reads.

1. Create the private repo `saurabnigam/family-apps` first, so it can be selected below.
2. Sign in to GitHub as the **personal** account `saurabnigam`. Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token.
3. Resource owner: `saurabnigam`. Repository access: **Only select repositories**: `saurabnigam.github.io` and `family-apps`.
4. Repository permissions: **Contents: Read and write**. Nothing else (GitHub adds read-only Metadata by itself).
5. Pick an expiry and note the date in your calendar.
6. Store the token in the macOS keychain. The `-w` at the end makes `security` prompt for the value, so it never lands in shell history:

   ```
   security add-generic-password -a "$USER" -s MY_SITES_GH_TOKEN -w
   ```

7. Do NOT export it from `~/.zshrc`. Every process and agent on the laptop would then inherit it. `publish.sh` reads the keychain item itself, only while it runs. On Linux or in CI, set `MY_SITES_GH_TOKEN` in that environment instead.

If `main` has branch protection that requires pull requests, the token cannot push to it. Either allow it or publish through a different flow.

### (b2) First push only: a short-lived bootstrap token

Both repos add a file in `.github/workflows/`. GitHub refuses a push that creates or changes a workflow file unless the token also has the **Workflows** permission. The publish token from (b) deliberately lacks it, so agents can never change a workflow. Use a second token for the first push, and again for any later push that edits a workflow.

1. Create a fine-grained token as in (b), for the same two repositories. Permissions: **Contents: Read and write** and **Workflows: Read and write**. Expiry: 1 day.
2. Store it in the keychain:

   ```
   security add-generic-password -a "$USER" -s MY_SITES_BOOTSTRAP_TOKEN -w
   ```

3. Push this branch. The credential helper reads the keychain, so the token is never in the command or the shell history. The empty `credential.helper=` first switches off the stored work login:

   ```
   git -c credential.helper= -c credential.helper='!f() { echo username=saurabnigam; echo "password=$(security find-generic-password -w -s MY_SITES_BOOTSTRAP_TOKEN)"; }; f' push -u origin add-showcase-publishing
   ```

   For `family-apps`, first run `git remote add origin https://github.com/saurabnigam/family-apps.git`, then the same command with `push -u origin main`.

4. Delete the token on GitHub (Settings → Developer settings → Fine-grained tokens), then remove it from the keychain:

   ```
   security delete-generic-password -s MY_SITES_BOOTSTRAP_TOKEN
   ```

### (c) Set the commit identity

Commits made by `publish.sh` never use the global git identity (it is a work identity). Set:

```
export MY_SITES_GIT_NAME="Saurabh Nigam"
export MY_SITES_GIT_EMAIL="<your GitHub noreply address>"
```

Use the noreply address shown in GitHub → Settings → Emails. If unset, the defaults are `Saurabh Nigam` and `saurabnigam@users.noreply.github.com`. If commits do not show your avatar on GitHub, the address is not the one GitHub expects; copy it from Settings → Emails.

### (d) Install the skill

```
scripts/install-skill.sh              # ~/.claude/skills and ~/.agents/skills
scripts/install-skill.sh --only claude
scripts/install-skill.sh --uninstall
```

It copies (it does not symlink), so re-run it after you change the skill.

### (e) After you buy a domain

1. Settings → Pages → Custom domain: enter it, and enforce HTTPS once GitHub offers it.
2. Add the DNS records GitHub asks for at your registrar.
3. Export the new base URL so `publish.sh` prints the right link:

   ```
   export MY_SITES_PUBLIC_URL="https://your-domain.example"
   ```

   Also update the URL in `llms.txt` and `skills/publish-to-my-sites/SKILL.md`, then re-run `scripts/install-skill.sh`.

## Local preview and tests

```
python3 scripts/build_site.py --out dist showcase && python3 -m http.server 8000 --directory dist
```

Then open http://localhost:8000/. To run the checks that CI runs:

```
python3 -m unittest discover -s tests -v
python3 scripts/scan_secrets.py showcase
python3 scripts/build_site.py --check showcase
bash skills/publish-to-my-sites/test_publish.sh
```

The last command is an end-to-end test of `publish.sh` against a local bare repo. It makes no network call and needs no token.

Requirements: Python 3.9 or newer (standard library only), bash and git.
