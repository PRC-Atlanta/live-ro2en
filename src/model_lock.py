"""Pinned model versions (requirements/models.lock.txt) for setup.sh and the app's first-run
download (src/live/model_setup.py, which uses `items`/`subset` and `pin_ref`).

Downloading a Hugging Face repo by commit hash does not write `refs/main` in the cache, and
the offline runtime (HF_HUB_OFFLINE=1, no revision given) resolves models through
`refs/main`. So after each pinned download setup calls `pin_ref`, which points `refs/main`
at the locked commit: the runtime then finds the model, and finds exactly that version."""
from __future__ import annotations

import pathlib
import sys

LOCK = pathlib.Path(__file__).resolve().parents[1] / "requirements" / "models.lock.txt"


def entries(kind: str, lock: pathlib.Path | None = None) -> dict[str, str]:
    """{name: value} for one kind of lock line (hf, dataset, url, sha256)."""
    out = {}
    for line in (lock or LOCK).read_text().splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] == kind:
            out[parts[1]] = parts[2]
    return out


def revision(repo: str, kind: str = "hf", lock: pathlib.Path | None = None) -> str:
    rev = entries(kind, lock).get(repo)
    if not rev:                      # never "latest": a new upload must not change the service
        sys.exit(f"{repo} is not pinned in {(lock or LOCK).name}; add its commit first")
    return rev


def pin_ref(repo: str, rev: str, cache_dir: str | pathlib.Path | None = None, kind: str = "model") -> pathlib.Path:
    """Point the cache's refs/main for `repo` at `rev` (what offline loading resolves)."""
    if cache_dir is None:
        from huggingface_hub import constants
        cache_dir = constants.HF_HUB_CACHE
    ref = pathlib.Path(cache_dir) / f"{kind}s--{repo.replace('/', '--')}" / "refs" / "main"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(rev)
    return ref


def fetch(repo: str, files: list[str] | None = None) -> None:
    """Download `repo` (or just `files`) at its locked commit and pin refs/main to it."""
    from huggingface_hub import hf_hub_download, snapshot_download
    rev = revision(repo)
    if files:
        for f in files:
            hf_hub_download(repo, f, revision=rev)
    else:
        snapshot_download(repo, revision=rev)
    pin_ref(repo, rev)


PLACEHOLDER = "0" * 40               # an unpublished repo (see the comment in models.lock.txt)
TIERS = ("fast", "quality")          # download profiles; each one includes the tiers before it


def items(lock: pathlib.Path | None = None) -> list[dict]:
    """Every hf/dataset lock line with its optional columns:
    {kind, repo, rev, size, profile, files (list, [] = not listed), dir (str|None)}."""
    out = []
    for line in (lock or LOCK).read_text().splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[0] not in ("hf", "dataset"):
            continue
        extra = dict(p.split("=", 1) for p in parts[3:] if "=" in p)
        out.append({"kind": parts[0], "repo": parts[1], "rev": parts[2],
                    "size": int(extra.get("size", 0)), "profile": extra.get("profile", "bench"),
                    "files": [f for f in extra.get("files", "").split(",") if f],
                    "dir": extra.get("dir")})
    return out


def subset(profile: str, lock: pathlib.Path | None = None) -> list[dict]:
    """The lock items a download profile needs: 'fast' = Fast only, 'quality' = Fast + Best."""
    if profile not in TIERS:
        raise ValueError(f"unknown profile {profile!r}; one of {', '.join(TIERS)}")
    want = TIERS[:TIERS.index(profile) + 1]
    return [i for i in items(lock) if i["profile"] in want]


def pin_all(cache_dir: str | pathlib.Path | None = None) -> list[str]:
    """Point refs/main at the locked commit for every pinned repo present in the cache
    (whatever downloaded it: fetch, the CT2 converter)."""
    if cache_dir is None:
        from huggingface_hub import constants
        cache_dir = constants.HF_HUB_CACHE
    done = []
    for repo, rev in entries("hf").items():
        if (pathlib.Path(cache_dir) / f"models--{repo.replace('/', '--')}" / "snapshots" / rev).is_dir():
            pin_ref(repo, rev, cache_dir)
            done.append(repo)
    return done


if __name__ == "__main__":
    if sys.argv[1:] == ["pin-all"]:
        print("pinned refs/main:", ", ".join(pin_all()) or "none")
    else:
        sys.exit("usage: model_lock.py pin-all")
