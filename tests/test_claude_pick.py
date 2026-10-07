"""Tests for bin/claude-pick: the logic, never rofi or a terminal."""
import importlib.machinery
import importlib.util
import json
import os
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
    def run_launch(self, folder, action, session=None):
        with mock.patch.object(cp.subprocess, "Popen") as popen:
            cp.launch(str(folder), action, session)
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


if __name__ == "__main__":
    unittest.main()
