"""Tests for bin/claude-new: the names of the sessions and which one it continues.
It runs the real script in a throwaway HOME with a fake `claude` that only records
its arguments."""
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

CLAUDE_NEW = Path(__file__).resolve().parent.parent / "bin" / "claude-new"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        (self.home / "Zeke_projects" / "foo").mkdir(parents=True)
        (self.home / "work" / "foo").mkdir(parents=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "claude.log"
        self.fresh_log = self.tmp / "fresh.log"
        self.stub("claude", f'printf "%s\\n" "$*" >> "{self.log}"')       # not echo: it eats -n
        tools = self.tmp / "tools"
        tools.mkdir()
        for tool in ("bash", "sh", "env", "sed", "grep", "cut", "tail", "head", "basename",
                     "date", "stat", "cat"):
            found = shutil.which(tool)
            if found:
                (tools / tool).symlink_to(found)
        self.env = {"HOME": str(self.home), "PATH": f"{self.bin}:{tools}",
                    "XDG_CONFIG_HOME": str(self.home / ".config"),
                    "CLAUDE_PROJECTS_DIR": str(self.home / "Zeke_projects")}
        self.machine("laptop")

    def stub(self, name, body):
        f = self.bin / name
        f.write_text(f"#!/bin/sh\n{body}\n")
        f.chmod(0o755)

    def machine(self, name):
        d = self.home / ".config" / "claude-new"
        d.mkdir(parents=True, exist_ok=True)
        (d / "machine").write_text(name + "\n")

    def session(self, folder, sid, title, age=0):
        """A saved session of `folder`, with the name Claude stores for it."""
        slug = "".join(c if c.isalnum() else "-" for c in str(folder))
        d = self.home / ".claude" / "projects" / slug
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{sid}.jsonl"
        f.write_text('{"type":"custom-title","customTitle":"%s","sessionId":"%s"}\n' % (title, sid))
        t = time.time() - age
        os.utime(f, (t, t))

    def run_new(self, *args, cwd=None, stdin=""):
        self.log.unlink(missing_ok=True)
        r = subprocess.run([str(CLAUDE_NEW), *args], cwd=cwd or self.home / "Zeke_projects" / "foo",
                           env=self.env, input=stdin, capture_output=True, text=True)
        called = self.log.read_text().strip() if self.log.exists() else ""
        return r, called


class TestNames(Base):
    def test_a_folder_without_sessions_starts_at_001_with_its_path_and_the_machine(self):
        _, called = self.run_new("--new")
        self.assertEqual(called, "-n Zeke_projects-foo_001@laptop")

    def test_in_home_the_folder_part_is_home(self):
        _, called = self.run_new("--new", cwd=self.home)
        self.assertEqual(called, "-n home_001@laptop")

    def test_two_folders_with_the_same_last_name_stay_apart(self):
        _, a = self.run_new("--new", cwd=self.home / "Zeke_projects" / "foo")
        _, b = self.run_new("--new", cwd=self.home / "work" / "foo")
        self.assertEqual((a, b), ("-n Zeke_projects-foo_001@laptop", "-n work-foo_001@laptop"))

    def test_a_folder_given_by_name_is_looked_up_in_the_projects_dir(self):
        _, called = self.run_new("--new", "foo", cwd=self.home)
        self.assertEqual(called, "-n Zeke_projects-foo_001@laptop")

    def test_the_rest_of_the_arguments_go_to_claude(self):
        _, called = self.run_new("--new", "foo", "-m", "x", cwd=self.home)
        self.assertEqual(called, "-n Zeke_projects-foo_001@laptop -m x")

    def test_unusual_characters_become_dashes(self):
        odd = self.home / "my app (old)"
        odd.mkdir()
        _, called = self.run_new("--new", cwd=odd)
        self.assertEqual(called, "-n my-app--old-_001@laptop")

    def test_outside_home_the_absolute_path_is_used(self):
        outside = self.tmp / "srv" / "site"
        outside.mkdir(parents=True)
        slug = str(outside).lstrip("/").replace("/", "-")
        _, called = self.run_new("--new", cwd=outside)
        self.assertEqual(called, f"-n {slug}_001@laptop")

    def test_the_machine_defaults_to_the_start_of_the_machine_id(self):
        (self.home / ".config" / "claude-new" / "machine").unlink()
        mid = Path("/etc/machine-id")
        if not mid.exists():
            self.skipTest("no /etc/machine-id")
        _, called = self.run_new("--new")
        self.assertTrue(called.endswith("@" + mid.read_text()[:4]), called)


class TestNumbersAndLast(Base):
    foo = property(lambda self: self.home / "Zeke_projects" / "foo")

    def test_numbers_continue_across_machines(self):
        self.session(self.foo, "mine", "Zeke_projects-foo_001@laptop", age=500)
        self.session(self.foo, "theirs", "Zeke_projects-foo_002@arch", age=100)
        _, called = self.run_new("--new")
        self.assertEqual(called, "-n Zeke_projects-foo_003@laptop")

    def test_last_is_the_most_recent_session_whatever_the_machine(self):
        self.session(self.foo, "mine", "Zeke_projects-foo_001@laptop", age=10)
        self.session(self.foo, "theirs", "Zeke_projects-foo_002@arch", age=900)
        _, called = self.run_new("--last")
        self.assertEqual(called, "-r mine")                    # newer, although numbered lower

    def test_last_without_sessions_starts_a_new_one(self):
        _, called = self.run_new("--last")
        self.assertEqual(called, "-n Zeke_projects-foo_001@laptop")

    def test_old_names_of_this_machine_still_count(self):
        self.session(self.foo, "old", "laptop_foo_003", age=50)
        _, called = self.run_new("--new")
        self.assertEqual(called, "-n Zeke_projects-foo_004@laptop")
        _, called = self.run_new("--last")
        self.assertEqual(called, "-r old")

    def test_old_home_names_count_too(self):
        self.session(self.home, "h", "laptop_home_002")
        _, called = self.run_new("--new", cwd=self.home)
        self.assertEqual(called, "-n home_003@laptop")

    def test_old_names_of_another_machine_and_other_names_are_ignored(self):
        self.session(self.foo, "a", "arch_foo_009")
        self.session(self.foo, "b", "something else entirely")
        self.session(self.foo, "c", "laptop_foo_12")           # not three digits
        _, called = self.run_new("--new")
        self.assertEqual(called, "-n Zeke_projects-foo_001@laptop")

    def test_a_name_without_a_machine_counts(self):
        self.session(self.foo, "plain", "Zeke_projects-foo_005")
        _, called = self.run_new("--new")
        self.assertEqual(called, "-n Zeke_projects-foo_006@laptop")

    def test_sessions_of_other_folders_do_not_count(self):
        self.session(self.home / "work" / "foo", "w", "work-foo_007@laptop")
        _, called = self.run_new("--new")
        self.assertEqual(called, "-n Zeke_projects-foo_001@laptop")


class TestQuestionAndFreshness(Base):
    foo = property(lambda self: self.home / "Zeke_projects" / "foo")

    def test_enter_continues_the_last_session(self):
        self.session(self.foo, "s1", "Zeke_projects-foo_001@arch")
        r, called = self.run_new(stdin="\n")
        self.assertEqual(called, "-r s1")
        self.assertIn("Last session here: Zeke_projects-foo_001@arch", r.stdout)

    def test_n_starts_the_next_number(self):
        self.session(self.foo, "s1", "Zeke_projects-foo_001@arch")
        _, called = self.run_new(stdin="n\n")
        self.assertEqual(called, "-n Zeke_projects-foo_002@laptop")

    def test_claude_fresh_runs_before_claude_with_the_folder(self):
        self.stub("claude-fresh", f'printf "%s\\n" "$1" >> "{self.fresh_log}"')
        self.run_new("--new")
        self.assertEqual(self.fresh_log.read_text().strip(), str(self.foo))

    def test_without_claude_fresh_it_still_starts(self):
        _, called = self.run_new("--new")
        self.assertEqual(called, "-n Zeke_projects-foo_001@laptop")

    def test_an_unknown_folder_is_an_error(self):
        r, called = self.run_new("--new", "nowhere", cwd=self.home)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no folder 'nowhere'", r.stderr)
        self.assertEqual(called, "")


if __name__ == "__main__":
    unittest.main()
