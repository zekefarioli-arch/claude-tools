# Working with sessions

How I start, continue and cut Claude Code sessions with these tools. A session belongs to the folder where it was started, so the folder matters more than anything else.

## Daily use

| What I want | Command |
|---|---|
| Choose a folder and a session in rofi, with the context usage of each | the key bound to `claude-pick` |
| The same, in a terminal without a screen (a TTY, ssh) | `claudio` |
| Start or continue the session of a folder in the terminal | `claude-new` (in the folder, or `claude-new myproject` for a folder of the projects directory) |
| Continue the last session without being asked | `claude-new --last` |
| Start the next numbered session without being asked | `claude-new --new` |
| Bring the project up to date from GitHub by hand | `claude-fresh [folder]` (it also runs before every session) |
| Open an older session that has no name | `claude -r` in its folder, and pick it from the list; `claude-pick` lists it too |

Leaving a session is `/exit` or Ctrl+D. It is saved, and I continue it later from the same folder.

## When to start a new one

A session that has gone on for a long time answers more slowly and starts to forget, because the context is full. The red dot in `claude-pick` (from 50% of the window) is my signal, but the same is true when I move to an unrelated task. A new session begins with an empty context, so it is fast and focused, and the project files carry what matters.

Before I leave the old session, I ask Claude to update the state docs of the project: `docs/STATE.md`, `docs/CHANGELOG.md` and `docs/DECISIONS.md`. The plan and the open decisions go in the "In progress" section of `STATE.md`, because the new session only knows what was written down and what git shows. After that, I commit and push, so the other computer finds it too.

## How a session is named

The name is `<path>_NNN@<machine>`: the path of the folder from `~` with dashes instead of slashes (`home` in `~`), a three-digit number, and the computer that started it. For example, `Zeke_projects-mini-calendar_002@arch` is the second session of `~/Zeke_projects/mini-calendar`, started on the computer named `arch`. The machine does not matter when `claude-new` looks for the last session or the next number, so a folder keeps one sequence even if two computers add to it.

Older names, `<machine>_<folder>_NNN`, still count. Names that follow neither scheme are shown by `claude-pick` as they are, but `claude-new` does not use them for the numbering.

## Where the sessions are

Claude keeps the sessions of a folder in `~/.claude/projects/<path with every non-alphanumeric character turned into a dash>/`, one `.jsonl` file per session. `claude-new` and `claude-pick` read that folder, which is why there is no database and no counter file. Each computer has its own copy, so a session does not move to the other one yet.

The name of a session is stored in a `customTitle` line of its file. Sessions without that line, such as the ones started with plain `claude`, show the title that Claude generated, or the first characters of their id.
