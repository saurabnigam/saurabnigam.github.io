"""Tests for scripts/scan_secrets.py. Run from the repo root:

    python3 -m unittest discover -s tests -v

Secret-looking values are assembled at run time so this file never contains
a string that the scanner itself would flag.
"""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "scripts", "scan_secrets.py")

# (pattern name, secret value, expected first four characters)
SAMPLES = [
    ("github-classic-token", "ghp_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fH3jL5", "ghp_"),
    ("github-fine-grained-token", "github_pat_" + "11ABCDEFG0abcdefghij_KLMNOPQRSTUVWXYZ0123456789", "gith"),
    ("github-oauth-token", "gho_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fH3jL5", "gho_"),
    ("github-oauth-token", "ghu_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fH3jL5", "ghu_"),
    ("github-oauth-token", "ghs_" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fH3jL5", "ghs_"),
    ("aws-access-key-id", "AKIA" + "ABCDEFGH12345678", "AKIA"),
    ("anthropic-api-key", "sk-ant-" + "api03-abcdefghij_KLMNOPqrst-UVWXYZ", "sk-a"),
    ("openai-style-key", "sk-" + "aB3dE5gH7jK9mN1pQ3sT5vW7yZ9bD1fH", "sk-a"),
    ("slack-token", "xoxb-" + "1234567890-abcdefghij", "xoxb"),
    ("slack-token", "xoxp-" + "1234567890-abcdefghij", "xoxp"),
    ("private-key", "-----BEGIN " + "RSA PRIVATE KEY-----", "----"),
    ("private-key", "-----BEGIN " + "PRIVATE KEY-----", "----"),
    ("private-key", "-----BEGIN " + "OPENSSH PRIVATE KEY-----", "----"),
]

NEAR_MISSES = [
    "ghp_" + "a" * 35,
    "AKIA" + "A" * 15,
    "sk-" + "a" * 31,
    "xoxb-" + "123",
    "github_pat_" + "a" * 21,
    "-----BEGIN " + "CERTIFICATE-----",
]


def run_scan(*paths):
    return subprocess.run(
        [sys.executable, "-B", SCRIPT] + list(paths),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
    )


class ScanTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="scan secrets ")
        self.addCleanup(self._tmp.cleanup)
        self.dir = os.path.realpath(self._tmp.name)

    def write(self, rel, content, binary=False):
        path = os.path.join(self.dir, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        mode = "wb" if binary else "w"
        with open(path, mode) as f:
            f.write(content)
        return path


class TestPatterns(ScanTestCase):
    def test_each_pattern_is_detected_and_never_printed_in_full(self):
        for index, (name, secret, first4) in enumerate(SAMPLES):
            with self.subTest(name=name, first4=first4, index=index):
                path = self.write("case%d/file.txt" % index, "line one\nkey = %s\nend\n" % secret)
                proc = run_scan(os.path.dirname(path))
                self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
                self.assertIn("%s:2: %s %s…" % (path, name, first4), proc.stdout)
                combined = proc.stdout + proc.stderr
                self.assertNotIn(secret, combined)
                # Nothing beyond the first four characters of the secret leaks.
                self.assertNotIn(secret[:8], combined.replace(first4 + "…", ""))
                self.assertNotIn("no secrets found", proc.stdout)

    def test_near_misses_are_not_flagged(self):
        for index, text in enumerate(NEAR_MISSES):
            with self.subTest(index=index):
                path = self.write("miss%d/file.txt" % index, "value: %s\n" % text)
                proc = run_scan(os.path.dirname(path))
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_line_numbers_and_multiple_hits(self):
        ghp = SAMPLES[0][1]
        akia = SAMPLES[5][1]
        path = self.write("multi.txt", "a\n%s\nb\nx %s y\n" % (ghp, akia))
        proc = run_scan(path)
        self.assertEqual(proc.returncode, 1)
        lines = proc.stdout.strip().splitlines()
        self.assertEqual(
            lines,
            [
                "%s:2: github-classic-token ghp_…" % path,
                "%s:4: aws-access-key-id AKIA…" % path,
            ],
        )
        self.assertNotIn(ghp, proc.stdout + proc.stderr)
        self.assertNotIn(akia, proc.stdout + proc.stderr)

    def test_crlf_files_report_correct_line(self):
        secret = SAMPLES[0][1]
        path = self.write("crlf.txt", "a\r\nb\r\n%s\r\n" % secret, binary=False)
        proc = run_scan(path)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("%s:3: github-classic-token" % path, proc.stdout)


class TestCleanAndSkips(ScanTestCase):
    def test_clean_directory_passes(self):
        self.write("a/index.html", "<title>Hello</title>\n")
        self.write("a/b/notes.txt", "nothing to see\n")
        proc = run_scan(self.dir)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "no secrets found")

    def test_empty_directory_passes(self):
        proc = run_scan(self.dir)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "no secrets found")

    def test_binary_file_with_nul_in_first_8k_is_skipped(self):
        secret = SAMPLES[0][1]
        self.write("img.bin", b"\x00\x01" + secret.encode("ascii") + b"\n", binary=True)
        proc = run_scan(self.dir)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertEqual(proc.stdout.strip(), "no secrets found")
        self.assertIn("binary", proc.stderr)

    def test_nul_after_first_8k_is_still_scanned(self):
        secret = SAMPLES[0][1]
        data = b"a" * 9000 + b"\x00" + secret.encode("ascii") + b"\n"
        self.write("late-nul.txt", data, binary=True)
        proc = run_scan(self.dir)
        self.assertEqual(proc.returncode, 1)

    def test_file_over_5_mib_is_skipped_and_reported(self):
        secret = SAMPLES[0][1]
        chunk = b"a" * 1023 + b"\n"
        with open(os.path.join(self.dir, "big.txt"), "wb") as f:
            f.write(secret.encode("ascii") + b"\n")
            for _ in range(5 * 1024 + 1):
                f.write(chunk)
        proc = run_scan(self.dir)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertEqual(proc.stdout.strip(), "no secrets found")
        self.assertIn("5 MiB", proc.stderr)

    def test_file_of_exactly_5_mib_is_scanned(self):
        secret = SAMPLES[0][1]
        total = 5 * 1024 * 1024
        head = secret.encode("ascii") + b"\n"
        with open(os.path.join(self.dir, "edge.txt"), "wb") as f:
            f.write(head)
            f.write(b"a" * (total - len(head)))
        self.assertEqual(os.path.getsize(os.path.join(self.dir, "edge.txt")), total)
        proc = run_scan(self.dir)
        self.assertEqual(proc.returncode, 1)

    def test_symlink_is_not_followed_and_is_reported(self):
        outside = tempfile.TemporaryDirectory(prefix="outside ")
        self.addCleanup(outside.cleanup)
        secret_file = os.path.join(outside.name, "s.txt")
        with open(secret_file, "w") as f:
            f.write(SAMPLES[0][1] + "\n")
        os.symlink(secret_file, os.path.join(self.dir, "link.txt"))
        proc = run_scan(self.dir)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("symlink", proc.stderr)

    def test_multiple_paths_and_single_file_argument(self):
        clean = self.write("clean/a.txt", "ok\n")
        dirty = self.write("dirty/b.txt", "x = %s\n" % SAMPLES[7][1])
        proc = run_scan(clean, dirty)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("%s:1: openai-style-key sk-a…" % dirty, proc.stdout)

    def test_missing_path_is_an_error_not_a_pass(self):
        proc = run_scan(os.path.join(self.dir, "does-not-exist"))
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn("no secrets found", proc.stdout)
        self.assertIn("does-not-exist", proc.stderr)

    def test_no_arguments_is_a_usage_error(self):
        proc = run_scan()
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("no secrets found", proc.stdout)


if __name__ == "__main__":
    unittest.main()
