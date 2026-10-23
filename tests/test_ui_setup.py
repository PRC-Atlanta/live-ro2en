"""S5b: Setup wizard, Settings and After screens. The flow tests run under node
(tests/ui/s5b_setup.test.mjs) against ui/mock_state.js; the static checks run here."""
import pathlib
import re
import shutil
import subprocess

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
UI = ROOT / "src" / "live" / "ui"
S5B_FILES = [*(UI / "screens" / "setup").glob("*.js"), *(UI / "screens" / "settings").glob("*.js"),
             UI / "screens" / "after.js", UI / "components" / "listeditor.js"]


def test_s5b_flows_under_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    r = subprocess.run([node, "--test", str(ROOT / "tests" / "ui" / "s5b_setup.test.mjs")],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]


def _visible_strings(src):
    """String literals that end up on screen (skips imports, selectors, keys and paths)."""
    src = re.sub(r"^\s*(import|//).*$", "", src, flags=re.M)
    for s in re.findall(r'"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\.)*)`', src):
        s = s[0] or s[1]
        if re.fullmatch(r"[\w./#:-]*", s) or s.startswith(("lt.", "fx-")):
            continue
        yield s


def test_no_technical_terms_in_s5b_copy():
    banned = re.compile(r"\b(latency|VAD|chunk|clause|engine|STT|MT|keywords_en|venv|NLLB|Gemma|model id)\b")
    for f in S5B_FILES:
        for s in _visible_strings(f.read_text()):
            assert not banned.search(s), f"{f.name}: {s!r}"
        assert not re.search(r'"Fast"|"Quality"', f.read_text()), f.name


def test_settings_keys_exist_in_config():
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    keys = re.findall(r'key: "([\w.]+)"', (UI / "screens" / "settings" / "fields.js").read_text())
    assert len(keys) >= 15
    for k in keys:
        sec, name = k.split(".")
        assert sec in ("live", "propresenter"), k
        if k != "live.autostart":                  # new in S3's defaults.yaml
            assert name in cfg[sec], k


def test_s5b_screens_are_not_placeholders():
    for f in [UI / "screens" / "setup" / "index.js", UI / "screens" / "settings" / "index.js", UI / "screens" / "after.js"]:
        src = f.read_text()
        assert "_placeholder.js" not in src and "export function render(" in src, f
