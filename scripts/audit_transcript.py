"""Audit a transcript against its recording: one offline HTML page, audio inside.

  python3 scripts/audit_transcript.py TRANSCRIPT.md AUDIO [-o OUT.html] [--format mp3|original]

The page shows the transcript one line per row next to a player. Each row plays its own stretch
of the recording, the text is editable, and a row is confirmed once it matches what is said.
"Export" gives the corrected transcript in the same Markdown layout.

Line timings are estimates (no speech model is used): lines are spread over the non-silent parts
of the recording in proportion to their length. Wherever a row is off, play to where it really
starts and press "Starts here"; rows between two such pins are re-spaced.

Transcript layout: an optional "# title" block and one note block, then one spoken line per text
line. Words quoted in the note ("like this") are treated as needing a listen and flag their rows.
The output holds church-private audio, so it is written outside the repository by default
(next to the transcript) and *.audit.html is git-ignored.

Needs ffmpeg for --format mp3 (the default, plays everywhere) and for the silence map; with
--format original and no ffmpeg it still works, with plainer timing estimates."""
from __future__ import annotations

import argparse
import base64
import json
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

MIME = {".mp3": "audio/mpeg", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg",
        ".wav": "audio/wav", ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".flac": "audio/flac",
        ".aac": "audio/aac", ".webm": "audio/webm"}


def parse_transcript(text: str) -> tuple[list[str], list[str]]:
    """(preamble lines, spoken lines). The preamble is the leading "# title" block plus the note
    block after it, when the file starts with a title and has a body after them."""
    blocks: list[list[str]] = [[]]
    for line in text.splitlines():
        if line.strip():
            blocks[-1].append(line.rstrip())
        elif blocks[-1]:
            blocks.append([])
    blocks = [b for b in blocks if b]
    pre: list[list[str]] = []
    if blocks and blocks[0][0].startswith("#"):
        pre.append(blocks.pop(0))
        if len(blocks) > 1:
            pre.append(blocks.pop(0))
    preamble: list[str] = []
    for b in pre:
        preamble += (b if not preamble else ["", *b])
    return preamble, [line for b in blocks for line in b]


def suspect_terms(preamble: list[str]) -> list[str]:
    """Words the note quotes as doubtful: every "quoted" or „quoted” string after the title."""
    note = " ".join(l for l in preamble if not l.startswith("#"))
    found = re.findall(r'["„“]([^"„“”]{2,80})["”]', note)
    return list(dict.fromkeys(t.strip() for t in found if t.strip()))


def parse_silences(log: str, duration: float) -> list[list[float]]:
    """Non-silent [start, end] stretches from ffmpeg silencedetect output."""
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: (-?[\d.]+)", log)]
    speech, at = [], 0.0
    for i, s in enumerate(starts):
        s = max(0.0, s)
        if s - at > 0.05:
            speech.append([round(at, 2), round(s, 2)])
        at = ends[i] if i < len(ends) else duration
    if duration - at > 0.05:
        speech.append([round(at, 2), round(duration, 2)])
    return speech


def probe_duration(audio: pathlib.Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of",
                          "default=nw=1:nk=1", str(audio)], capture_output=True, text=True)
    return float(out.stdout.strip() or 0)


def speech_map(audio: pathlib.Path, duration: float) -> list[list[float]]:
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(audio), "-af",
                          "silencedetect=noise=-35dB:d=0.6", "-f", "null", "-"],
                         capture_output=True, text=True)
    return parse_silences(out.stderr, duration)


