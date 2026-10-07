"""Tests for bin/claude-pick: the logic, never rofi or a terminal."""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "bin/claude-pick"
loader = importlib.machinery.SourceFileLoader("claude_pick", str(SCRIPT))
spec = importlib.util.spec_from_loader("claude_pick", loader)
cp = importlib.util.module_from_spec(spec)
loader.exec_module(cp)


def reply(ctx, model="claude-opus-5-5"):
    usage = {"input_tokens": 2, "cache_read_input_tokens": ctx - 2, "cache_creation_input_tokens": 0}
    # compact separators, like the files Claude writes
    return json.dumps({"type": "assistant", "message": {"model": model, "usage": usage}},
                      separators=(",", ":"))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home = self.tmp / "home"
        (self.home / "Zeke_projects" / "idle").mkdir(parents=True)
        (self.home / "Zeke_projects" / "busy").mkdir()
        (self.home / ".cache" / "helper").mkdir(parents=True)
        self.store = self.tmp / "store"
        self.store.mkdir()
        for name, val in [("HOME", self.home), ("CODE", self.home / "Zeke_projects"),
                          ("PROJECTS", self.store), ("WINDOW_FILE", self.tmp / "window")]:
            p = mock.patch.object(cp, name, val)
            p.start()
            self.addCleanup(p.stop)
        cp.scan.cache, cp.scan.dirty = {}, False

    def session(self, folder, sid, lines, age=0):
        d = self.store / str(folder).replace("/", "-")
        d.mkdir(exist_ok=True)
        f = d / f"{sid}.jsonl"
        f.write_text("\n".join(lines) + "\n")
        t = time.time() - age
        os.utime(f, (t, t))
        return f


class TestReading(Base):
    def test_last_context_skips_synthetic_and_takes_the_last_real_reply(self):
        f = self.session(self.home, "a", [json.dumps({"cwd": str(self.home)}), reply(1000),
                                          reply(80000), reply(0, "<synthetic>")])
        self.assertEqual(cp.last_context(f, f.stat().st_size), 80000)

    def test_last_context_when_the_reply_is_far_from_the_end(self):
        filler = json.dumps({"type": "user", "x": "y" * 300_000})
        f = self.session(self.home, "b", [reply(42000), filler, filler])
        self.assertEqual(cp.last_context(f, f.stat().st_size), 42000)

    def test_scan_title_prefers_custom_over_ai_and_counts_replies(self):
        f = self.session(self.home, "c", [json.dumps({"cwd": str(self.home)}),
                                          json.dumps({"type": "ai-title", "aiTitle": "AI name"}),
                                          json.dumps({"type": "custom-title", "customTitle": "d36e_home_001"}),
                                          reply(5000), reply(6000)])
        info = cp.scan(f)
        self.assertEqual((info["title"], info["replies"], info["ctx"], info["cwd"]),
                         ("d36e_home_001", 2, 6000, str(self.home)))

    def test_scan_falls_back_to_ai_title_then_id(self):
        f = self.session(self.home, "deadbeef-1", [json.dumps({"type": "ai-title", "aiTitle": "AI name"})])
        self.assertEqual(cp.scan(f)["title"], "AI name")
        g = self.session(self.home, "cafebabe-2", [json.dumps({"cwd": "/x"})])
        self.assertEqual(cp.scan(g)["title"], "cafebabe")

    def test_scan_uses_the_cache_until_the_file_changes(self):
        f = self.session(self.home, "d", [reply(1000)])
        cp.scan(f)
        with mock.patch.object(cp, "last_context", side_effect=AssertionError("re-read")):
            cp.scan(f)                       # cached: must not read again
        f.write_text(reply(2000) + "\n")
        self.assertEqual(cp.scan(f)["ctx"], 2000)


