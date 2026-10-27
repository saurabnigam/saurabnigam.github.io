#!/usr/bin/env python3
"""Build the deployable static site and generate section listings.

    python3 scripts/build_site.py [--root DIR] [--out DIR] [--check] \
        SECTION[:LISTING_FILE] [...]

SECTION is a top-level directory (for example "showcase" or "apps"). Every
immediate sub-directory with a slug-style name and an index.html is a page.
The listing is written into LISTING_FILE (default SECTION/index.html), between
the markers <!-- listing:start --> and <!-- listing:end -->. A JSON index is
written to OUT/SECTION/index.json.

The source tree is never modified. Listings are generated in the copy under
--out only. With --check nothing is written; the script only validates.

Python standard library only. Works on Python 3.9 and later.
"""
from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import posixpath
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
SECTION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

# Top-level names that are never deployed. Any name starting with "." is
# excluded at every depth.
EXCLUDED_TOP = frozenset(
    [
        "scripts",
        "tests",
        "docs",
        "skills",
        "dist",
        "README.md",
        "_config.yml",
        "node_modules",
        "__pycache__",
    ]
)

MAX_FILE_BYTES = 25 * 1024 * 1024  # Cloudflare Pages per-file limit
MAX_FILES = 20000  # Cloudflare Pages per-deployment file limit
META_READ_BYTES = 1024 * 1024  # metadata must appear in the first MiB

LISTING_START = "<!-- listing:start -->"
LISTING_END = "<!-- listing:end -->"

# One left-to-right pass over the page. A comment, script or style block is
# skipped, a <title> or <meta> tag is captured. Whichever starts first wins, so
# text inside a quoted attribute value is never mistaken for markup.
TOKEN_RE = re.compile(
    r"<!--.*?-->"
    r"|<(script|style)\b.*?</\1\s*>"
    r"|<title(?:\s[^>]*)?>(.*?)</title\s*>"
    r"|<meta\b((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>",
    re.I | re.S,
)
ATTR_RE = re.compile(
    r"([^\s\"'<>/=]+)(?:\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'=<>`]+)))?"
)


class Reporter:
    """Prints problems as they happen and counts errors.

    problem() is a warning in build mode and an error in --check mode.
    error() is always an error.
    """

    def __init__(self, strict: bool) -> None:
        self.strict = strict
        self.errors = 0

    def error(self, message: str) -> None:
        self.errors += 1
        print("error: " + message, file=sys.stderr)

    def problem(self, message: str) -> None:
        if self.strict:
            self.error(message)
        else:
            print("warning: " + message, file=sys.stderr)


@dataclass
class Page:
    slug: str
    title: str
    description: Optional[str]
    created: Optional[str]
    draft: bool


@dataclass
class SectionPlan:
    section: str
    listing_rel: str
    pages: List[Page]
    listing_text: str  # new content of the listing file ("" until rendered)

    @property
    def listed(self) -> List[Page]:
        return sort_pages([p for p in self.pages if not p.draft])

    @property
    def drafts(self) -> int:
        return sum(1 for p in self.pages if p.draft)


# ---------------------------------------------------------------- metadata


def clean_text(value: str) -> str:
    return " ".join(html.unescape(value).split())


def parse_date(value: str) -> Optional[datetime.date]:
    if not DATE_RE.fullmatch(value):
        return None
    try:
        return datetime.date(int(value[0:4]), int(value[5:7]), int(value[8:10]))
    except ValueError:
        return None


def parse_meta_attrs(raw: str) -> Dict[str, str]:
    attrs: Dict[str, str] = {}
    for attr in ATTR_RE.finditer(raw):
        key = attr.group(1).lower()
        value = next((g for g in attr.groups()[1:] if g is not None), None)
        if value is not None and key not in attrs:
            attrs[key] = value
    return attrs


def extract_head(text: str) -> Tuple[Optional[str], Dict[str, str]]:
    """Return (raw title or None, {meta name in lower case: content}).

    The first <title> and the first <meta> of each name win.
    """
    title: Optional[str] = None
    metas: Dict[str, str] = {}
    for token in TOKEN_RE.finditer(text):
        if token.group(2) is not None:
            if title is None:
                title = token.group(2)
        elif token.group(3) is not None:
            attrs = parse_meta_attrs(token.group(3))
            name = attrs.get("name")
            content = attrs.get("content")
            if name is not None and content is not None:
                metas.setdefault(name.strip().lower(), content)
    return title, metas


