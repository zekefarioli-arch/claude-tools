"""Keep a project up to date before Claude works on it (shared by claude-fresh and claude-pick).

Two computers share the same projects through GitHub. If the other one pushed
commits, this one is behind and Claude would work on old code. freshen() runs a
short `git fetch` and, only when it is safe, `git pull --ff-only`. It never
merges, never forces, never pushes by itself and never touches a tree with
uncommitted changes.

In ~ there is no single project, so it works on the "home project" (by default
~/dotfiles, where the configs and the docs live) and on the other repos that travel
between computers and are not inside the projects folder (EXTRA, by default this
tool itself). Both places can be changed with environment variables:
CLAUDE_PROJECTS_DIR (default ~/Zeke_projects) and CLAUDE_HOME_PROJECT (default ~/dotfiles).
"""
import os
import re
import subprocess
from pathlib import Path

HOME = Path.home()
PROJECTS_DIR = Path(os.environ.get("CLAUDE_PROJECTS_DIR", HOME / "Zeke_projects"))
HOME_PROJECT = Path(os.environ.get("CLAUDE_HOME_PROJECT", HOME / "dotfiles"))
EXTRA = [PROJECTS_DIR / "claude-tools"]   # updated too when Claude is opened in ~
FETCH_TIMEOUT = 4   # seconds; without network or access the update is skipped

# Statuses that need me to look at them before going on (claude-fresh exits 2)
ATTENTION = {"dirty-behind", "diverged", "failed"}


def git(path, *args, timeout=5):
    """Run git in path; returns (returncode, stdout). Never prompts for a password."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0",
               GIT_SSH_COMMAND="ssh -o BatchMode=yes -o ConnectTimeout=3")
    try:
        r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True,
                           timeout=timeout, env=env)
        return r.returncode, r.stdout
    except (subprocess.TimeoutExpired, OSError):
        return 124, ""


def targets(folder):
    """The folders whose git state matters: the home project (and EXTRA) for ~, the folder itself otherwise."""
    folder = Path(folder)
    found = [HOME_PROJECT] + EXTRA if folder == HOME else [folder]
    return [p for p in found if p.is_dir()]


def target(folder):
    """The main folder whose state is shown (the first of targets), or None."""
    found = targets(folder)
    return found[0] if found else None


def state(folder):
    """{'repo', 'branch', 'upstream', 'ahead', 'behind', 'dirty'} from what git already knows
    (no network), or None if the folder is not in a git repo."""
    path = target(folder)
    if path is None:
        return None
    rc, out = git(path, "status", "--porcelain=v1", "-b")
    if rc != 0:
        return None
    lines = out.splitlines()
    head = lines[0][3:] if lines else ""
    m = re.match(r"(?P<branch>.+?)(?:\.\.\.(?P<up>\S+?))?(?: \[(?P<track>[^\]]*)\])?$", head)
    ahead = behind = 0
    if m and m.group("track"):
        a = re.search(r"ahead (\d+)", m.group("track"))
        b = re.search(r"behind (\d+)", m.group("track"))
        ahead, behind = int(a.group(1)) if a else 0, int(b.group(1)) if b else 0
    return {"repo": str(path), "branch": m.group("branch") if m else "",
            "upstream": m.group("up") if m else None, "ahead": ahead, "behind": behind,
            "dirty": len(lines) - 1 if lines else 0}


def badges(st):
    """Short marks for a menu: ↑ commits not pushed, ✎ uncommitted files, ↓ commits behind."""
    if not st:
        return ""
    out = []
    if st["ahead"]:
        out.append(f"↑{st['ahead']}")
    if st["dirty"]:
        out.append("✎")
    if st["behind"]:
        out.append(f"↓{st['behind']}")
    return " ".join(out)


def freshen(folder):
    """freshen_one for every target of the folder; the status that needs the most attention wins."""
    results = [freshen_one(t) for t in targets(folder)]
    if not results:
        return "none", ""
    if len(results) == 1:
        return results[0]
    status = next((s for s, _ in results if s in ATTENTION), None) or \
        next((s for s, _ in results if s not in ("current", "none", "noupstream")), "current")
    return status, "\n".join(m for _, m in results if m)


def freshen_one(folder):
    """Fetch and, when safe, fast-forward one repo. Returns (status, message).

    status: none (not a repo), noupstream, offline, current, updated, ahead, dirty,
    dirty-behind, diverged, failed."""
    st = state(folder)
    if st is None:
        return "none", ""
    if not st["upstream"]:
        return "noupstream", f"{st['repo']}: no remote branch to compare with"
    rc, _ = git(st["repo"], "fetch", "--quiet", timeout=FETCH_TIMEOUT)
    if rc != 0:
        return "offline", f"{st['repo']}: could not reach the remote, not updated"
    st = state(folder)
    name = Path(st["repo"]).name
    ahead, behind, dirty = st["ahead"], st["behind"], st["dirty"]
    if behind and ahead:
        return "diverged", (f"{name}: {behind} commit(s) behind and {ahead} not pushed; "
                            "not touched, merge or rebase by hand")
    if behind and dirty:
        return "dirty-behind", (f"{name}: {behind} commit(s) behind but {dirty} file(s) have "
                                "uncommitted changes; not updated")
    if behind:
        rc, out = git(st["repo"], "pull", "--ff-only", "--quiet", timeout=30)
        if rc != 0:
            return "failed", f"{name}: could not fast-forward ({behind} behind), not updated"
        return "updated", f"{name}: updated, pulled {behind} commit(s) from the other machine"
    notes = []
    if ahead:
        notes.append(f"{ahead} commit(s) not pushed")
    if dirty:
        notes.append(f"{dirty} file(s) uncommitted")
    if notes:
        return ("ahead" if ahead else "dirty"), f"{name}: up to date, but " + " and ".join(notes)
    return "current", f"{name}: up to date"