class TestFolders(Base):
    def test_sessions_by_folder_ignores_hidden_missing_and_outside_folders(self):
        busy = self.home / "Zeke_projects" / "busy"
        self.session(busy, "s1", [json.dumps({"cwd": str(busy)}), reply(1000)])
        self.session("hid", "s2", [json.dumps({"cwd": str(self.home / ".cache" / "helper")}), reply(1)])
        self.session("gone", "s3", [json.dumps({"cwd": str(self.home / "deleted")}), reply(1)])
        self.session("out", "s4", [json.dumps({"cwd": "/tmp"}), reply(1)])
        self.assertEqual(list(cp.sessions_by_folder()), [str(busy)])

    def test_newest_session_first(self):
        busy = self.home / "Zeke_projects" / "busy"
        self.session(busy, "old", [json.dumps({"cwd": str(busy)}), reply(1000)], age=5000)
        self.session(busy, "new", [json.dumps({"cwd": str(busy)}), reply(2000)], age=10)
        self.assertEqual([s["id"] for s in cp.sessions_by_folder()[str(busy)]], ["new", "old"])

    def test_folder_rows_order_and_refresh_marker(self):
        busy = self.home / "Zeke_projects" / "busy"
        self.session(busy, "s1", [json.dumps({"cwd": str(busy)}), reply(120_000)])
        rows = cp.folder_rows()
        self.assertEqual(rows[0][0], str(self.home))                 # home is the default
        self.assertEqual(rows[1][0], str(busy))                      # worked folders next
        self.assertIn("refresh?", rows[1][2])                        # 60% of 200k
        self.assertEqual([r[0] for r in rows[2:-1]], [str(self.home / "Zeke_projects" / "idle")])
        self.assertIn("no sessions yet", rows[2][2])
        self.assertIsNone(rows[-1][0])                               # "Other folder…"


class TestSessionMenu(Base):
    def folder(self, ctx_last, ctx_old=1000):
        busy = self.home / "Zeke_projects" / "busy"
        self.session(busy, "old", [json.dumps({"cwd": str(busy)}), reply(ctx_old)], age=5000)
        self.session(busy, "new", [json.dumps({"cwd": str(busy)}), reply(ctx_last)], age=10)
        return cp.sessions_by_folder()[str(busy)]

    def test_healthy_context_continues_first(self):
        rows = cp.session_rows(self.folder(40_000))
        self.assertEqual([r[0] for r in rows], ["resume", "new", "handoff", "resume"])
        self.assertEqual(rows[0][1]["id"], "new")

    def test_full_context_suggests_a_handoff_first(self):
        rows = cp.session_rows(self.folder(150_000))
        self.assertEqual([r[0] for r in rows], ["handoff", "new", "resume", "resume"])
        self.assertIn("recommended", rows[0][3] + rows[0][2])


class TestFormat(Base):
    def test_percent_and_window(self):
        self.assertEqual(cp.percent(90_000), 45)
        self.assertEqual(cp.percent(250_000), 25)                    # above 200k: 1M window
        (self.tmp / "window").write_text("100000\n")
        self.assertEqual(cp.percent(50_000), 50)

    def test_colour_thresholds(self):
        self.assertEqual([cp.colour(p) for p in (10, 35, 49, 50, 90)],
                         [cp.GREEN, cp.YELLOW, cp.YELLOW, cp.RED, cp.RED])

    def test_ago_and_size(self):
        now = 1_000_000
        self.assertEqual(cp.ago(now - 30, now), "1 min ago")
        self.assertEqual(cp.ago(now - 7200, now), "2 h ago")
        self.assertEqual(cp.ago(now - 90000, now), "yesterday")
        self.assertEqual(cp.ago(now - 3 * 86400, now), "3 d ago")
        self.assertEqual((cp.mib(2048), cp.mib(5 * 1048576)), ("2 KB", "5 MB"))


