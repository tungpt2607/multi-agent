"""Test rules_sync.py trên thư mục tạm (không đụng repo thật).

Chạy: python3 -m unittest test_rules_sync -v  (trong thư mục scripts/)
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rules_sync  # noqa: E402

MANIFEST = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "rules_manifest.json")
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

WORKFLOW = "stages:\n  - name: execution\n    mode: %s\n    x: 1\n"
CLAUDE = "# Riêng repo\n<!-- rules-sync:begin minh-bach-agent -->\n%s\n<!-- rules-sync:end minh-bach-agent -->\nĐuôi riêng\n"


def write(root, rel, text):
    p = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def read(root, rel):
    with open(os.path.join(root, *rel.split("/")), encoding="utf-8", newline="") as f:
        return f.read()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # git cô lập: không kế thừa cấu hình/hook toàn cục
        env = {"HOME": self.tmp.name, "GIT_CONFIG_GLOBAL": os.devnull,
               "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t",
               "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
               "GIT_COMMITTER_EMAIL": "t@t"}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("GIT_DIR", None)
        self.src = os.path.join(self.tmp.name, "src")
        self.dst = os.path.join(self.tmp.name, "dst")
        for root, mode in ((self.src, "draft"), (self.dst, "branch")):
            write(root, ".agents/workflow.yaml", WORKFLOW % mode)
            write(root, ".agents/schemas/a.json", "{\"a\": 1}\n")
            write(root, "CLAUDE.md", CLAUDE % "luật chung")

    def git(self, root, *args):
        return subprocess.run(["git", "-C", root] + list(args), check=True,
                              capture_output=True, text=True).stdout.strip()

    def make_repo(self, root, branch):
        self.git(root, "init", "-q")
        self.git(root, "symbolic-ref", "HEAD", "refs/heads/" + branch)
        self.snapshot(root)

    def snapshot(self, root):
        """Tạo commit bằng lệnh nền (không dùng lệnh ghi cấp cao)."""
        files = self.git(root, "ls-files", "-o", "-c").splitlines()
        for f in files:
            self.git(root, "update-index", "--add", f)
        tree = self.git(root, "write-tree")
        sha = self.git(root, "commit-tree", tree, "-m", "t")
        self.git(root, "update-ref", "HEAD", sha)
        return sha

    def run_cli(self, cmd):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = rules_sync.main([cmd, "--source", self.src, "--target",
                                    self.dst, "--manifest", MANIFEST])
        return code, out.getvalue(), err.getvalue()


class CheckTests(Base):
    def test_match_ignores_mode(self):
        code, out, _ = self.run_cli("check")
        self.assertEqual(code, 0)
        self.assertIn("OK: 3/3", out)

    def test_drift_schema(self):
        write(self.dst, ".agents/schemas/a.json", "{\"a\": 2}\n")
        code, out, _ = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn("DRIFT: .agents/schemas/a.json", out)

    def test_drift_other_than_mode(self):
        write(self.dst, ".agents/workflow.yaml", (WORKFLOW % "branch") + "y: 2\n")
        code, out, _ = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn("DRIFT: .agents/workflow.yaml", out)

    def test_drift_marker_block_only(self):
        write(self.dst, "CLAUDE.md", CLAUDE % "bị sửa tay")
        code, out, _ = self.run_cli("check")
        self.assertEqual(code, 1)
        self.assertIn("DRIFT: CLAUDE.md", out)

    def test_outside_marker_is_ignored(self):
        write(self.dst, "CLAUDE.md",
              "KHÁC\n" + (CLAUDE % "luật chung") + "thêm\n")
        self.assertEqual(self.run_cli("check")[0], 0)

    def test_missing_file(self):
        # nguồn có thêm file mà đích chưa có
        write(self.src, ".agents/schemas/b.json", "{\"b\": 1}\n")
        code, out, _ = self.run_cli("check")
        self.assertEqual(code, 2)
        self.assertIn("MISSING: .agents/schemas/b.json", out)

    def test_missing_marker(self):
        write(self.dst, "CLAUDE.md", "không có marker\n")
        code, out, _ = self.run_cli("check")
        self.assertEqual(code, 2)
        self.assertIn("MISSING: CLAUDE.md", out)

    def test_check_does_not_write(self):
        write(self.dst, ".agents/schemas/a.json", "{}\n")
        before = sorted(os.walk(self.dst))
        self.run_cli("check")
        self.assertEqual(before, sorted(os.walk(self.dst)))
        self.assertEqual(read(self.dst, ".agents/schemas/a.json"), "{}\n")


class ApplyTests(Base):
    def setUp(self):
        super().setUp()
        write(self.src, "CLAUDE.md", CLAUDE % "luật mới")
        write(self.src, ".agents/schemas/b.json", "{\"b\": 1}\n")
        self.make_repo(self.src, "main")
        self.sha7 = self.git(self.src, "rev-parse", "HEAD")[:7]

    def test_refused_on_main(self):
        self.make_repo(self.dst, "main")
        before = read(self.dst, "CLAUDE.md")
        code, _, err = self.run_cli("apply")
        self.assertEqual(code, 3)
        self.assertIn("từ chối", err)
        self.assertEqual(read(self.dst, "CLAUDE.md"), before)
        self.assertFalse(os.path.exists(
            os.path.join(self.dst, ".agents", "schemas", "b.json")))

    def test_refused_wrong_branch(self):
        self.make_repo(self.dst, "rules-sync/0000000")
        self.assertEqual(self.run_cli("apply")[0], 3)

    def test_refused_dirty_tree(self):
        self.make_repo(self.dst, "rules-sync/" + self.sha7)
        write(self.dst, "untracked.txt", "x")
        code, _, err = self.run_cli("apply")
        self.assertEqual(code, 3)
        self.assertIn("chưa commit", err)

    def test_apply_ok_keeps_mode_and_outside_marker(self):
        self.make_repo(self.dst, "rules-sync/" + self.sha7)
        code, out, _ = self.run_cli("apply")
        self.assertEqual(code, 0, out)
        self.assertIn("mode: branch", read(self.dst, ".agents/workflow.yaml"))
        claude = read(self.dst, "CLAUDE.md")
        self.assertIn("luật mới", claude)
        self.assertTrue(claude.startswith("# Riêng repo\n"))
        self.assertTrue(claude.endswith("Đuôi riêng\n"))
        self.assertEqual(read(self.dst, ".agents/schemas/b.json"), "{\"b\": 1}\n")
        self.assertEqual(self.run_cli("check")[0], 0)

    def test_apply_stops_without_marker(self):
        write(self.dst, "CLAUDE.md", "không marker\n")
        self.make_repo(self.dst, "rules-sync/" + self.sha7)
        code, _, _ = self.run_cli("apply")
        self.assertEqual(code, 3)
        self.assertEqual(read(self.dst, "CLAUDE.md"), "không marker\n")
        # kiểm hết rồi mới ghi: schema mới cũng chưa được tạo
        self.assertFalse(os.path.exists(
            os.path.join(self.dst, ".agents", "schemas", "b.json")))


class ManifestTests(Base):
    def run_with_manifest(self, text):
        mpath = os.path.join(self.tmp.name, "m.json")
        write(self.tmp.name, "m.json", text)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = rules_sync.main(["check", "--source", self.src, "--target",
                                    self.dst, "--manifest", mpath])
        return code, err.getvalue()

    def test_manifest_not_object(self):
        for text in ("[]", "[1, 2]", "\"x\"", "null", "42"):
            code, err = self.run_with_manifest(text)
            self.assertEqual(code, 3, text)
            self.assertIn("LỖI", err)
            self.assertNotIn("Traceback", err)

    def test_manifest_entry_not_object(self):
        code, err = self.run_with_manifest('{"version": 1, "entries": ["x"]}')
        self.assertEqual(code, 3)
        self.assertIn("LỖI", err)


class GitWhitelistTests(unittest.TestCase):
    def test_allowed(self):
        for args in (("rev-parse", "HEAD"), ("branch", "--show-current"),
                     ("status", "--porcelain")):
            self.assertTrue(rules_sync.git_allowed(args), args)

    def test_rejected(self):
        for args in (("branch", "newname"), ("branch",), ("branch", "-D", "x"),
                     ("branch", "--show-current", "x"), ("status",),
                     ("status", "--porcelain", "-z"), ("rev-parse",),
                     ("rev-parse", "--git-dir"), ("commit", "-m", "x"),
                     ("push",), ()):
            self.assertFalse(rules_sync.git_allowed(args), args)

    def test_git_read_refuses_branch_create(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(rules_sync.SyncError):
                rules_sync.git_read(d, "branch", "newname")


class PathGuardTests(unittest.TestCase):
    def test_blocked_paths(self):
        for bad in ("../x", "/etc/passwd", ".git/config", ".claude/a",
                    ".github/w.yml", ".agents/knowledge/r.md",
                    ".agents/state/a.json", "a/../b"):
            with self.assertRaises(rules_sync.SyncError, msg=bad):
                rules_sync.split_rel(bad)

    def test_symlink_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "real"))
            os.symlink(os.path.join(d, "real"), os.path.join(d, "link"))
            with self.assertRaises(rules_sync.SyncError):
                rules_sync.full_path(d, ["link", "f.json"])


if __name__ == "__main__":
    unittest.main()