def read_page_source(path: str) -> str:
    with open(path, "rb") as handle:
        data = handle.read(META_READ_BYTES)
    return data.decode("utf-8-sig", errors="replace")


def load_page(root: str, section: str, slug: str, reporter: Reporter) -> Page:
    label = "%s/%s" % (section, slug)
    text = read_page_source(os.path.join(root, section, slug, "index.html"))

    raw_title, metas = extract_head(text)
    title = clean_text(raw_title) if raw_title is not None else ""
    if not title:
        reporter.problem("%s: missing <title>; using the slug as title" % label)
        title = slug

    description: Optional[str] = None
    if "description" in metas:
        description = clean_text(metas["description"]) or None

    created: Optional[str] = None
    if "created" in metas:
        raw = metas["created"].strip()
        if parse_date(raw) is None:
            reporter.problem(
                "%s: invalid created date %r (expected a real YYYY-MM-DD date)" % (label, raw)
            )
        else:
            created = raw

    draft = metas.get("draft", "").strip().lower() == "true"
    return Page(slug=slug, title=title, description=description, created=created, draft=draft)


def sort_pages(pages: List[Page]) -> List[Page]:
    def key(page: Page) -> Tuple[int, int, str, str]:
        if page.created is not None:
            parsed = parse_date(page.created)
            ordinal = parsed.toordinal() if parsed is not None else 0
            return (0, -ordinal, page.title.casefold(), page.slug)
        return (1, 0, page.title.casefold(), page.slug)

    return sorted(pages, key=key)


# --------------------------------------------------------------- discovery


def discover_pages(root: str, section: str, reporter: Reporter) -> List[Page]:
    section_dir = os.path.join(root, section)
    pages: List[Page] = []
    for name in sorted(os.listdir(section_dir)):
        if name.startswith(".") or name.startswith("_"):
            continue
        path = os.path.join(section_dir, name)
        if os.path.islink(path) or not os.path.isdir(path):
            continue
        index_path = os.path.join(path, "index.html")
        # A symlinked index.html is not copied, so it must not be listed.
        has_index = os.path.isfile(index_path) and not os.path.islink(index_path)
        if not SLUG_RE.fullmatch(name):
            if has_index:
                reporter.problem(
                    "%s/%s: not a valid slug (use lower-case letters, digits and single "
                    "hyphens); page skipped" % (section, name)
                )
            continue
        if not has_index:
            continue
        pages.append(load_page(root, section, name, reporter))
    return pages


# ------------------------------------------------------------- tree walk


def collect_tree(root: str, reporter: Reporter) -> List[List[str]]:
    """List every deployable file as a list of path parts, in sorted order."""
    files: List[List[str]] = []

    def walk(abs_dir: str, parts: List[str]) -> None:
        with os.scandir(abs_dir) as entries:
            ordered = sorted(entries, key=lambda entry: entry.name)
        for entry in ordered:
            name = entry.name
            if name.startswith("."):
                continue
            if not parts and name in EXCLUDED_TOP:
                continue
            here = parts + [name]
            if entry.is_symlink():
                reporter.problem("symlink skipped: %s" % "/".join(here))
            elif entry.is_dir(follow_symlinks=False):
                walk(entry.path, here)
            elif entry.is_file(follow_symlinks=False):
                files.append(here)
            else:
                reporter.problem("special file skipped: %s" % "/".join(here))

    walk(root, [])
    return files


# ----------------------------------------------------------------- listing


def render_block(section: str, pages: List[Page], indent: str) -> str:
    lines = ['<ul class="listing">']
    if not pages:
        lines.append('  <li class="empty">Nothing here yet.</li>')
    for page in pages:
        href = html.escape("/%s/%s/" % (section, page.slug), quote=True)
        item = '<li><a href="%s">%s</a>' % (href, html.escape(page.title, quote=True))
        if page.created:
            item += ' <span class="meta">%s</span>' % html.escape(page.created, quote=True)
        if page.description:
            item += "<p>%s</p>" % html.escape(page.description, quote=True)
        lines.append("  " + item + "</li>")
    lines.append("</ul>")
    return "\n".join(indent + line for line in lines)


