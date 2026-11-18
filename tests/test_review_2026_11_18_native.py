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


def _fake_sparkle(root):
    """A stand-in sign_update where SwiftPM puts Sparkle's tools (macos/.build/artifacts)."""
    bin_dir = root / "macos/.build/artifacts/sparkle/Sparkle/bin"
    bin_dir.mkdir(parents=True)
    tool = bin_dir / "sign_update"
    tool.write_text('#!/bin/sh\necho \'sparkle:edSignature="x" length="1"\'\n')
    tool.chmod(0o755)
    return tool


def _fake_info_plist(root, version="9.9.9", build="99"):
    (root / "macos").mkdir(exist_ok=True)
    (root / "macos/Info.plist").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleShortVersionString</key><string>{version}</string>
<key>CFBundleVersion</key><string>{build}</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
</dict></plist>
""")


def test_make_appcast_uses_the_swiftpm_sparkle_and_says_how_to_get_it(tmp_path):
    root = _appcast_copy(tmp_path)
    text = (root / "packaging/make_appcast.sh").read_text()
    assert "macos/.build/artifacts/sparkle/Sparkle/bin" in text
    assert "curl" not in text and "SPARKLE_SHA256" not in text     # no tarball download any more
    r = subprocess.run(["bash", str(root / "packaging/make_appcast.sh"), str(tmp_path / "dist")],
                       capture_output=True, text=True)
    assert r.returncode != 0
    assert "no Sparkle sign_update" in r.stderr and "swift package resolve" in r.stderr
    assert not (root / "build").exists()          # nothing downloaded or created


def test_make_appcast_finds_the_swiftpm_sign_update(tmp_path):
    root = _appcast_copy(tmp_path)
    _fake_sparkle(root)
    _fake_info_plist(root)
    dist = tmp_path / "dist"
    dist.mkdir()
    r = subprocess.run(["bash", str(root / "packaging/make_appcast.sh"), str(dist)],
                       capture_output=True, text=True, env={**os.environ, "APPCAST_ONLY": "ro2en"})
    # past the tools check, stopped at the missing DMG
    assert r.returncode != 0
    assert "no Sparkle sign_update" not in r.stderr
    assert "LiveRO2EN-9.9.9.dmg" in r.stdout + r.stderr


@pytest.mark.skipif(shutil.which("hdiutil") is None, reason="needs macOS hdiutil")
def test_make_appcast_detaches_and_removes_the_mount_when_the_dmg_has_no_app(tmp_path):
    root = _appcast_copy(tmp_path)
    _fake_sparkle(root)
    _fake_info_plist(root)
    dist = tmp_path / "dist"
    dist.mkdir()
    src = tmp_path / "src"
    src.mkdir()
    (src / "readme.txt").write_text("no app here\n")
    subprocess.run(["hdiutil", "create", "-quiet", "-volname", "T", "-srcfolder", str(src),
                    "-format", "UDZO", str(dist / "LiveRO2EN-9.9.9.dmg")], check=True)
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    r = subprocess.run(["bash", str(root / "packaging/make_appcast.sh"), str(dist)],
                       capture_output=True, text=True,
                       env={**os.environ, "APPCAST_ONLY": "ro2en", "TMPDIR": str(tmpdir),
                            "APPCAST_URL_PREFIX": "http://127.0.0.1:8799/"})
    assert r.returncode != 0
    assert "no .app inside" in r.stderr
    assert list(tmpdir.iterdir()) == []          # the mount point was detached and removed
    mounts = subprocess.run(["mount"], capture_output=True, text=True).stdout
    assert str(tmpdir) not in mounts and str(tmpdir.resolve()) not in mounts


def test_make_appcast_cleans_the_mount_on_every_exit():
    text = (REPO / "packaging/make_appcast.sh").read_text()
    assert "trap unmount EXIT" in text
    assert "detach -quiet -force" in text        # a busy volume is forced off
    assert 'die "could not mount' in text


# --- Swift shell (no Swift test target: pin the source) --------------------------------------
SWIFT = REPO / "macos/Sources/LiveRO2EN"


def test_resumed_launch_keeps_resuming_until_it_goes_live():
    src = (SWIFT / "ProcessSupervisor.swift").read_text()
    assert "resumePending = resume" in src                     # set by a resumed launch
    assert src.count("self.noteState(obj)") == 2               # readiness and every poll use it
    note = src[src.index("private func noteState"):src.index("private func getJSON")]
    # while the models load, a second crash resumes again; error/needs setup/Stop end it
    assert '"loading"' in note and "wasLive = true; return" in note
    assert "resumePending = false" in note


def test_display_crash_reloads_go_through_the_backoff():
    src = (SWIFT / "DisplayWindow.swift").read_text()
    body = src[src.index("func webViewWebContentProcessDidTerminate"):src.index("func webView(_ webView")]
    assert "retryLater(after:" in body and "load(url)" not in body
    assert "crashesBeforeSlowing" in body and "slowRetry" in body
