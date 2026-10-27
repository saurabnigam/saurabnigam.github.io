#!/usr/bin/env python3
"""Scan files for secrets before they are published.

    python3 scripts/scan_secrets.py PATH [PATH ...]

Scans text files recursively. Skips binary files (a NUL byte in the first
8 KiB), files larger than 5 MiB, and symlinks, and says so on stderr. On a hit
it prints "path:line: <pattern name> <first 4 chars>..." and never the whole
match. Exit codes: 0 nothing found, 1 a secret was found, 2 a path could not
be scanned.

The same patterns are used by skills/publish-to-my-sites/publish.sh. Keep the
two lists in step.

Python standard library only. Works on Python 3.9 and later.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import List, Optional, Tuple

MAX_BYTES = 5 * 1024 * 1024
BINARY_PROBE_BYTES = 8 * 1024

PATTERNS = [
    ("github-classic-token", re.compile(rb"ghp_[A-Za-z0-9]{36}")),
    ("github-fine-grained-token", re.compile(rb"github_pat_[A-Za-z0-9_]{22,}")),
    ("github-oauth-token", re.compile(rb"gh[osu]_[A-Za-z0-9]{36}")),
    ("aws-access-key-id", re.compile(rb"AKIA[0-9A-Z]{16}")),
    ("anthropic-api-key", re.compile(rb"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-style-key", re.compile(rb"sk-[A-Za-z0-9]{32,}")),
    ("slack-token", re.compile(rb"xox[abprs]-[A-Za-z0-9-]{10,}")),
    ("private-key", re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

ELLIPSIS = "…"


class Counters:
    def __init__(self) -> None:
        self.binary = 0
        self.large = 0
        self.symlinks = 0
        self.unreadable = 0


def scan_bytes(data: bytes) -> List[Tuple[int, str, str]]:
    """Return (line number, pattern name, first four characters) per hit."""
    hits: List[Tuple[int, str, str]] = []
    for number, line in enumerate(data.split(b"\n"), start=1):
        for name, pattern in PATTERNS:
            for match in pattern.finditer(line):
                prefix = match.group(0)[:4].decode("ascii", errors="replace")
                hits.append((number, name, prefix))
    return hits


def scan_file(path: str, counters: Counters) -> List[Tuple[int, str, str]]:
    try:
        if os.path.islink(path):
            counters.symlinks += 1
            return []
        if os.path.getsize(path) > MAX_BYTES:
            counters.large += 1
            return []
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        counters.unreadable += 1
        print("error: cannot read %s: %s" % (path, exc.strerror or exc), file=sys.stderr)
        return []
    if b"\x00" in data[:BINARY_PROBE_BYTES]:
        counters.binary += 1
        return []
    return scan_bytes(data)


def iter_files(path: str, counters: Counters):
    if os.path.isfile(path):
        yield path
        return
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        dirnames.sort()
        for name in list(dirnames):
            if os.path.islink(os.path.join(dirpath, name)):
                counters.symlinks += 1
                dirnames.remove(name)
        for name in sorted(filenames):
            yield os.path.join(dirpath, name)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scan_secrets.py", description="Scan files for secrets before publishing."
    )
    parser.add_argument("paths", nargs="+", metavar="PATH", help="file or directory to scan")
    args = parser.parse_args(argv)

    counters = Counters()
    found = 0
    bad_paths = 0
    for root in args.paths:
        if not os.path.exists(root):
            print("error: path does not exist: %s" % root, file=sys.stderr)
            bad_paths += 1
            continue
        for path in iter_files(root, counters):
            for number, name, prefix in scan_file(path, counters):
                found += 1
                print("%s:%d: %s %s%s" % (path, number, name, prefix, ELLIPSIS))

    skipped = []
    if counters.binary:
        skipped.append("%d binary file(s)" % counters.binary)
    if counters.large:
        skipped.append("%d file(s) over 5 MiB" % counters.large)
    if counters.symlinks:
        skipped.append("%d symlink(s)" % counters.symlinks)
    if skipped:
        print("note: not scanned: " + ", ".join(skipped), file=sys.stderr)

    if found:
        return 1
    if bad_paths or counters.unreadable:
        return 2
    print("no secrets found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