def splice_listing(text: str, section: str, pages: List[Page]) -> str:
    """Replace everything between the two markers. Raises ValueError."""
    starts = text.count(LISTING_START)
    ends = text.count(LISTING_END)
    if starts != 1 or ends != 1:
        raise ValueError(
            "expected exactly one %s and one %s marker, found %d and %d"
            % (LISTING_START, LISTING_END, starts, ends)
        )
    start = text.index(LISTING_START)
    end = text.index(LISTING_END)
    if end < start:
        raise ValueError("the listing end marker comes before the listing start marker")
    line_start = text.rfind("\n", 0, start) + 1
    prefix = text[line_start:start]
    indent = prefix if prefix.strip() == "" else ""
    block = render_block(section, pages, indent)
    return text[: start + len(LISTING_START)] + "\n" + block + "\n" + indent + text[end:]


def index_json(section: str, pages: List[Page]) -> str:
    items = [
        {
            "slug": page.slug,
            "url": "/%s/%s/" % (section, page.slug),
            "title": page.title,
            "description": page.description,
            "created": page.created,
        }
        for page in pages
    ]
    return json.dumps(items, ensure_ascii=False, indent=2) + "\n"


def plural(count: int, word: str) -> str:
    return "%d %s%s" % (count, word, "" if count == 1 else "s")


# ------------------------------------------------------------- orchestration


def parse_spec(spec: str) -> Tuple[str, str]:
    section, _, listing = spec.partition(":")
    if not SECTION_RE.fullmatch(section):
        raise ValueError(
            "invalid section %r (use a single top-level directory name, no slashes)" % section
        )
    if section in EXCLUDED_TOP:
        raise ValueError("section %r is excluded from the build" % section)
    if ":" in spec and listing == "":
        raise ValueError("empty listing file in %r" % spec)
    if listing == "":
        listing = section + "/index.html"
    normal = posixpath.normpath(listing)
    if (
        listing.startswith("/")
        or listing.endswith("/")
        or "\\" in listing
        or normal == "."
        or normal == ".."
        or normal.startswith("../")
    ):
        raise ValueError("invalid listing file %r (must be a path inside the repo)" % listing)
    return section, normal


def listing_is_excluded(listing_rel: str) -> bool:
    parts = listing_rel.split("/")
    return parts[0] in EXCLUDED_TOP or any(p.startswith(".") for p in parts)


def plan_section(root: str, section: str, listing_rel: str, reporter: Reporter) -> Optional[SectionPlan]:
    section_dir = os.path.join(root, section)
    if os.path.islink(section_dir) or not os.path.isdir(section_dir):
        reporter.error("section directory not found: %s" % section)
        return None
    if listing_is_excluded(listing_rel):
        reporter.error("%s: listing file %s is excluded from the build" % (section, listing_rel))
        return None
    listing_path = os.path.join(root, *listing_rel.split("/"))
    if os.path.islink(listing_path) or not os.path.isfile(listing_path):
        reporter.error("%s: listing file not found: %s" % (section, listing_rel))
        return None

    pages = discover_pages(root, section, reporter)

    try:
        with open(listing_path, "rb") as handle:
            text = handle.read().decode("utf-8")
    except UnicodeDecodeError:
        reporter.error("%s: listing file is not valid UTF-8: %s" % (section, listing_rel))
        return None
    plan = SectionPlan(section=section, listing_rel=listing_rel, pages=pages, listing_text="")
    try:
        plan.listing_text = splice_listing(text, section, plan.listed)
    except ValueError as exc:
        reporter.error("%s: %s: %s" % (section, listing_rel, exc))
        return None
    return plan


