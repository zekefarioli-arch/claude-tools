# claude-tools

Three small terminal tools that start Claude Code in the right folder, in the right session, with the project up to date.

## Why I built it

I work on two computers, a Fedora laptop and an Arch desktop, and both share their projects through GitHub. Claude Code keeps its sessions per folder, so a session started in the wrong folder is hard to find again, and a long session slowly becomes slow and forgetful. On top of that, I kept opening a project on one computer without noticing that the other one had already pushed new commits.

These three problems have the same root: nothing checks the situation before Claude starts. For this reason I wrote one command for each question. Which folder and which session? Is the context getting too long? Is the code up to date? I chose plain scripts and rofi instead of a daemon or a terminal UI framework because I keep my systems light, and because a script that does one job is easy to read when it breaks.

## What is inside

| Command | What it does |
|---|---|
| `claude-pick` | A rofi launcher: you choose a folder, then a session, and it opens a new terminal with Claude in it. It shows how full each session is and whether the folder has unpushed work. |
| `claude-new` | Starts or continues the session of a folder, named `<path>_NNN@<machine>`. |
| `claude-fresh` | Brings the project up to date from GitHub before Claude starts, but only when that is safe. |

The three of them share one small Python module, `lib/claude-tools/gitfresh.py`, which does the git work.

## The launcher

For the launcher, think of the context of a session as a desk. On a clean desk Claude finds things quickly; a desk covered in papers still works, but everything takes longer and some papers get lost. The launcher shows how covered each desk is before I sit down.

From my side, the flow has three steps. First, I press the key I bound to `claude-pick` (Super+a in my xmonad). The first list shows `~` for system things, then every folder where Claude has already worked, most recent first, and then the other folders of my projects directory. After I choose a folder, a second list shows its sessions, and my choice opens a new terminal in that folder.

```
●  System (home)               30%    470 replies    5 MB  1 min ago   ↑3 ✎
●  ~/Zeke_projects/dotfiles    62%    310 replies    4 MB  2 h ago     refresh?
○  ~/Zeke_projects/idle        no sessions yet
＋ Other folder…
```

The dot is green below 35%, yellow below 50% and red from 50%. The percentage is the size of the prompt in the last reply of the session, that is input tokens plus cache tokens, divided by the context window. The marks at the end come from git: `↑3` is three commits not pushed, `✎` is uncommitted files and `↓2` is two commits behind.

When a session is in the red, the second list puts **New session with handoff** first. The handoff starts an empty session whose first message asks Claude to read the state docs of the project, to run `git status` and `git log`, and to tell me where we were. It only knows what was written down, so I keep a short "In progress" section in `docs/STATE.md` and ask Claude to update it before I leave a long session.

The percentage depends on the size of the window, which I had to assume: 200,000 tokens by default. If a session is already above that number, the script assumes a window of 1,000,000.

## Keeping the project up to date

Before every session, `claude-fresh` does what I would do by hand when I arrive at the other computer: it reads what changed while I was away. It runs a `git fetch` with a four-second limit and, only if the project is behind and has no uncommitted changes, it runs `git pull --ff-only`. A fast-forward only adds commits on top of the ones I already have, so nothing of mine can be overwritten.

| State of the folder | What happens |
|---|---|
| Behind and clean | It fast-forwards and says how many commits it pulled. |
| Behind, with uncommitted changes | It touches nothing and waits for Enter. |
| Diverged (behind and ahead) | It touches nothing and waits for Enter. |
| Ahead only | It says how many commits are not pushed. It never pushes by itself. |
| No network | It skips the update and says so. |

In `~` there is no single project, so it works on the home project, `~/dotfiles` by default, on this repository, and on any other repository listed in `~/.config/claude-tools/repos`. The `claude-pick` list also resolves `~` to the home project when it draws the git marks.

The main cost is the network: a slow connection adds up to four seconds before each session. The `↓` mark in the list is only as fresh as the last fetch, so it is exact just after a project is opened.