def build(transcript: pathlib.Path, audio: pathlib.Path, out: pathlib.Path, fmt: str) -> dict:
    preamble, lines = parse_transcript(transcript.read_text(encoding="utf-8"))
    if not lines:
        sys.exit(f"no spoken lines found in {transcript}")
    have_ffmpeg = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
    duration, speech = 0.0, []
    if have_ffmpeg:
        duration = probe_duration(audio)
        speech = speech_map(audio, duration)
    payload, mime = audio.read_bytes(), MIME.get(audio.suffix.lower(), "audio/mpeg")
    if fmt == "mp3" and audio.suffix.lower() != ".mp3":
        if not have_ffmpeg:
            sys.exit("ffmpeg not found: install it or pass --format original")
        with tempfile.TemporaryDirectory() as tmp:
            mp3 = pathlib.Path(tmp) / "a.mp3"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(audio), "-ac", "1", "-ar",
                            "24000", "-b:a", "48k", str(mp3)], check=True)
            payload, mime = mp3.read_bytes(), "audio/mpeg"
    name = re.sub(r"\.transcript$", "", transcript.stem)
    data = {"name": name, "file": transcript.name, "preamble": preamble, "lines": lines,
            "suspects": suspect_terms(preamble), "duration": duration, "speech": speech}
    page = (PAGE.replace("__TITLE__", "Audit " + name.replace("<", ""))
            .replace("__DATA__", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__AUDIO__", f"data:{mime};base64,{base64.b64encode(payload).decode()}"))
    out.write_text(page, encoding="utf-8")
    return data


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("transcript", type=pathlib.Path)
    ap.add_argument("audio", type=pathlib.Path)
    ap.add_argument("-o", "--out", type=pathlib.Path)
    ap.add_argument("--format", choices=("mp3", "original"), default="mp3",
                    help="audio inside the page: mp3 (default) or the file as it is")
    a = ap.parse_args()
    stem = re.sub(r"\.transcript$", "", a.transcript.stem)
    out = a.out or a.transcript.with_name(stem + ".audit.html")
    d = build(a.transcript, a.audio, out, a.format)
    print(f"{out}  ({len(d['lines'])} lines, {len(d['suspects'])} doubtful terms, "
          f"{d['duration'] / 60:.1f} min, {out.stat().st_size / 1e6:.1f} MB)")


PAGE = r"""<!doctype html>
<html lang="ro">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#f6f5f1;--panel:#fff;--ink:#1d1f23;--mute:#6a6f78;--line:#dcdad2;--accent:#1f5fbf;
--ok:#1c7c4a;--okbg:#e6f3ea;--warn:#9a5b00;--warnbg:#fff1cf;--now:#e9f0fc}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#15171a;--panel:#1e2125;
--ink:#e8e6e1;--mute:#9aa0a9;--line:#33373d;--accent:#7fb0ff;--ok:#6fcf97;--okbg:#17301f;
--warn:#f0c060;--warnbg:#3a2e10;--now:#1c2a40}}
:root[data-theme=dark]{--bg:#15171a;--panel:#1e2125;--ink:#e8e6e1;--mute:#9aa0a9;--line:#33373d;
--accent:#7fb0ff;--ok:#6fcf97;--okbg:#17301f;--warn:#f0c060;--warnbg:#3a2e10;--now:#1c2a40}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
button{font:inherit;color:inherit;background:var(--panel);border:1px solid var(--line);
border-radius:8px;padding:6px 10px;cursor:pointer;min-height:36px}
button:hover{border-color:var(--accent)}
button.pri{background:var(--accent);border-color:var(--accent);color:#fff}
@media (prefers-color-scheme:dark){button.pri{color:#0d1420}}
header{position:sticky;top:0;z-index:5;background:var(--panel);border-bottom:1px solid var(--line);
padding:10px 16px}
.wrap{max-width:920px;margin:0 auto}
h1{font-size:15px;margin:0 0 8px;font-weight:600;overflow-wrap:anywhere}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.bar .sp{flex:1}
#seek{width:100%;margin:8px 0 2px;accent-color:var(--accent)}
.meta{display:flex;justify-content:space-between;gap:12px;color:var(--mute);font-size:13px;
font-variant-numeric:tabular-nums;flex-wrap:wrap}
select{font:inherit;min-height:36px;border-radius:8px;border:1px solid var(--line);
background:var(--panel);color:var(--ink);padding:0 6px}
main{padding:12px 16px 80px}
.tip{color:var(--mute);font-size:14px;margin:4px 0 12px}
.tip summary{cursor:pointer}
kbd{font:12px ui-monospace,monospace;border:1px solid var(--line);border-radius:4px;padding:1px 4px;
background:var(--bg)}
.row{display:grid;grid-template-columns:64px 1fr;gap:4px 10px;background:var(--panel);
border:1px solid var(--line);border-left:4px solid var(--line);border-radius:8px;padding:8px 10px;
margin:0 0 8px}
.row.sus{border-left-color:var(--warn)}
.row.ok{border-left-color:var(--ok)}
.row.now{background:var(--now)}
.row.sel{outline:2px solid var(--accent);outline-offset:1px}
.t{font:13px ui-monospace,monospace;color:var(--accent);padding:4px 0;min-height:32px;border:0;
background:none;text-align:left;font-variant-numeric:tabular-nums}
.t.pin{font-weight:700}
.t small{display:block;color:var(--mute);font-weight:400}
textarea{width:100%;font:inherit;color:inherit;background:transparent;border:1px solid transparent;
border-radius:6px;padding:4px 6px;resize:none;overflow:hidden;display:block}
textarea:hover{border-color:var(--line)}
textarea:focus{border-color:var(--accent);outline:0;background:var(--bg)}
.acts{grid-column:2;display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:13px}
.acts button{min-height:32px;padding:3px 9px;font-size:13px}
.badge{border-radius:10px;padding:1px 8px;font-size:12px}
.badge.w{background:var(--warnbg);color:var(--warn)}
.badge.g{background:var(--okbg);color:var(--ok)}
.badge.e{border:1px solid var(--line);color:var(--mute)}
.row.ok .okb{background:var(--okbg);border-color:var(--ok);color:var(--ok)}
.hide{display:none!important}
dialog{width:min(860px,94vw);border:1px solid var(--line);border-radius:12px;background:var(--panel);
color:var(--ink);padding:16px}
dialog::backdrop{background:rgba(0,0,0,.45)}
#md{width:100%;height:50vh;border:1px solid var(--line);font:13px/1.45 ui-monospace,monospace;
overflow:auto;background:var(--bg);resize:vertical}
@media (max-width:560px){.row{grid-template-columns:1fr}.acts{grid-column:1}}
</style>
</head>
<body>
<header><div class="wrap">
<h1 id="ttl"></h1>
<div class="bar">
<button id="play" class="pri" aria-label="Play or pause">Play</button>
<button id="back" aria-label="Back 3 seconds">−3 s</button>
<button id="fwd" aria-label="Forward 3 seconds">+3 s</button>
<select id="rate" aria-label="Speed"><option>0.75</option><option selected>1</option>
<option>1.25</option><option>1.5</option></select>
<label style="font-size:14px"><input type="checkbox" id="follow" checked> Follow</label>
<span class="sp"></span>
<select id="filter" aria-label="Show"><option value="all">All lines</option>
<option value="todo">Not confirmed</option><option value="sus">Needs a listen</option>
<option value="edit">Edited</option></select>
<button id="export">Export</button>
</div>
<input id="seek" type="range" min="0" max="1" step="0.1" value="0" aria-label="Position">
<div class="meta"><span id="clock">0:00 / 0:00</span><span id="count"></span></div>
<audio id="au" preload="auto" src="__AUDIO__"></audio>
</div></header>
<main><div class="wrap">
<details class="tip"><summary>How this works, and keys</summary>
<p>Click a time to hear that line. Fix the text if it differs from what is said, then press
<b>Confirm</b>. Times are estimates: when a line is off, play to where it really starts and press
<b>Starts here</b>. The lines between two pinned ones are re-spaced, so a few pins bring the
whole page into step. Work is kept in this browser; <b>Export</b> gives the corrected file.</p>
<p><kbd>Ctrl</kbd>+<kbd>Enter</kbd> confirm and play the next line ·
<kbd>Alt</kbd>+<kbd>R</kbd> replay the line · <kbd>Alt</kbd>+<kbd>P</kbd> play / pause ·
<kbd>Alt</kbd>+<kbd>←</kbd> <kbd>→</kbd> 3 s back / forward ·
<kbd>Alt</kbd>+<kbd>S</kbd> the selected line starts here ·
<kbd>Alt</kbd>+<kbd>N</kbd> the next line starts here (tap through while it plays).
Outside a text box: <kbd>Space</kbd> play / pause, <kbd>←</kbd> <kbd>→</kbd> seek.</p></details>
<div id="rows"></div>
</div></main>
<dialog id="dlg">
<p id="sum" style="margin-top:0"></p>
<textarea id="md" readonly></textarea>
<div class="bar" style="margin-top:10px"><button id="dl" class="pri">Download .md</button>
<button id="cp">Copy</button><span id="cpmsg" class="tip" style="margin:0"></span><span class="sp"></span>
<button id="reset">Discard all my changes</button><button id="close">Close</button></div>
</dialog>
<script>
const D = __DATA__;
const $ = id => document.getElementById(id), au = $('au'), N = D.lines.length;
const KEY = 'audit:' + D.name + ':' + N + ':' + D.lines.join('\n').length;
let S = {text: {}, ok: {}, pin: {}};
try { S = Object.assign(S, JSON.parse(localStorage.getItem(KEY) || '{}')); } catch (e) {}
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) {} };
let dur = D.duration || 0, starts = [], sel = 0, stopAt = null, nowIdx = -1;

const fmt = s => { s = Math.max(0, s); const m = Math.floor(s / 60);
  return m + ':' + String(Math.floor(s % 60)).padStart(2, '0'); };
const cur = i => (i in S.text ? S.text[i] : D.lines[i]);
const low = s => s.toLocaleLowerCase('ro');
const susOf = D.lines.map(l => D.suspects.filter(t => low(l).includes(low(t))));

// Speech clock: seconds of non-silence before wall time w, and back.
const SP = () => (D.speech.length ? D.speech : [[0, dur]]);
function toSp(w) { let a = 0; for (const [s, e] of SP()) { if (w <= s) break; a += Math.min(w, e) - s; } return a; }
function toWall(x) { let a = 0, last = 0; for (const [s, e] of SP()) {
  if (x <= a + (e - s)) return s + (x - a); a += e - s; last = e; } return last; }
function retime() {
  const w = D.lines.map(l => l.length + 12), cum = [0];
  for (let i = 0; i < N; i++) cum.push(cum[i] + w[i]);
  const first = SP()[0][0];
  const pts = [[0, 0 in S.pin ? S.pin[0] : first]];
  for (let i = 1; i < N; i++) if (i in S.pin) pts.push([i, S.pin[i]]);
  pts.push([N, Math.max(dur, pts[pts.length - 1][1])]);
  starts = new Array(N);
  for (let k = 0; k + 1 < pts.length; k++) {
    const [p, tp] = pts[k], [q, tq] = pts[k + 1], a = toSp(tp), b = toSp(tq);
    for (let i = p; i < q; i++) {
      const f = (cum[i] - cum[p]) / (cum[q] - cum[p]);
      starts[i] = i === p ? tp : (b - a > 1 ? Math.min(tq, Math.max(tp, toWall(a + (b - a) * f))) : tp + (tq - tp) * f);
    }
  }
}
const endOf = i => (i + 1 < N ? starts[i + 1] : dur);

const rows = [];
function build() {
  const host = $('rows');
  D.lines.forEach((_, i) => {
    const r = document.createElement('div'); r.className = 'row';
    r.innerHTML = '<button class="t" title="Play this line"></button><textarea rows="1" spellcheck="false"></textarea>' +
      '<div class="acts"><button class="okb">Confirm</button><button class="here" title="This line starts at the current position">Starts here</button>' +
      '<button class="unpin hide" title="Go back to the estimated time">Unpin</button><button class="undo hide">Undo edit</button>' +
      '<span class="badge w hide"></span><span class="badge e hide">edited</span></div>';
    const ta = r.querySelector('textarea'); ta.value = cur(i);
    ta.setAttribute('aria-label', 'Line ' + (i + 1));
    ta.addEventListener('input', () => { if (ta.value === D.lines[i]) delete S.text[i]; else S.text[i] = ta.value;
      delete S.ok[i]; save(); fit(ta); paint(i); counts(); });
    ta.addEventListener('focus', () => select(i));
    r.querySelector('.t').onclick = () => { select(i); playLine(i); };
    r.querySelector('.okb').onclick = () => { toggleOk(i); };
    r.querySelector('.here').onclick = () => pinHere(i);
    r.querySelector('.unpin').onclick = () => { delete S.pin[i]; save(); retime(); paintAll(); };
    r.querySelector('.undo').onclick = () => { delete S.text[i]; ta.value = D.lines[i]; save(); fit(ta); paint(i); counts(); };
    r.addEventListener('click', () => select(i));
    host.appendChild(r); rows.push(r);
  });
}
function fit(ta) { ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 2 + 'px'; }
function paint(i) {
  const r = rows[i], t = r.querySelector('.t'), w = r.querySelector('.badge.w');
  t.innerHTML = fmt(starts[i]) + '<small>' + (i + 1) + (i in S.pin ? ' · pinned' : '') + '</small>';
  t.classList.toggle('pin', i in S.pin);
  r.classList.toggle('ok', !!S.ok[i]); r.classList.toggle('sus', susOf[i].length > 0 && !S.ok[i]);
  r.classList.toggle('sel', i === sel); r.classList.toggle('now', i === nowIdx);
  r.querySelector('.okb').textContent = S.ok[i] ? 'Confirmed ✓' : 'Confirm';
  r.querySelector('.unpin').classList.toggle('hide', !(i in S.pin));
  r.querySelector('.undo').classList.toggle('hide', !(i in S.text));
  r.querySelector('.badge.e').classList.toggle('hide', !(i in S.text));
  w.classList.toggle('hide', !susOf[i].length); w.textContent = 'listen: ' + susOf[i].join(', ');
  const f = $('filter').value;
  r.classList.toggle('hide', (f === 'todo' && !!S.ok[i]) || (f === 'sus' && !susOf[i].length) || (f === 'edit' && !(i in S.text)));
}
function paintAll() { for (let i = 0; i < N; i++) paint(i); counts(); rows.forEach(r => fit(r.querySelector('textarea'))); }
function counts() {
  const ok = Object.keys(S.ok).length, ed = Object.keys(S.text).length;
  const sus = susOf.filter((s, i) => s.length && !S.ok[i]).length;
  $('count').textContent = ok + ' of ' + N + ' confirmed · ' + ed + ' edited · ' + sus + ' flagged left';
}
function select(i) { if (i === sel) return; const o = sel; sel = i; paint(o); paint(i); }
function toggleOk(i) { if (S.ok[i]) delete S.ok[i]; else S.ok[i] = 1; save(); paint(i); counts(); }
function playLine(i) { au.currentTime = Math.max(0, starts[i] - 0.4); stopAt = endOf(i) + 0.4; au.play(); }
function pinHere(i) {
  const t = au.currentTime; S.pin[i] = Math.round(t * 10) / 10;
  for (const k of Object.keys(S.pin)) { const j = +k; if ((j < i && S.pin[j] >= t) || (j > i && S.pin[j] <= t)) delete S.pin[j]; }
  save(); retime(); paintAll();
}
function next(i) { for (let j = i + 1; j < N; j++) if (!rows[j].classList.contains('hide')) return j; return -1; }
function tick() {
  const t = au.currentTime;
  if (stopAt !== null && t >= stopAt) { au.pause(); stopAt = null; }
  $('seek').value = t; $('clock').textContent = fmt(t) + ' / ' + fmt(dur);
  let i = -1; for (let j = 0; j < N; j++) { if (starts[j] <= t) i = j; else break; }
  if (i !== nowIdx) { const o = nowIdx; nowIdx = i; if (o >= 0) paint(o); if (i >= 0) { paint(i);
    if ($('follow').checked && !au.paused && stopAt === null) rows[i].scrollIntoView({block: 'center', behavior: 'smooth'}); } }
}
function toggle() { stopAt = null; if (au.paused) au.play(); else au.pause(); }
const seekBy = d => { stopAt = null; au.currentTime = Math.min(dur, Math.max(0, au.currentTime + d)); };

function exportMd() {
  const ok = Object.keys(S.ok).length, ed = Object.keys(S.text).length, all = ok === N;
  const todo = []; for (let i = 0; i < N; i++) if (!S.ok[i]) todo.push(i + 1);
  let pre = D.preamble.slice();
  if (all) pre = pre.filter(l => l.startsWith('#')).map(l => l.replace('(UNVERIFIED)', '(verified)'));
  const stamp = new Date().toISOString().slice(0, 10);
  const note = all ? 'Checked line by line against the audio on ' + stamp + ': all ' + N + ' lines confirmed, ' + ed + ' corrected.'
    : 'Partly checked against the audio on ' + stamp + ': ' + ok + ' of ' + N + ' lines confirmed, ' + ed + ' corrected. Not yet confirmed (line numbers of the text below): ' + todo.join(', ') + '.';
  const body = D.lines.map((_, i) => cur(i).replace(/\s*\n\s*/g, ' ').trim()).filter(Boolean);
  $('sum').textContent = note;
  return (pre.length ? pre.join('\n') + '\n\n' : '') + note + '\n\n' + body.join('\n') + '\n';
}

$('ttl').textContent = 'Audit: ' + D.name;
$('play').onclick = toggle; $('back').onclick = () => seekBy(-3); $('fwd').onclick = () => seekBy(3);
$('rate').onchange = e => { au.playbackRate = +e.target.value; };
$('seek').oninput = e => { stopAt = null; au.currentTime = +e.target.value; };
$('filter').onchange = paintAll;
au.addEventListener('timeupdate', tick);
au.addEventListener('play', () => { $('play').textContent = 'Pause'; });
au.addEventListener('pause', () => { $('play').textContent = 'Play'; });
au.addEventListener('error', () => { $('clock').textContent = 'This browser cannot play the audio in this page.'; });
function ready() { if (isFinite(au.duration) && au.duration > 0) dur = au.duration; $('seek').max = dur; retime(); paintAll(); tick(); }
au.addEventListener('loadedmetadata', ready); au.addEventListener('durationchange', ready);
(function loop() { if (!au.paused) tick(); requestAnimationFrame(loop); })();
$('export').onclick = () => { $('md').value = exportMd(); $('cpmsg').textContent = ''; $('dlg').showModal(); };
$('close').onclick = () => $('dlg').close();
$('dl').onclick = () => { const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([$('md').value], {type: 'text/markdown'})); a.download = D.file; a.click(); };
$('cp').onclick = async () => { let ok = false;
  try { await navigator.clipboard.writeText($('md').value); ok = true; } catch (e) { $('md').select(); try { ok = document.execCommand('copy'); } catch (e2) {} }
  $('cpmsg').textContent = ok ? 'Copied.' : 'Select the text above and copy it.'; };
$('reset').onclick = () => { if (!confirm('Discard every edit, confirmation and pin on this page?')) return;
  S = {text: {}, ok: {}, pin: {}}; save(); rows.forEach((r, i) => { r.querySelector('textarea').value = D.lines[i]; });
  retime(); paintAll(); $('dlg').close(); };
document.addEventListener('keydown', e => {
  const typing = /TEXTAREA|INPUT|SELECT/.test(document.activeElement.tagName) && document.activeElement.id !== 'seek';
  if ($('dlg').open) return;
  if (e.ctrlKey && e.key === 'Enter') { e.preventDefault(); if (!S.ok[sel]) toggleOk(sel);
    const j = next(sel); if (j >= 0) { select(j); rows[j].querySelector('textarea').focus({preventScroll: true});
      rows[j].scrollIntoView({block: 'center', behavior: 'smooth'}); playLine(j); } return; }
  if (e.altKey) {
    const c = e.code;
    if (c === 'KeyR') playLine(sel); else if (c === 'KeyP') toggle(); else if (c === 'ArrowLeft') seekBy(-3);
    else if (c === 'ArrowRight') seekBy(3); else if (c === 'KeyS') pinHere(sel);
    else if (c === 'KeyN') { const j = next(sel); if (j >= 0) { select(j); pinHere(j); rows[j].scrollIntoView({block: 'center', behavior: 'smooth'}); } }
    else return;
    e.preventDefault(); return;
  }
  if (typing) return;
  if (e.code === 'Space') { e.preventDefault(); toggle(); }
  else if (e.code === 'ArrowLeft') seekBy(-3); else if (e.code === 'ArrowRight') seekBy(3);
});
build(); ready();
window.addEventListener('resize', () => rows.forEach(r => fit(r.querySelector('textarea'))));
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