class TestLaunch(Base):
    def run_launch(self, folder, action, session=None, terminal="alacritty"):
        with mock.patch.object(cp, "choose_terminal", return_value=terminal), \
                mock.patch.object(cp.subprocess, "Popen") as popen:
            cp.launch(str(folder), action, session)
        self.popen = popen
        return popen.call_args[0][0]

    def test_new_session_goes_through_claude_new(self):
        cmd = self.run_launch(self.home / "Zeke_projects" / "busy", "new")
        self.assertEqual(cmd[0], "alacritty")
        self.assertEqual(cmd[cmd.index("-e") + 1:], ["claude-new", "--new", str(self.home / "Zeke_projects" / "busy")])
        self.assertEqual(cmd[cmd.index("--working-directory") + 1], str(self.home / "Zeke_projects" / "busy"))

    def test_handoff_passes_the_message(self):
        cmd = self.run_launch(self.home, "handoff")
        self.assertEqual(cmd[-1], cp.HANDOFF)
        self.assertIn("Claude · home", cmd)

    def test_resume_uses_the_session_id(self):
        cmd = self.run_launch(self.home, "resume", {"id": "abc-123"})
        inner = cmd[cmd.index("-e") + 1:]
        self.assertEqual(inner[:2], ["bash", "-c"])
        self.assertIn("claude-fresh", inner[2])                      # updates the project first
        self.assertEqual(inner[-2:], [str(self.home), "abc-123"])    # then claude -r <id>
        self.assertIn("claude -r", inner[2])


def scripted(*answers):
    """An `ask` function that gives the answers in order and records the prompts."""
    it = iter(answers)
    seen = []

    def ask(prompt):
        seen.append(prompt)
        try:
            return next(it)
        except StopIteration:
            raise EOFError
    ask.seen = seen
    return ask


class TestConsoleMenu(unittest.TestCase):
    ROWS = ["~  System (home)   27%  21 replies", "~/p/alpha  36%  60 replies",
            "~/p/beta  90%  1224 replies  refresh?", "Other folder…"]

    def pick(self, *answers):
        lines = []
        return cp.console_menu("T", self.ROWS, scripted(*answers), lines.append), lines

    def test_a_number_picks_that_row(self):
        self.assertEqual(self.pick("3")[0], 2)

    def test_enter_picks_the_first_row(self):
        self.assertEqual(self.pick("")[0], 0)

    def test_text_filters_and_the_numbers_follow_the_filter(self):
        index, lines = self.pick("beta", "1")
        self.assertEqual(index, 2)
        self.assertTrue(any(l.strip().startswith("1  ~/p/beta") for l in lines))

    def test_enter_after_a_filter_picks_the_first_row_shown(self):
        self.assertEqual(self.pick("alpha", "")[0], 1)

    def test_a_slash_shows_everything_again(self):
        self.assertEqual(self.pick("beta", "/", "4")[0], 3)

    def test_quit_and_end_of_input_return_none(self):
        self.assertIsNone(self.pick("q")[0])
        self.assertIsNone(self.pick()[0])

    def test_a_bad_number_or_an_unmatched_text_asks_again(self):
        index, lines = self.pick("9", "zzz", "2")
        self.assertEqual(index, 1)
        self.assertTrue(any("no row 9" in l for l in lines))
        self.assertTrue(any("nothing matches 'zzz'" in l for l in lines))

    def test_the_percentage_is_coloured_like_the_dot_of_rofi(self):
        self.assertEqual(cp.colour_line("x  27%  y", False), "x  27%  y")
        self.assertIn("\033[32m27%", cp.colour_line("x  27%  y", True))
        self.assertIn("\033[33m40%", cp.colour_line("x  40%  y", True))
        self.assertIn("\033[31m90%", cp.colour_line("x  90%  y", True))
        self.assertEqual(cp.colour_line("no sessions yet", True), "no sessions yet")


