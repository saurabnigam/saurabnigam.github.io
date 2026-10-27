"""Tests for scripts/build_site.py. Run from the repo root:

    python3 -m unittest discover -s tests -v

Every test works in a temp dir whose path contains a space.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "scripts", "build_site.py")

LISTING_PAGE = (
    "<!doctype html><html><head><title>Listing</title></head><body>\n"
    "<!-- listing:start -->\n<li class=\"empty\">Nothing here yet.</li>\n"
    "<!-- listing:end -->\n</body></html>\n"
)


def page_html(title="Page", description=None, created=None, draft=False, extra=""):
    head = []
    if title is not None:
        head.append("<title>%s</title>" % title)
    if description is not None:
        head.append('<meta name="description" content="%s">' % description)
    if created is not None:
        head.append('<meta name="created" content="%s">' % created)
    if draft:
        head.append('<meta name="draft" content="true">')
    return "<!doctype html><html><head>%s</head><body>hi%s</body></html>\n" % (
        "\n".join(head),
        extra,
    )


def write(base, rel, content="x", mode="w"):
    path = os.path.join(base, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if "b" in mode:
        with open(path, mode) as f:
            f.write(content)
    else:
        with open(path, mode, encoding="utf-8", newline="") as f:
            f.write(content)
    return path


def read(base, rel):
    with open(os.path.join(base, *rel.split("/")), encoding="utf-8", newline="") as f:
        return f.read()


def tree_hashes(base):
    """Map of relative path -> sha256 for every file under base."""
    result = {}
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames.sort()
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, base)
            with open(full, "rb") as f:
                result[rel] = hashlib.sha256(f.read()).hexdigest()
    return result


class BuildTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="build site ")
        self.addCleanup(self._tmp.cleanup)
        self.base = os.path.realpath(self._tmp.name)
        self.root = os.path.join(self.base, "my repo")
        self.out = os.path.join(self.base, "out dir")
        os.makedirs(self.root)

    def run_build(self, *args, **kwargs):
        cmd = [sys.executable, "-B", SCRIPT, "--root", kwargs.get("root", self.root)]
        cmd.extend(args)
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True
        )
        return proc

    def build(self, *sections):
        return self.run_build("--out", self.out, *sections)

    def check(self, *sections):
        return self.run_build("--check", *sections)

    def make_showcase(self, pages):
        """pages: dict slug -> html. Creates showcase/index.html with markers."""
        write(self.root, "showcase/index.html", LISTING_PAGE)
        for slug, content in pages.items():
            write(self.root, "showcase/%s/index.html" % slug, content)

    def slugs_in_json(self, section="showcase"):
        data = json.loads(read(self.out, section + "/index.json"))
        return [item["slug"] for item in data]


class TestOrderingAndListing(BuildTestCase):
    def test_orders_by_created_desc_then_undated_then_title_then_slug(self):
        self.make_showcase(
            {
                "old": page_html("Old", created="2026-01-01"),
                "new": page_html("Bee", created="2026-03-01"),
                "tie": page_html("aardvark", created="2026-03-01"),
                "zed": page_html("zeta"),
                "alpha": page_html("Alpha"),
                "dup-b": page_html("Same"),
                "dup-a": page_html("same"),
            }
        )
        proc = self.build("showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            self.slugs_in_json(),
            ["tie", "new", "old", "alpha", "dup-a", "dup-b", "zed"],
        )

    def test_listing_markup_exact(self):
        self.make_showcase(
            {
                "full": page_html("Full", description="Has all", created="2026-02-02"),
                "bare": page_html("Bare"),
                "desc-only": page_html("Desc", description="Only text"),
                "date-only": page_html("Dated", created="2026-01-05"),
            }
        )
        proc = self.build("showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        html_out = read(self.out, "showcase/index.html")
        expected = (
            "<!-- listing:start -->\n"
            '<ul class="listing">\n'
            '  <li><a href="/showcase/full/">Full</a> <span class="meta">2026-02-02</span>'
            "<p>Has all</p></li>\n"
            '  <li><a href="/showcase/date-only/">Dated</a> <span class="meta">2026-01-05</span></li>\n'
            '  <li><a href="/showcase/bare/">Bare</a></li>\n'
            '  <li><a href="/showcase/desc-only/">Desc</a><p>Only text</p></li>\n'
            "</ul>\n"
            "<!-- listing:end -->"
        )
        self.assertIn(expected, html_out)

    def test_empty_section_uses_placeholder_and_empty_json(self):
        self.make_showcase({})
        proc = self.build("showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(
            '<ul class="listing">\n  <li class="empty">Nothing here yet.</li>\n</ul>',
            read(self.out, "showcase/index.html"),
        )
        self.assertEqual(read(self.out, "showcase/index.json"), "[]\n")
        self.assertIn("showcase: 0 pages listed, 0 drafts skipped", proc.stdout)

    def test_title_and_description_are_html_escaped(self):
        self.make_showcase(
            {
                "raw": page_html('A <b> & "c"', description="<script>alert(1)</script> & more"),
                "ent": page_html(
                    "A &lt;b&gt; &amp; &quot;c&quot; it&#39;s", description="x &lt;i&gt; y"
                ),
            }
        )
        proc = self.build("showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        html_out = read(self.out, "showcase/index.html")
        self.assertIn("A &lt;b&gt; &amp; &quot;c&quot;</a>", html_out)
        self.assertIn("<p>&lt;script&gt;alert(1)&lt;/script&gt; &amp; more</p>", html_out)
        self.assertIn("A &lt;b&gt; &amp; &quot;c&quot; it&#x27;s</a>", html_out)
        self.assertIn("<p>x &lt;i&gt; y</p>", html_out)
        self.assertNotIn("<b>", html_out)
        self.assertNotIn("<script>", html_out)
        data = {i["slug"]: i for i in json.loads(read(self.out, "showcase/index.json"))}
        self.assertEqual(data["raw"]["title"], 'A <b> & "c"')
        self.assertEqual(data["ent"]["title"], 'A <b> & "c" it\'s')

    def test_metadata_parsing_variants(self):
        self.make_showcase(
            {
                "single": (
                    "<HTML><HEAD><TITLE>\n  Spaced   out\n title </TITLE>\n"
                    "<META CONTENT='It\"s fine' NAME='Description'>\n"
                    "<meta content=\"2026-04-01\" name=\"created\" />\n"
                    "<meta name='description' content='second one ignored'>\n"
                    "</HEAD><BODY></BODY></HTML>\n"
                ),
                "gt-in-attr": (
                    '<head><title>Gt</title><meta name="description" content="a > b">'
                    "</head>"
                ),
                "commented": (
                    "<head><!-- <title>Wrong</title> --><title>Right</title>"
                    "<script>var s = '<title>Nope</title>';</script></head>"
                ),
            }
        )
        proc = self.build("showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = {i["slug"]: i for i in json.loads(read(self.out, "showcase/index.json"))}
        self.assertEqual(data["single"]["title"], "Spaced out title")
        self.assertEqual(data["single"]["description"], 'It"s fine')
        self.assertEqual(data["single"]["created"], "2026-04-01")
        self.assertEqual(data["gt-in-attr"]["description"], "a > b")
        self.assertEqual(data["commented"]["title"], "Right")

    def test_non_ascii_title_kept_in_json_and_html(self):
        self.make_showcase({"uni": page_html("Café — 日本")})
        proc = self.build("showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        raw = read(self.out, "showcase/index.json")
        self.assertIn("Café — 日本", raw)
        self.assertNotIn("\\u", raw)
        self.assertIn("Café — 日本</a>", read(self.out, "showcase/index.html"))


class TestIndexJson(BuildTestCase):
    def test_index_json_exact_content(self):
        self.make_showcase(
            {
                "a-page": page_html("A page", description="Desc", created="2026-05-05"),
                "b-page": page_html("B page"),
            }
        )
        proc = self.build("showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        expected = (
            "[\n"
            "  {\n"
            '    "slug": "a-page",\n'
            '    "url": "/showcase/a-page/",\n'
            '    "title": "A page",\n'
            '    "description": "Desc",\n'
            '    "created": "2026-05-05"\n'
            "  },\n"
            "  {\n"
            '    "slug": "b-page",\n'
            '    "url": "/showcase/b-page/",\n'
            '    "title": "B page",\n'
            '    "description": null,\n'
            '    "created": null\n'
            "  }\n"
            "]\n"
        )
        self.assertEqual(read(self.out, "showcase/index.json"), expected)


class TestDrafts(BuildTestCase):
    def test_draft_is_copied_but_not_listed(self):
        self.make_showcase(
            {
                "live": page_html("Live", created="2026-01-01"),
                "secret-wip": page_html("WIP", draft=True),
            }
        )
        proc = self.build("showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.slugs_in_json(), ["live"])
        self.assertNotIn("secret-wip", read(self.out, "showcase/index.html"))
        self.assertTrue(os.path.isfile(os.path.join(self.out, "showcase", "secret-wip", "index.html")))
        self.assertIn("showcase: 1 page listed, 1 draft skipped", proc.stdout)

    def test_summary_line_plural(self):
        self.make_showcase(
            {
                "one": page_html("One"),
                "two": page_html("Two"),
                "three": page_html("Three"),
                "wip": page_html("Wip", draft=True),
            }
        )
        proc = self.build("showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("showcase: 3 pages listed, 1 draft skipped\n", proc.stdout)


class TestDiscoveryRules(BuildTestCase):
    def test_invalid_slug_warns_in_build_and_fails_in_check(self):
        self.make_showcase({"good": page_html("Good")})
        write(self.root, "showcase/Bad_Slug/index.html", page_html("Bad"))
        proc = self.build("showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Bad_Slug", proc.stderr)
        self.assertIn("warning", proc.stderr)
        self.assertEqual(self.slugs_in_json(), ["good"])
        proc = self.check("showcase")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("error", proc.stderr)
        self.assertIn("Bad_Slug", proc.stderr)

    def test_underscore_and_dot_dirs_ignored_silently(self):
        self.make_showcase({"good": page_html("Good")})
        write(self.root, "showcase/_drafts/index.html", page_html("Hidden"))
        write(self.root, "showcase/.git-ish/index.html", page_html("Hidden too"))
        proc = self.check("showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        proc = self.build("showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(self.slugs_in_json(), ["good"])

    def test_slug_dir_without_index_html_is_not_a_page(self):
        self.make_showcase({"good": page_html("Good")})
        write(self.root, "showcase/empty-dir/readme.txt", "no index")
        proc = self.check("showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        proc = self.build("showcase")
        self.assertEqual(self.slugs_in_json(), ["good"])

    def test_missing_title_falls_back_to_slug_and_fails_check(self):
        self.make_showcase({"no-title": page_html(None, description="d")})
        proc = self.build("showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("no-title", proc.stderr)
        self.assertIn("title", proc.stderr)
        data = json.loads(read(self.out, "showcase/index.json"))
        self.assertEqual(data[0]["title"], "no-title")
        proc = self.check("showcase")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("error", proc.stderr)

    def test_empty_title_counts_as_missing(self):
        self.make_showcase({"blank": "<title>   </title>"})
        self.assertEqual(self.check("showcase").returncode, 1)

    def test_invalid_created_date(self):
        self.make_showcase(
            {
                "bad-month": page_html("Bad", created="2026-13-45"),
                "bad-shape": page_html("Shape", created="27/10/2026"),
                "good": page_html("Good", created="2026-02-28"),
            }
        )
        proc = self.build("showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("bad-month", proc.stderr)
        self.assertIn("bad-shape", proc.stderr)
        data = {i["slug"]: i for i in json.loads(read(self.out, "showcase/index.json"))}
        self.assertIsNone(data["bad-month"]["created"])
        self.assertIsNone(data["bad-shape"]["created"])
        self.assertEqual(data["good"]["created"], "2026-02-28")
        self.assertEqual(self.check("showcase").returncode, 1)

    def test_leap_day_validation(self):
        self.make_showcase({"leap": page_html("Leap", created="2028-02-29")})
        self.assertEqual(self.check("showcase").returncode, 0)
        self.make_showcase({"leap": page_html("Leap", created="2027-02-29")})
        self.assertEqual(self.check("showcase").returncode, 1)

    def test_missing_section_dir_is_an_error(self):
        write(self.root, "index.html", LISTING_PAGE)
        proc = self.check("showcase:index.html")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("showcase", proc.stderr)


class TestMarkers(BuildTestCase):
    def assert_fails_without_output(self, listing):
        write(self.root, "showcase/index.html", listing)
        write(self.root, "showcase/p/index.html", page_html("P"))
        proc = self.build("showcase")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("marker", proc.stderr)
        self.assertFalse(os.path.exists(self.out), "failed build must not create output")
        self.assertEqual(self.check("showcase").returncode, 1)

    def test_missing_both_markers(self):
        self.assert_fails_without_output("<html><body>no markers</body></html>")

    def test_missing_end_marker(self):
        self.assert_fails_without_output("<!-- listing:start -->\n")

    def test_missing_start_marker(self):
        self.assert_fails_without_output("<!-- listing:end -->\n")

    def test_duplicate_start_marker(self):
        self.assert_fails_without_output(
            "<!-- listing:start -->\n<!-- listing:start -->\n<!-- listing:end -->\n"
        )

    def test_duplicate_end_marker(self):
        self.assert_fails_without_output(
            "<!-- listing:start -->\n<!-- listing:end -->\n<!-- listing:end -->\n"
        )

    def test_end_before_start(self):
        self.assert_fails_without_output("<!-- listing:end -->\n<!-- listing:start -->\n")

    def test_failed_build_keeps_previous_output(self):
        self.make_showcase({"p": page_html("P")})
        self.assertEqual(self.build("showcase").returncode, 0)
        before = tree_hashes(self.out)
        write(self.root, "showcase/index.html", "no markers")
        self.assertEqual(self.build("showcase").returncode, 1)
        self.assertEqual(tree_hashes(self.out), before)

    def test_indented_markers_keep_indentation(self):
        write(
            self.root,
            "showcase/index.html",
            "<body>\n    <!-- listing:start -->\n    old\n    <!-- listing:end -->\n</body>\n",
        )
        write(self.root, "showcase/p/index.html", page_html("P"))
        self.assertEqual(self.build("showcase").returncode, 0)
        self.assertIn(
            '    <!-- listing:start -->\n    <ul class="listing">\n      <li><a href="/showcase/p/">P</a></li>\n    </ul>\n    <!-- listing:end -->',
            read(self.out, "showcase/index.html"),
        )


class TestListingFile(BuildTestCase):
    def test_root_launcher_listing_file(self):
        write(self.root, "index.html", LISTING_PAGE)
        write(self.root, "apps/packing-list/index.html", page_html("Packing list", created="2026-10-27"))
        original = read(self.root, "index.html")
        proc = self.build("apps:index.html")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        launcher = read(self.out, "index.html")
        self.assertIn('<li><a href="/apps/packing-list/">Packing list</a>', launcher)
        self.assertFalse(os.path.exists(os.path.join(self.out, "apps", "index.html")))
        self.assertTrue(os.path.isfile(os.path.join(self.out, "apps", "index.json")))
        self.assertEqual(self.slugs_in_json("apps"), ["packing-list"])
        self.assertEqual(read(self.root, "index.html"), original)
        self.assertIn("apps: 1 page listed, 0 drafts skipped", proc.stdout)

    def test_default_listing_file_is_section_index(self):
        self.make_showcase({"p": page_html("P")})
        self.assertEqual(self.build("showcase").returncode, 0)
        self.assertIn('href="/showcase/p/"', read(self.out, "showcase/index.html"))

    def test_two_sections_in_one_run(self):
        write(self.root, "index.html", LISTING_PAGE)
        write(self.root, "apps/a/index.html", page_html("A"))
        self.make_showcase({"s": page_html("S")})
        proc = self.build("apps:index.html", "showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.slugs_in_json("apps"), ["a"])
        self.assertEqual(self.slugs_in_json("showcase"), ["s"])

    def test_two_sections_sharing_a_listing_file_is_an_error(self):
        write(self.root, "index.html", LISTING_PAGE)
        write(self.root, "apps/a/index.html", page_html("A"))
        write(self.root, "more/b/index.html", page_html("B"))
        proc = self.check("apps:index.html", "more:index.html")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("index.html", proc.stderr)

    def test_listing_file_must_exist(self):
        write(self.root, "apps/a/index.html", page_html("A"))
        proc = self.check("apps:index.html")
        self.assertEqual(proc.returncode, 1)

    def test_listing_file_may_not_escape_root_or_be_excluded(self):
        write(self.root, "apps/a/index.html", page_html("A"))
        write(self.root, "README.md", LISTING_PAGE)
        self.assertNotEqual(self.check("apps:../x.html").returncode, 0)
        self.assertNotEqual(self.check("apps:/abs.html").returncode, 0)
        self.assertEqual(self.check("apps:README.md").returncode, 1)


class TestExclusions(BuildTestCase):
    def test_excluded_names_absent_and_site_files_present(self):
        self.make_showcase({"p": page_html("P")})
        write(self.root, "index.html", "<title>root</title>")
        for rel in [
            ".git/config",
            ".github/workflows/x.yml",
            ".hidden",
            ".nojekyll",
            "scripts/build_site.py",
            "tests/test_x.py",
            "docs/notes.md",
            "skills/s/SKILL.md",
            "dist/old.html",
            "README.md",
            "_config.yml",
            "node_modules/pkg/index.js",
            "__pycache__/x.pyc",
            "showcase/p/.DS_Store",
            "showcase/p/.secret/key.txt",
        ]:
            write(self.root, rel, "x")
        for rel in ["_headers", "robots.txt", "llms.txt", "showcase/p/app.js", "showcase/p/img/a.png"]:
            write(self.root, rel, "keep")
        proc = self.build("showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        built = sorted(tree_hashes(self.out))
        self.assertEqual(
            built,
            sorted(
                [
                    "_headers",
                    "index.html",
                    "llms.txt",
                    "robots.txt",
                    os.path.join("showcase", "index.html"),
                    os.path.join("showcase", "index.json"),
                    os.path.join("showcase", "p", "app.js"),
                    os.path.join("showcase", "p", "img", "a.png"),
                    os.path.join("showcase", "p", "index.html"),
                ]
            ),
        )

    def test_nested_dir_named_like_excluded_top_level_is_kept(self):
        self.make_showcase({"p": page_html("P")})
        write(self.root, "showcase/p/docs/readme.txt", "keep me")
        self.assertEqual(self.build("showcase").returncode, 0)
        self.assertTrue(os.path.isfile(os.path.join(self.out, "showcase", "p", "docs", "readme.txt")))

    def test_out_inside_root_is_not_copied_into_itself(self):
        self.make_showcase({"p": page_html("P")})
        out = os.path.join(self.root, "dist")
        for _ in range(2):
            proc = self.run_build("--out", out, "showcase")
            self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(os.path.exists(os.path.join(out, "dist")))
        self.assertTrue(os.path.isfile(os.path.join(out, "showcase", "p", "index.html")))

    def test_symlinks_are_skipped_with_warning_and_fail_check(self):
        self.make_showcase({"p": page_html("P")})
        outside = os.path.join(self.base, "outside.txt")
        write(self.base, "outside.txt", "outside")
        os.symlink(outside, os.path.join(self.root, "showcase", "p", "link.txt"))
        proc = self.build("showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("symlink", proc.stderr)
        self.assertFalse(os.path.lexists(os.path.join(self.out, "showcase", "p", "link.txt")))
        proc = self.check("showcase")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("symlink", proc.stderr)


class TestSymlinkedIndex(BuildTestCase):
    def test_page_whose_index_html_is_a_symlink_is_not_listed(self):
        self.make_showcase({"good": page_html("Good")})
        real = write(self.base, "real.html", page_html("Linked"))
        os.makedirs(os.path.join(self.root, "showcase", "linked"))
        os.symlink(real, os.path.join(self.root, "showcase", "linked", "index.html"))
        proc = self.build("showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.slugs_in_json(), ["good"])
        self.assertNotIn("linked", read(self.out, "showcase/index.html"))
        self.assertIn("symlink", proc.stderr)
        self.assertEqual(self.check("showcase").returncode, 1)


class TestOutputDirSafety(BuildTestCase):
    def test_stale_output_is_removed(self):
        self.make_showcase({"p": page_html("P")})
        write(self.out, "stale.txt", "old")
        write(self.out, "showcase/gone/index.html", "old")
        self.assertEqual(self.build("showcase").returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.out, "stale.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.out, "showcase", "gone")))

    def test_out_equal_to_root_is_refused(self):
        self.make_showcase({"p": page_html("P")})
        before = tree_hashes(self.root)
        proc = self.run_build("--out", self.root, "showcase")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(tree_hashes(self.root), before)

    def test_out_that_contains_root_is_refused(self):
        self.make_showcase({"p": page_html("P")})
        before = tree_hashes(self.root)
        proc = self.run_build("--out", self.base, "showcase")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(tree_hashes(self.root), before)

    def test_out_inside_source_section_is_refused(self):
        self.make_showcase({"p": page_html("P")})
        before = tree_hashes(self.root)
        proc = self.run_build("--out", os.path.join(self.root, "showcase", "p"), "showcase")
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(tree_hashes(self.root), before)

    def test_out_is_required_unless_check(self):
        self.make_showcase({})
        proc = self.run_build("showcase")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--out", proc.stderr)


class TestSourceUntouchedAndDeterministic(BuildTestCase):
    def populate(self):
        write(self.root, "index.html", LISTING_PAGE)
        write(self.root, "apps/a/index.html", page_html("A", created="2026-01-01"))
        write(self.root, "apps/a/data.bin", b"\x00\x01\x02", mode="wb")
        write(self.root, "apps/draft/index.html", page_html("D", draft=True))
        write(self.root, "scripts/x.py", "print(1)")
        write(self.root, "_headers", "/*\n  X-Test: 1\n")

    def test_source_tree_is_byte_identical_after_build(self):
        self.populate()
        before = tree_hashes(self.root)
        files_before = sorted(
            os.path.relpath(os.path.join(d, f), self.root)
            for d, _, fs in os.walk(self.root)
            for f in fs
        )
        proc = self.build("apps:index.html")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(tree_hashes(self.root), before)
        files_after = sorted(
            os.path.relpath(os.path.join(d, f), self.root)
            for d, _, fs in os.walk(self.root)
            for f in fs
        )
        self.assertEqual(files_after, files_before)

    def test_check_writes_nothing(self):
        self.populate()
        before = tree_hashes(self.root)
        proc = self.run_build("--check", "--out", self.out, "apps:index.html")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(tree_hashes(self.root), before)
        self.assertFalse(os.path.exists(self.out))
        self.assertIn("apps:", proc.stdout)
        self.assertIn("check", proc.stdout)

    def test_idempotent_into_same_and_different_out_dirs(self):
        self.populate()
        self.assertEqual(self.build("apps:index.html").returncode, 0)
        first = tree_hashes(self.out)
        self.assertEqual(self.build("apps:index.html").returncode, 0)
        self.assertEqual(tree_hashes(self.out), first)
        other = os.path.join(self.base, "other out")
        proc = self.run_build("--out", other, "apps:index.html")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(tree_hashes(other), first)

    def test_binary_files_copied_unchanged(self):
        self.populate()
        self.assertEqual(self.build("apps:index.html").returncode, 0)
        with open(os.path.join(self.out, "apps", "a", "data.bin"), "rb") as f:
            self.assertEqual(f.read(), b"\x00\x01\x02")

    def test_crlf_listing_file_outside_markers_is_preserved(self):
        write(
            self.root,
            "showcase/index.html",
            "<p>a</p>\r\n<!-- listing:start -->\r\nold\r\n<!-- listing:end -->\r\n<p>b</p>\r\n",
        )
        self.assertEqual(self.build("showcase").returncode, 0)
        out = read(self.out, "showcase/index.html")
        self.assertTrue(out.startswith("<p>a</p>\r\n<!-- listing:start -->\n"))
        self.assertTrue(out.endswith("<!-- listing:end -->\r\n<p>b</p>\r\n"))


class TestLimits(BuildTestCase):
    def test_file_over_25_mib_fails_check(self):
        self.make_showcase({"big": page_html("Big")})
        path = os.path.join(self.root, "showcase", "big", "blob.bin")
        with open(path, "wb") as f:
            f.truncate(25 * 1024 * 1024 + 1)
        proc = self.check("showcase")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("25 MiB", proc.stderr)
        self.assertIn("blob.bin", proc.stderr)

    def test_file_of_exactly_25_mib_passes_check(self):
        self.make_showcase({"big": page_html("Big")})
        path = os.path.join(self.root, "showcase", "big", "blob.bin")
        with open(path, "wb") as f:
            f.truncate(25 * 1024 * 1024)
        proc = self.check("showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_oversize_file_is_only_a_warning_in_build(self):
        self.make_showcase({"big": page_html("Big")})
        path = os.path.join(self.root, "showcase", "big", "blob.bin")
        with open(path, "wb") as f:
            f.truncate(25 * 1024 * 1024 + 1)
        proc = self.build("showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("warning", proc.stderr)
        self.assertIn("25 MiB", proc.stderr)

    def test_file_count_over_20000_fails_check(self):
        self.make_showcase({"many": page_html("Many")})
        bulk = os.path.join(self.root, "showcase", "many", "bulk")
        os.makedirs(bulk)
        # showcase/index.html + many/index.html + N bulk files = 20001 files.
        count = 20001 - 2
        for i in range(count):
            with open(os.path.join(bulk, "f%05d" % i), "wb"):
                pass
        proc = self.check("showcase")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("20000", proc.stderr)
        os.remove(os.path.join(bulk, "f00000"))
        proc = self.check("showcase")
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_excluded_files_do_not_count_toward_limits(self):
        self.make_showcase({"p": page_html("P")})
        path = os.path.join(self.root, "docs", "huge.bin")
        os.makedirs(os.path.dirname(path))
        with open(path, "wb") as f:
            f.truncate(30 * 1024 * 1024)
        self.assertEqual(self.check("showcase").returncode, 0)


class TestArguments(BuildTestCase):
    def test_bad_section_names_are_rejected(self):
        self.make_showcase({})
        for bad in ["../x", "a/b", ".hidden", "scripts", "docs", "", ":index.html"]:
            proc = self.check(bad)
            self.assertNotEqual(proc.returncode, 0, bad)

    def test_duplicate_section_is_rejected(self):
        self.make_showcase({})
        self.assertNotEqual(self.check("showcase", "showcase").returncode, 0)

    def test_root_defaults_to_parent_of_scripts(self):
        # Without --root the script must use the repo that contains it. The
        # real repos always have a section dir, so use it via --check only.
        proc = subprocess.run(
            [sys.executable, "-B", SCRIPT, "--check", "no-such-section-xyz"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertIn("no-such-section-xyz", proc.stderr)


if __name__ == "__main__":
    unittest.main()
