#!/usr/bin/env bash
# Install claude-tools: the programs it needs (pacman, dnf or apt) and one link per
# command in ~/.local/bin. Nothing is copied, so `git pull` is the whole update.
#
#   ./install.sh                 install missing packages, then link the commands
#   ./install.sh --no-deps       only link the commands
#   ./install.sh --name arch     also name this computer: sessions become arch_<folder>_001
#   ./install.sh --prefix DIR    link into DIR/bin instead of ~/.local/bin
#
# It does not install Claude Code itself; use Anthropic's own installer for that.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
prefix="$HOME/.local"
deps=yes
name=""
while [ $# -gt 0 ]; do
    case "$1" in
        --no-deps) deps=no ;;
        --name)    name=${2:?--name needs a value}; shift ;;
        --prefix)  prefix=${2:?--prefix needs a directory}; shift ;;
        -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "install.sh: unknown option '$1'" >&2; exit 1 ;;
    esac
    shift
done

if [ "$deps" = yes ]; then
    # command -> package name, per package manager
    if command -v pacman >/dev/null 2>&1; then
        mgr="sudo pacman -S --needed"; pkgs="rofi:rofi alacritty:alacritty git:git python3:python"
    elif command -v dnf >/dev/null 2>&1; then
        mgr="sudo dnf install -y";      pkgs="rofi:rofi alacritty:alacritty git:git python3:python3"
    elif command -v apt-get >/dev/null 2>&1; then
        mgr="sudo apt-get install -y";  pkgs="rofi:rofi alacritty:alacritty git:git python3:python3"
    else
        mgr=""; pkgs=""; echo "No pacman, dnf or apt found: install rofi, alacritty, git and python3 yourself."
    fi
    missing=""
    for pair in $pkgs; do
        command -v "${pair%%:*}" >/dev/null 2>&1 || missing="$missing ${pair#*:}"
    done
    if [ -n "$missing" ]; then
        echo "Installing:$missing"
        $mgr $missing
    fi
fi

mkdir -p "$prefix/bin"
for f in "$here"/bin/*; do
    [ -f "$f" ] && [ -x "$f" ] || continue      # only the commands, not caches
    ln -sfn "$f" "$prefix/bin/$(basename "$f")"
    echo "linked $prefix/bin/$(basename "$f")"
done

if [ -n "$name" ]; then
    mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/claude-new"
    echo "$name" > "${XDG_CONFIG_HOME:-$HOME/.config}/claude-new/machine"
    echo "machine name: $name"
fi

command -v claude >/dev/null 2>&1 || echo "Note: Claude Code ('claude') is not installed yet."
case ":$PATH:" in
    *":$prefix/bin:"*) ;;
    *) echo "Note: $prefix/bin is not in your PATH." ;;
esac