class TestConsoleFlow(Base):
    busy = property(lambda self: self.home / "Zeke_projects" / "busy")

    def setUp(self):
        super().setUp()
        self.executed = []
        self.chdirs = []
        for name, fake in (("chdir", self.chdirs.append),):
            p = mock.patch.object(cp.os, name, fake)
            p.start()
            self.addCleanup(p.stop)

    def run_flow(self, found, *answers):
        cp.run_console(found, ask=scripted(*answers), out=lambda *_: None,
                       execute=lambda prog, argv: self.executed.append((prog, argv)))

    def worked(self, ctx=40_000):
        self.session(self.busy, "s1", [json.dumps({"cwd": str(self.busy)}), reply(ctx)], age=10)
        return cp.sessions_by_folder()

    def test_a_folder_with_sessions_asks_for_the_session_and_resumes_it_in_this_terminal(self):
        found = self.worked()
        self.run_flow(found, "busy", "", "")        # filter, first folder, first session (continue)
        prog, argv = self.executed[0]
        self.assertEqual(self.chdirs, [str(self.busy)])
        self.assertEqual(prog, "bash")
        self.assertEqual(argv[-2:], [str(self.busy), "s1"])
        self.assertIn("claude -r", argv[2])

    def test_a_full_context_offers_the_handoff_first(self):
        found = self.worked(ctx=150_000)
        self.run_flow(found, "busy", "", "")
        prog, argv = self.executed[0]
        self.assertEqual(prog, "claude-new")
        self.assertEqual(argv[:3], ["claude-new", "--new", str(self.busy)])
        self.assertEqual(argv[-1], cp.HANDOFF)

    def test_a_folder_without_sessions_starts_a_new_one_without_a_second_menu(self):
        lines = []
        cp.run_console({}, ask=scripted("idle", ""), out=lines.append,      # filter, then Enter
                       execute=lambda prog, argv: self.executed.append((prog, argv)))
        self.assertEqual(self.executed[0][1][:2], ["claude-new", "--new"])
        self.assertFalse(any("pick a session" in l for l in lines))

    def test_quitting_the_first_menu_starts_nothing(self):
        self.run_flow({}, "q")
        self.assertEqual(self.executed, [])
        self.assertEqual(self.chdirs, [])

    def test_other_folder_asks_for_a_path(self):
        other = self.home / "work" / "foo"
        other.mkdir(parents=True)
        self.run_flow({}, "Other", "", str(other))        # filter, Enter, then the path
        self.assertEqual(self.executed[0][1][:3], ["claude-new", "--new", str(other)])

    def test_the_folder_prompt_retries_and_can_go_back(self):
        out = []
        ask = scripted("/no/such/place", str(self.busy))
        self.assertEqual(cp.browse_console(ask, out.append), str(self.busy))
        self.assertTrue(any("not a folder" in l for l in out))
        self.assertIsNone(cp.browse_console(scripted("q"), lambda *_: None))
        with mock.patch.dict(os.environ, {"HOME": str(self.home)}):
            self.assertEqual(cp.browse_console(scripted(""), lambda *_: None), str(self.home))

    def test_input_that_is_not_a_terminal_is_refused_with_a_clear_message(self):
        with mock.patch.object(cp.sys, "stdin") as stdin:
            stdin.isatty.return_value = False
            with self.assertRaises(SystemExit) as ctx:
                cp.run_console({})
        self.assertIn("needs a terminal", str(ctx.exception))


