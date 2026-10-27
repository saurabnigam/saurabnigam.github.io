---
name: publish-to-my-sites
description: Publish, share, put online or deploy a self-contained HTML page or small app to Saurabh's own sites. Use when he asks to publish a page to his showcase or resume site (public), or to the family site or family apps (private, login required), or says "put this online", "share this page", "deploy this app", "add this to my showcase", "add this to the family apps".
---

# Publish to my sites

You publish one folder per page by running `publish.sh`. It pushes one commit to a git repo. A deploy then runs by itself.

## The two targets

| | `public` | `family` |
|---|---|---|
| Name | Showcase on the resume site | Family apps |
| Who can see it | Everyone on the internet, and search engines | Only people on the family list, after an email login |
| URL | `https://saurabnigam.github.io/showcase/<slug>/` | `https://family-apps.pages.dev/apps/<slug>/` |
| May contain | Work you are happy to show anyone | Family-only content |
| Never contains | Phone, home address, family names or photos, ID numbers, financial or health data, secrets | Secrets, passwords, ID numbers, bank or card numbers, medical records |

Use the URL that `publish.sh` prints. The base URL changes when a custom domain goes live.

## Choose the target

- The human says public, showcase, resume or portfolio: use `public` with `--confirm-public`.
- The human says family, friends, private or home: use `family`.
- Anything else: ask ONE question: "Public showcase or the private family site?"
- Never choose `public` on your own. Never add `--confirm-public` unless the human asked for a public page in this conversation.

## Page contract

- One folder with a self-contained `index.html`. Inline CSS and JavaScript.
- Assets (images, data) go in the same folder, with relative paths.
- In `<head>`: `<title>`, `<meta name="description" content="...">`, `<meta name="created" content="YYYY-MM-DD">`. Optional: `<meta name="draft" content="true">` (published, not listed).
- No trackers, analytics, CDN scripts or external fonts.
- File names use only `A-Z a-z 0-9 . _ - /`. No symlinks, no file over 25 MiB, at most 500 files. Names starting with `.` are skipped.
- The slug is lower-case letters, digits and single hyphens, for example `trip-2026`.

## Before you publish

1. Open `index.html` as a plain file. It must render with no server and no network.
2. Read the page and its assets for secrets (tokens, keys, passwords). The script also scans, but you check first.
3. For `public`, check the page against the "never contains" list above. If unsure, stop and ask.

## Commands

Run from this skill's folder, or by full path. Do a dry run first, then the real run.

```
publish.sh --dry-run [--confirm-public] <public|family> <slug> <source_dir>
publish.sh [--confirm-public] [-m "message"] <public|family> <slug> <source_dir>
publish.sh --remove [--confirm-public] <public|family> <slug>
publish.sh --list <public|family>
```

- Updating: run the same command again. The whole folder is replaced, so deleted files disappear.
- "no changes" means the live copy already matches.
- Exit code 2 means a public command was missing `--confirm-public`. Do not retry with the flag unless the human asked for a public page.

## Authentication

- `publish.sh` gets a fine-grained token for the personal GitHub account from `MY_SITES_GH_TOKEN`, or from the macOS keychain item `MY_SITES_GH_TOKEN`. Never read, print or export the token yourself.
- NEVER use `gh auth`, `gh` commands, or the default git login on this machine. That login is a work account.
- If the token is missing, stop. Tell the human to create it and export it, as described in the README of `saurabnigam/saurabnigam.github.io`, section "One-time setup", step (b). Do not look for a token elsewhere.

## After publishing

- Tell the human the URL that `publish.sh` printed.
- For `family`, say that opening it needs a family login.
- The page and the listing go live on the next deploy, usually in 1 to 3 minutes. If the URL gives a 404 right away, wait and retry.
