"""List the repo files the packaged app needs at run time (used by packaging/assemble_app.sh).

  python3 -I packaging/app_files.py <repo>      one path per line, relative to <repo>

- all of src/live/ (Python, the old control page and the ui/ folder), minus caches;
- the src/*.py modules that src/live imports, followed transitively (found from the
  `import` statements, so a new dependency is picked up without editing this file);
- the bundled data files the code reads through paths.resource() and friends.
Tests, logs, models, work/, results/ and the venvs are never listed. Model files and the
rehearsal recordings are git-ignored, so assemble_app.sh adds those itself.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
import sys

# Data the orchestrator and the engine read at run time (paths relative to the repo root):
#   defaults.yaml           the base settings layer when LIVE_HOME is set (paths.base_config)
#   glossary.csv            translate.Glossary / preflight (defaults.yaml paths.glossary)
#   keywords_en.txt         Whistle keywords (defaults.yaml whistle.keywords_file)
#   terms.txt               STT hot words (stt_run.terms, defaults.yaml paths.terms)
#   requirements/models.lock.txt   model pins (src/model_lock.py), read by the first-run download
#   data/lexicon/*.txt      word lists of the confidence gate (src/live/gate.py)
DATA = ["defaults.yaml", "glossary.csv", "keywords_en.txt", "terms.txt",
        "requirements/models.lock.txt", "data/lexicon/ro.txt", "data/lexicon/en.txt"]
SKIP_PARTS = {"__pycache__", ".pytest_cache", "tests"}
SKIP_SUFFIX = {".pyc", ".pyo"}


def imports(f: pathlib.Path) -> set[str]:
    out = set()
    for n in ast.walk(ast.parse(f.read_text(), str(f))):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            out.add(n.module.split(".")[0])
    return out


def tracked(repo: pathlib.Path, sub: pathlib.Path) -> list[pathlib.Path]:
    """Only the files git tracks: an untracked note, recording, key or tool cache (.ruff_cache)
    left in src/ must never be sealed into a distributed app."""
    try:
        top = subprocess.run(["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        sys.exit("app_files.py: build the app from a git checkout (the bundle takes only tracked files)")
    if pathlib.Path(top).resolve() != repo.resolve():
        sys.exit(f"app_files.py: {repo} is not the root of its git checkout ({top})")
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z", "--", str(sub.relative_to(repo))],
                         capture_output=True, check=True).stdout
    return [repo / f for f in out.decode().split("\0") if f]


def main(repo: pathlib.Path) -> list[str]:
    src = repo / "src"
    live = src / "live"
    in_git = tracked(repo, src)
    files = [p for p in in_git if p.is_file() and p.is_relative_to(live)
             and not SKIP_PARTS & set(p.relative_to(live).parts) and p.suffix not in SKIP_SUFFIX
             and p.name != ".DS_Store"]
    top = {p.stem: p for p in in_git if p.parent == src and p.suffix == ".py"}
    local = {p.stem for p in files if p.suffix == ".py"}
    # a module someone forgot to `git add` would be left out and fail only on a volunteer's Mac
    untracked = {p.stem for p in live.rglob("*.py") if not SKIP_PARTS & set(p.relative_to(live).parts)} - local
    untracked |= {p.stem for p in src.glob("*.py")} - set(top)
    todo = [p for p in files if p.suffix == ".py"]
    seen: set[pathlib.Path] = set()
    extra: set[pathlib.Path] = set()
    missing: set[str] = set()
    while todo:
        f = todo.pop()
        if f in seen:
            continue
        seen.add(f)
        for m in imports(f):
            if m in untracked:
                missing.add(f"{m} (imported by {f.relative_to(repo)})")
            elif m in top and m not in local:
                extra.add(top[m])
                todo.append(top[m])
    if missing:
        sys.exit("app_files.py: imported but not in git (git add them): " + ", ".join(sorted(missing)))
    for d in DATA:
        if not (repo / d).is_file():
            sys.exit(f"app_files.py: missing {d}")
    rel = sorted({str(p.relative_to(repo)) for p in files + sorted(extra)} | set(DATA))
    return rel


if __name__ == "__main__":
    print("\n".join(main(pathlib.Path(sys.argv[1]).resolve())))