class TestConsoleIsTheBackup(Base):
    def main_with(self, argv, display, rofi_installed):
        env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
        if display:
            env["DISPLAY"] = ":0"
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(cp.sys, "argv", ["claude-pick", *argv]), \
                mock.patch.object(cp.shutil, "which", return_value="/usr/bin/rofi" if rofi_installed else None), \
                mock.patch.object(cp, "run_console") as console, \
                mock.patch.object(cp, "rofi", return_value=None) as rofi:
            cp.main()
        return console.called, rofi.called

    def test_without_a_display_it_falls_back_to_the_console(self):
        self.assertEqual(self.main_with([], display=False, rofi_installed=True), (True, False))

    def test_without_rofi_it_falls_back_to_the_console(self):
        self.assertEqual(self.main_with([], display=True, rofi_installed=False), (True, False))

    def test_with_a_display_and_rofi_it_uses_rofi(self):
        self.assertEqual(self.main_with([], display=True, rofi_installed=True), (False, True))

    def test_the_flag_forces_the_console_even_with_a_display(self):
        self.assertEqual(self.main_with(["--console"], display=True, rofi_installed=True), (True, False))

    def test_claudio_is_claude_pick_in_console_mode(self):
        import subprocess as sp
        env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
        env["HOME"] = str(self.home)
        res = sp.run([str(SCRIPT.parent / "claudio")], env=env, stdin=sp.DEVNULL, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("claudio: it needs a terminal", res.stderr)


class TestPath(unittest.TestCase):
    def test_local_bin_is_on_the_path_even_when_started_with_a_minimal_one(self):
        import subprocess as sp
        home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        code = ("import importlib.machinery as m, importlib.util as u, os;"
                f"l=m.SourceFileLoader('c', {str(SCRIPT)!r}); s=u.spec_from_loader('c', l);"
                "mod=u.module_from_spec(s); l.exec_module(mod); print(os.environ['PATH'])")
        out = sp.run([sys.executable, "-I", "-c", code], env={"HOME": home, "PATH": "/usr/bin"},
                     capture_output=True, text=True).stdout.strip()
        self.assertEqual(out.split(":")[0], f"{home}/.local/bin")
        self.assertIn("/usr/bin", out.split(":"))


class TestTerminals(Base):
    CMD = ["claude-new", "--new", "/work/foo"]

    def argv(self, terminal):
        return cp.terminal_argv(terminal, "/work/foo", "Claude · foo", self.CMD)

    def test_every_known_terminal_ends_with_the_command_and_knows_the_folder(self):
        for name in cp.TERMINALS:
            argv = self.argv(name)
            self.assertEqual(argv[0], name)
            self.assertEqual(argv[-3:], self.CMD, name)
            self.assertTrue(any("/work/foo" in a for a in argv[:-3]) or name == "xterm", name)

    def test_the_options_of_each_terminal(self):
        self.assertIn("--working-directory", self.argv("alacritty"))
        self.assertEqual(self.argv("wezterm")[1:3], ["start", "--class"])
        self.assertIn("--cwd", self.argv("wezterm"))
        self.assertIn("--directory", self.argv("kitty"))
        self.assertIn("--working-directory=/work/foo", self.argv("terminator"))
        self.assertIn("-x", self.argv("terminator"))
        self.assertIn("--title=Claude · foo", self.argv("terminator"))

    def test_an_unknown_terminal_is_given_dash_e(self):
        self.assertEqual(self.argv("mytty"), ["mytty", "-e", *self.CMD])

    def test_the_environment_variable_wins(self):
        with mock.patch.dict(os.environ, {"CLAUDE_TERMINAL": "kitty"}):
            self.assertEqual(cp.choose_terminal(), "kitty")

    def test_the_config_file_is_used_without_the_variable(self):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_TERMINAL"}
        (self.tmp / "terminal").write_text("foot\n")
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(cp.gitfresh, "CONFIG_DIR", self.tmp):
            self.assertEqual(cp.choose_terminal(), "foot")

    def test_detection_follows_the_order_of_the_table(self):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_TERMINAL"}
        have = {"terminator", "wezterm"}                      # alacritty is missing, as on my Arch
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(cp.gitfresh, "CONFIG_DIR", self.tmp / "none"), \
                mock.patch.object(cp.shutil, "which", side_effect=lambda t: f"/usr/bin/{t}" if t in have else None):
            self.assertEqual(cp.choose_terminal(), "wezterm")  # before terminator in the table

    def test_no_terminal_at_all(self):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_TERMINAL"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(cp.gitfresh, "CONFIG_DIR", self.tmp / "none"), \
                mock.patch.object(cp.shutil, "which", return_value=None):
            self.assertIsNone(cp.choose_terminal())

    def test_launch_without_a_terminal_says_what_to_set(self):
        with mock.patch.object(cp, "choose_terminal", return_value=None), \
                mock.patch.object(cp.subprocess, "Popen") as popen:
            with self.assertRaises(SystemExit) as ctx:
                cp.launch(str(self.home), "new")
        self.assertIn("CLAUDE_TERMINAL", str(ctx.exception))
        popen.assert_not_called()

    def test_launch_starts_the_terminal_inside_the_folder(self):
        with mock.patch.object(cp, "choose_terminal", return_value="wezterm"), \
                mock.patch.object(cp.subprocess, "Popen") as popen:
            cp.launch(str(self.home / "Zeke_projects" / "busy"), "new")
        self.assertEqual(popen.call_args.kwargs["cwd"], str(self.home / "Zeke_projects" / "busy"))
        self.assertEqual(popen.call_args[0][0][:2], ["wezterm", "start"])


if __name__ == "__main__":
    unittest.main()