def check_limits(
    root: str, files: List[List[str]], sections: List[str], reporter: Reporter
) -> None:
    if len(files) > MAX_FILES:
        reporter.problem(
            "the site has %d files; the limit is %d files per deployment" % (len(files), MAX_FILES)
        )
    for parts in files:
        if parts[0] not in sections:
            continue
        size = os.path.getsize(os.path.join(root, *parts))
        if size > MAX_FILE_BYTES:
            reporter.problem(
                "%s is %d bytes; the limit is 25 MiB per file" % ("/".join(parts), size)
            )


def check_output_dir(root: str, out: str) -> Optional[str]:
    """Return an error message if deleting `out` could harm the source."""
    root_path, out_path = Path(root), Path(out)
    if out_path == root_path or out_path in root_path.parents:
        return "--out %s is the repo root or contains it; refusing to delete it" % out
    if root_path in out_path.parents:
        top = out_path.relative_to(root_path).parts[0]
        if not (top in EXCLUDED_TOP or top.startswith(".")):
            return (
                "--out %s is inside the source tree (at %s/); use dist/ or a directory "
                "outside the repo" % (out, top)
            )
    return None


def write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(text.encode("utf-8"))


def build(root: str, out: str, files: List[List[str]], plans: List[SectionPlan]) -> None:
    if os.path.lexists(out):
        shutil.rmtree(out)
    os.makedirs(out)
    for parts in files:
        target = os.path.join(out, *parts)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(os.path.join(root, *parts), target)
    for plan in plans:
        write_text(os.path.join(out, *plan.listing_rel.split("/")), plan.listing_text)
        write_text(
            os.path.join(out, plan.section, "index.json"), index_json(plan.section, plan.listed)
        )


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="build_site.py",
        description="Build the deployable site and generate section listings.",
    )
    parser.add_argument("--root", help="repository root (default: parent of scripts/)")
    parser.add_argument("--out", help="output directory; deleted and recreated (required unless --check)")
    parser.add_argument(
        "--check", action="store_true", help="validate only; write nothing; exit 1 on any problem"
    )
    parser.add_argument(
        "sections", nargs="+", metavar="SECTION[:LISTING_FILE]", help="section to list"
    )
    args = parser.parse_args(argv)

    if not args.check and not args.out:
        parser.error("--out is required unless --check is given")

    specs: List[Tuple[str, str]] = []
    for raw in args.sections:
        try:
            specs.append(parse_spec(raw))
        except ValueError as exc:
            parser.error(str(exc))
    names = [section for section, _ in specs]
    if len(set(names)) != len(names):
        parser.error("a section was given more than once")
    listings = [listing for _, listing in specs]
    if len(set(listings)) != len(listings):
        shared = sorted(set(name for name in listings if listings.count(name) > 1))
        print(
            "error: two sections write to the same listing file: %s" % ", ".join(shared),
            file=sys.stderr,
        )
        return 1

    root = os.path.realpath(
        args.root if args.root else os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir)
    )
    if not os.path.isdir(root):
        print("error: --root is not a directory: %s" % root, file=sys.stderr)
        return 1

    out = None
    if not args.check:
        out = os.path.realpath(args.out)
        message = check_output_dir(root, out)
        if message:
            print("error: " + message, file=sys.stderr)
            return 1
        if os.path.lexists(out) and not os.path.isdir(out):
            print("error: --out exists and is not a directory: %s" % out, file=sys.stderr)
            return 1

    reporter = Reporter(strict=args.check)
    files = collect_tree(root, reporter)
    check_limits(root, files, names, reporter)

    plans: List[SectionPlan] = []
    for section, listing in specs:
        plan = plan_section(root, section, listing, reporter)
        if plan is not None:
            plans.append(plan)

    if reporter.errors:
        print("%s: %s found; nothing was written" % (
            "check failed" if args.check else "build failed", plural(reporter.errors, "error")
        ), file=sys.stderr)
        return 1

    if not args.check:
        assert out is not None
        build(root, out, files, plans)

    for plan in plans:
        count = len(plan.listed)
        if args.check:
            print(
                "%s: %s would be listed, %s skipped (check only)"
                % (plan.section, plural(count, "page"), plural(plan.drafts, "draft"))
            )
        else:
            print(
                "%s: %s listed, %s skipped"
                % (plan.section, plural(count, "page"), plural(plan.drafts, "draft"))
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
