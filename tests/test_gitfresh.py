"""Tests for lib/claude-tools/gitfresh.py with real git repositories:
a bare remote and two clones play the two computers."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib/claude-tools"))
import gitfresh  # noqa: E402

ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
           GIT_COMMITTER_EMAIL="t@t", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")


def run(cwd, *args):
    subprocess.run(args, cwd=cwd, env=ENV, check=True, capture_output=True)


class Machines(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.remote = self.tmp / "remote.git"
        run(self.tmp, "git", "init", "--bare", "-b", "main", str(self.remote))
        self.a, self.b = self.tmp / "laptop", self.tmp / "desktop"
        run(self.tmp, "git", "clone", str(self.remote), str(self.a))
        self.commit(self.a, "one.txt")
        run(self.a, "git", "push", "-u", "origin", "main")
        run(self.tmp, "git", "clone", str(self.remote), str(self.b))
        for k, v in ENV.items():                      # git in the code under test uses os.environ
            p = mock.patch.dict(os.environ, {k: v})
            p.start()
            self.addCleanup(p.stop)

    def commit(self, repo, name, push=False):
        (repo / name).write_text(name)
        run(repo, "git", "add", name)
        run(repo, "git", "commit", "-m", name)
        if push:
            run(repo, "git", "push")


class TestState(Machines):
    def test_clean_repo(self):
        st = gitfresh.state(self.a)
        self.assertEqual((st["ahead"], st["behind"], st["dirty"], st["upstream"]), (0, 0, 0, "origin/main"))
        self.assertEqual(gitfresh.badges(st), "")

    def test_ahead_and_dirty_badges(self):
        self.commit(self.a, "two.txt")
        (self.a / "scratch").write_text("x")
        st = gitfresh.state(self.a)
        self.assertEqual((st["ahead"], st["dirty"]), (1, 1))
        self.assertEqual(gitfresh.badges(st), "↑1 ✎")

    def test_not_a_repo_and_missing_folder(self):
        self.assertIsNone(gitfresh.state(self.tmp))
        self.assertIsNone(gitfresh.state(self.tmp / "nope"))
        self.assertEqual(gitfresh.badges(None), "")

    def test_home_means_the_home_project(self):
        shutil.copytree(self.a, self.tmp / "dotfiles")
        with mock.patch.object(gitfresh, "HOME", self.tmp), \
                mock.patch.object(gitfresh, "HOME_PROJECT", self.tmp / "dotfiles"), \
                mock.patch.object(gitfresh, "EXTRA", []):
            self.assertEqual(gitfresh.state(self.tmp)["repo"], str(self.tmp / "dotfiles"))

    def test_branch_name_with_a_dot(self):
        run(self.a, "git", "checkout", "-b", "release-1.0")
        self.assertEqual(gitfresh.state(self.a)["branch"], "release-1.0")


class TestFreshen(Machines):
    def test_behind_and_clean_is_fast_forwarded(self):
        self.commit(self.a, "two.txt", push=True)
        status, msg = gitfresh.freshen(self.b)
        self.assertEqual(status, "updated")
        self.assertIn("1 commit", msg)
        self.assertTrue((self.b / "two.txt").exists())

    def test_opening_home_updates_the_home_project_and_the_extra_repo(self):
        run(self.tmp, "git", "clone", str(self.remote), str(self.tmp / "dotfiles"))
        run(self.tmp, "git", "clone", str(self.remote), str(self.tmp / "extra"))
        self.commit(self.a, "two.txt", push=True)
        with mock.patch.object(gitfresh, "HOME", self.tmp), \
                mock.patch.object(gitfresh, "HOME_PROJECT", self.tmp / "dotfiles"), \
                mock.patch.object(gitfresh, "EXTRA", [self.tmp / "extra"]):
            status, msg = gitfresh.freshen(self.tmp)
        self.assertEqual(status, "updated")
        self.assertEqual(len(msg.splitlines()), 2)
        self.assertTrue((self.tmp / "dotfiles" / "two.txt").exists())
        self.assertTrue((self.tmp / "extra" / "two.txt").exists())

    def test_one_repo_that_needs_attention_wins(self):
        run(self.tmp, "git", "clone", str(self.remote), str(self.tmp / "dotfiles"))
        run(self.tmp, "git", "clone", str(self.remote), str(self.tmp / "extra"))
        self.commit(self.a, "two.txt", push=True)
        (self.tmp / "dotfiles" / "one.txt").write_text("local edit")
        with mock.patch.object(gitfresh, "HOME", self.tmp), \
                mock.patch.object(gitfresh, "HOME_PROJECT", self.tmp / "dotfiles"), \
                mock.patch.object(gitfresh, "EXTRA", [self.tmp / "extra"]):
            status, _ = gitfresh.freshen(self.tmp)
        self.assertEqual(status, "dirty-behind")
        self.assertTrue((self.tmp / "extra" / "two.txt").exists())    # the clean one was still updated

    def test_up_to_date(self):
        self.assertEqual(gitfresh.freshen(self.b)[0], "current")

    def test_behind_but_dirty_is_left_alone(self):
        self.commit(self.a, "two.txt", push=True)
        (self.b / "one.txt").write_text("my local edit")
        status, _ = gitfresh.freshen(self.b)
        self.assertEqual(status, "dirty-behind")
        self.assertFalse((self.b / "two.txt").exists())
        self.assertEqual((self.b / "one.txt").read_text(), "my local edit")

    def test_diverged_is_left_alone(self):
        self.commit(self.a, "two.txt", push=True)
        self.commit(self.b, "three.txt")
        status, _ = gitfresh.freshen(self.b)
        self.assertEqual(status, "diverged")
        self.assertFalse((self.b / "two.txt").exists())

    def test_ahead_only_reports_and_never_pushes(self):
        self.commit(self.b, "three.txt")
        status, msg = gitfresh.freshen(self.b)
        self.assertEqual(status, "ahead")
        self.assertIn("not pushed", msg)
        rc = subprocess.run(["git", "-C", str(self.remote), "log", "--oneline"], capture_output=True, text=True)
        self.assertNotIn("three.txt", rc.stdout)

    def test_uncommitted_only(self):
        (self.b / "scratch").write_text("x")
        self.assertEqual(gitfresh.freshen(self.b)[0], "dirty")

    def test_unreachable_remote_is_skipped(self):
        shutil.rmtree(self.remote)
        status, msg = gitfresh.freshen(self.b)
        self.assertEqual(status, "offline")
        self.assertIn("not updated", msg)

    def test_no_upstream(self):
        run(self.b, "git", "checkout", "-b", "local-only")
        self.assertEqual(gitfresh.freshen(self.b)[0], "noupstream")

    def test_not_a_repo(self):
        self.assertEqual(gitfresh.freshen(self.tmp), ("none", ""))

    def test_attention_statuses_make_the_cli_stop(self):
        self.assertEqual(gitfresh.ATTENTION, {"dirty-behind", "diverged", "failed"})


class TestBadgesInTheMenu(unittest.TestCase):
    def test_folder_rows_add_the_git_badges(self):
        import importlib.machinery
        import importlib.util
        loader = importlib.machinery.SourceFileLoader(
            "claude_pick2", str(Path(__file__).resolve().parent.parent / "bin/claude-pick"))
        cp = importlib.util.module_from_spec(importlib.util.spec_from_loader("claude_pick2", loader))
        loader.exec_module(cp)
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        proj = tmp / "Zeke_projects" / "p"
        proj.mkdir(parents=True)
        with mock.patch.object(cp, "HOME", tmp), mock.patch.object(cp, "CODE", tmp / "Zeke_projects"), \
                mock.patch.object(cp.gitfresh, "state", side_effect=lambda d: (
                    {"ahead": 2, "behind": 0, "dirty": 3} if Path(d) == proj else None)):
            rows = cp.folder_rows({})
        by = {d: plain for d, _, plain in rows}
        self.assertTrue(by[str(proj)].endswith("↑2 ✎"))
        self.assertNotIn("↑", by[str(tmp)])


if __name__ == "__main__":
    unittest.main()
