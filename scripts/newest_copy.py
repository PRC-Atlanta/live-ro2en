#!/usr/bin/env python3
"""Which copy of Live RO2EN is newer: this clone or the installed app?

The installed app carries no commit id, so its code (Contents/Resources/app/src) is matched
against the clone's history: the first commit whose files are identical is the one it was
built from. Prints the reasoning, then a last line `use: clone` or `use: app`.

  python3 scripts/newest_copy.py            # also fetches origin to say if a pull is due
  python3 scripts/newest_copy.py --no-fetch
"""
from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
APP = pathlib.Path("/Applications/Live RO2EN.app")
APP_SRC = APP / "Contents" / "Resources" / "app" / "src"
DEPTH = 1000                     # commits searched back


def git(*args) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True).stdout


def blob(path: pathlib.Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def installed() -> dict[str, str]:
    """src/... path -> git blob id of every file the app ships."""
    return {"src/" + p.relative_to(APP_SRC).as_posix(): blob(p)
            for p in APP_SRC.rglob("*") if p.is_file() and "__pycache__" not in p.parts}


def tree(commit: str) -> dict[str, str]:
    out = {}
    for line in git("ls-tree", "-r", commit, "--", "src").splitlines():
        meta, path = line.split("\t", 1)
        out[path] = meta.split()[2]
    return out


def built_from(files: dict[str, str], ref: str) -> tuple[str, int] | None:
    """(commit, commits after it up to ref) of the newest commit in ref matching the app."""
    for i, c in enumerate(git("rev-list", "-n", str(DEPTH), ref).split()):
        t = tree(c)
        if all(t.get(p) == h for p, h in files.items()):
            return c, i
    return None


def short(c: str) -> str:
    return git("log", "-1", "--format=%h %cs %s", c).strip()


def main() -> None:
    if not APP_SRC.is_dir():
        print(f"no installed app at {APP}")
        print("use: clone")
        return
    version = subprocess.run(["defaults", "read", str(APP / "Contents" / "Info"), "CFBundleShortVersionString"],
                             capture_output=True, text=True).stdout.strip()
    print(f"installed app {version or '?'}, clone at {short('HEAD')}")
    if "--no-fetch" not in sys.argv:
        git("fetch", "--quiet", "origin")
    upstream = git("rev-parse", "--abbrev-ref", "@{u}").strip()
    behind = int(git("rev-list", "--count", "HEAD..@{u}").strip() or 0) if upstream else 0
    if behind:
        print(f"clone is {behind} commit(s) behind {upstream}: `git pull` would bring it up to date")
    dirty = git("status", "--porcelain", "--", "src", "config.yaml").strip()
    if dirty:
        print("clone has uncommitted changes in src/ or config.yaml")

    files = installed()
    here = built_from(files, "HEAD")
    if here:
        commit, after = here
        print(f"app was built from {short(commit)}")
        if after or dirty:
            print(f"clone is newer: {after} commit(s) since the app's build" + (" plus local edits" if dirty else ""))
            print("use: clone")
        else:
            print("same code in both: the installed app is the release build")
            print("use: app")
        return
    there = built_from(files, upstream) if upstream else None
    if there:
        print(f"app was built from {short(there[0])}, which this clone doesn't have yet (pull to match)")
    else:
        print(f"app code matches no commit in the clone's last {DEPTH}: built from another checkout")
    print("app is newer")
    print("use: app")


if __name__ == "__main__":
    main()