## Session names

`claude-new` names a session `<path>_NNN@<machine>`, for example `Zeke_projects-mini-calendar_002@arch`. The path part is the path of the folder from `~` with the slashes turned into dashes, and it is `home` in `~`. Using the whole path keeps `~/work/app` and `~/Zeke_projects/app` apart, which the last folder name alone could not do.

The machine part comes after the `@` and it only labels the session. It is the first four characters of `/etc/machine-id`, or the text of `~/.config/claude-new/machine` if I want a readable name such as `arch`. To find the last session of a folder and the next number, `claude-new` ignores the machine: the most recent session wins, and the next number is the highest one plus one. For this reason, if the sessions of two computers ever end up in the same place, the numbers do not collide and a folder lists the work of both. The numbers come from the sessions that Claude already saved, so there is no counter file to keep in sync.

Sessions named with my first scheme, `<machine>_<folder>_NNN`, keep working: the ones of this computer still count for the numbering and for finding the last session.

It has two modes without questions, which `claude-pick` uses: `claude-new --new [folder]` starts the next number, and `claude-new --last [folder]` continues the most recent session. Without a flag, it shows the most recent session and asks whether to continue it or to start a new one.

At the moment, the sessions stay on the computer where they were created. The names already work across machines, so sharing them is a matter of copying files, which I have not done yet.

## Installation

```sh
git clone https://github.com/zekefarioli-arch/claude-tools ~/Zeke_projects/claude-tools
cd ~/Zeke_projects/claude-tools
./install.sh --name arch
```

`install.sh` detects `pacman`, `dnf` or `apt`, installs only the programs that are missing, and links the three commands into `~/.local/bin`. Nothing is copied, so `git pull` is the whole update. `--no-deps` skips the packages, `--name` writes the machine name, and `--prefix` changes the destination.

| Needed | Why |
|---|---|
| Claude Code | The tool being launched. `install.sh` does not install it. |
| Python 3 | The three commands use only its standard library. |
| git | `claude-fresh` and the git marks. |
| rofi | The two lists of `claude-pick`. |
| A terminal | alacritty, wezterm, kitty, foot, terminator or xterm; `claude-pick` opens each session in a new window of the first one it finds, or of the one you choose. |

Then I bind a key to `claude-pick`. In i3 or sway it is one line, `bindsym $mod+a exec claude-pick`; in my xmonad it is an action in a small config file.

## Configuration

| Setting | Default | What it changes |
|---|---|---|
| `CLAUDE_PROJECTS_DIR`, or `~/.config/claude-tools/projects_dir` | `~/Zeke_projects` | The folder whose subfolders appear in the first list, and where `claude-new myproject` looks. The file is the way to set it for a key bound in a window manager, which does not inherit environment variables. |
| `CLAUDE_TERMINAL`, or `~/.config/claude-tools/terminal` | the first installed of alacritty, wezterm, kitty, foot, terminator, xterm | The terminal in which `claude-pick` opens a session. Any other name is run as `NAME -e command`. |
| `CLAUDE_HOME_PROJECT` | `~/dotfiles` | The repository that `~` stands for. |
| `~/.config/claude-tools/repos` | none | More repositories to update when Claude is opened in `~`, one path per line. This repository is always included. |
| `~/.config/claude-pick/window` | 200000 | The context window used for the percentage. |
| `~/.config/claude-new/machine` | first 4 of `/etc/machine-id` | The machine part of the session names. |

## Tests

```sh
python3 -m unittest discover -s tests
```

The tests for `claude-fresh` use real git repositories: a bare remote and two clones play the two computers. The tests for `claude-pick` cover the logic, such as the percentage, the order of the lists and the command that opens each session, but never rofi or a terminal.

## Limits and next steps

The rofi screens are the part I can only check by eye, so the tests stop at the data they receive. The window size is an assumption, and the percentage drops by itself after a `/compact`. My next step is to share the sessions between the two computers, so that a folder lists the sessions of both.
