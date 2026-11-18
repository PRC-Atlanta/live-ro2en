"""Review 2026-11-18, native and packaging fixes: the release scripts refuse unsafe inputs."""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packaging"))
import app_files  # noqa: E402

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t",
                    "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)


def _fake_repo(tmp_path):
    """The smallest checkout app_files.main() accepts, committed."""
    repo = tmp_path / "repo"
    (repo / "src/live").mkdir(parents=True)
    (repo / "src/voice").mkdir(parents=True)
    (repo / "src/live/app.py").write_text("x = 1\n")
    (repo / "src/voice/voice.py").write_text("y = 1\n")
    for d in app_files.DATA:
        (repo / d).parent.mkdir(parents=True, exist_ok=True)
        (repo / d).write_text("data\n")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _run_app_files(repo, **env):
    return subprocess.run([sys.executable, "-I", str(REPO / "packaging/app_files.py"), str(repo)],
                          capture_output=True, text=True, env={**os.environ, **env})


@needs_git
def test_app_files_clean_tree_lists_files(tmp_path):
    repo = _fake_repo(tmp_path)
    r = _run_app_files(repo)
    assert r.returncode == 0, r.stderr
    assert "src/live/app.py" in r.stdout.split()


@needs_git
def test_app_files_refuses_uncommitted_bundled_file(tmp_path):
    repo = _fake_repo(tmp_path)
    (repo / "src/live/app.py").write_text("x = 2\n")
    r = _run_app_files(repo, ALLOW_DIRTY="")
    assert r.returncode != 0
    assert "uncommitted" in r.stderr and "src/live/app.py" in r.stderr


@needs_git
def test_app_files_allow_dirty_overrides(tmp_path):
    repo = _fake_repo(tmp_path)
    (repo / "glossary.csv").write_text("changed\n")
    r = _run_app_files(repo, ALLOW_DIRTY="1")
    assert r.returncode == 0, r.stderr
    assert "glossary.csv" in r.stdout.split()


def _appcast_copy(tmp_path):
    """make_appcast.sh in a throwaway repo layout, so it never touches the real build/cache."""
    root = tmp_path / "r"
    (root / "packaging").mkdir(parents=True)
    shutil.copy(REPO / "packaging/make_appcast.sh", root / "packaging/make_appcast.sh")
    return root


def test_make_appcast_needs_a_sparkle_pin(tmp_path):
    root = _appcast_copy(tmp_path)
    script = root / "packaging/make_appcast.sh"
    text = script.read_text()
    # whatever the pin is now, an empty one must stop the script before any download
    import re
    script.write_text(re.sub(r'^SPARKLE_SHA256_PIN=".*"$', 'SPARKLE_SHA256_PIN=""', text, flags=re.M))
    env = {k: v for k, v in os.environ.items() if k != "SPARKLE_SHA256"}
    r = subprocess.run(["bash", str(script), str(tmp_path / "dist")], capture_output=True, text=True, env=env)
    assert r.returncode != 0
    assert "no sha256 pinned" in r.stderr
    assert not (root / "build").exists()          # nothing downloaded or created


def test_make_appcast_rejects_a_cached_tarball_that_does_not_match(tmp_path):
    root = _appcast_copy(tmp_path)
    cache = root / "build/cache"
    cache.mkdir(parents=True)
    tarball = cache / "Sparkle-2.10.1.tar.xz"
    tarball.write_bytes(b"not sparkle")
    r = subprocess.run(["bash", str(root / "packaging/make_appcast.sh"), str(tmp_path / "dist")],
                       capture_output=True, text=True, env={**os.environ, "SPARKLE_SHA256": "0" * 64})
    assert r.returncode != 0
    assert "does not match its sha256 pin" in r.stderr
    assert not (cache / "sparkle-2.10.1").exists()   # never unpacked
