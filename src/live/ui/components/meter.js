// Segmented sound-level meter with a GOOD target band, peak hold, Speech and Clip LEDs, a 30 s
// history strip and a verdict in words (docs/APP_PLAN.md §1.3; drawing copied from
// docs/ui_mockup/compare.html between meter:begin/end). Fed by state.audio {rms_db, peak_db,
// speech}; the verdict is computed here so the band can be tuned without touching the backend.
import { S } from "../i18n/strings.en.js";

// starting guess; set from the real soundboard at the church rehearsal (APP_PLAN §4.5)
export const BAND = { MIN_DB: -60, GOOD_LO: -30, GOOD_HI: -10, HOT: -3, CLIP: -1 };
export const VERDICT_CLASS = { idle: "good", good: "good", quiet: "warn", loud: "warn", music: "warn", clip: "bad", none: "bad" };

const dbOr = (v, d = -90) => (typeof v === "number" && Number.isFinite(v) ? v : d);

// The level model, without any DOM (tests import it).
export function createLevel(band = BAND) {
  const M = { val: -90, peak: -90, peakT: 0, clipT: -1e9, sigT: -1e9, speechT: -1e9, t0: null,
              last: [], hist: [], bucket: null, key: null, lastT: null };
  M.push = (a, now) => {
    if (a && a.engine === false) {                       // nothing listens: no level is not "no sound"
      M.decay(now); M.t0 = null; M.last = []; M.key = "idle";
      return M.key;
    }
    if (M.t0 == null) M.t0 = now;
    const rms = dbOr(a?.rms_db), peak = dbOr(a?.peak_db, rms), speech = !!a?.speech;
    M.decay(now);
    if (rms > M.val) M.val = rms;
    if (peak >= M.peak || now - M.peakT > 1500) { M.peak = peak; M.peakT = now; }
    if (peak >= band.CLIP) M.clipT = now;
    if (rms > -70 && a?.ok !== false) M.sigT = now;
    if (speech) M.speechT = now;
    M.last = M.last.concat([{ t: now, rms, speech }]).filter(x => now - x.t <= 5000);   // 5 s
    const slot = Math.floor(now / 250);                                                   // a bar per 250 ms
    if (!M.bucket || M.bucket.slot !== slot) {
      if (M.bucket) M.hist = M.hist.concat([M.bucket]).slice(-120);                       // 30 s
      M.bucket = { slot, db: rms, speech, clip: peak >= band.CLIP };
    } else {
      M.bucket.db = Math.max(M.bucket.db, rms); M.bucket.speech ||= speech; M.bucket.clip ||= peak >= band.CLIP;
    }
    M.key = M.judge(now);
    return M.key;
  };
  // slow release between samples: 24 dB/s
  M.decay = now => { if (M.lastT != null) M.val = Math.max(-90, M.val - 24 * (now - M.lastT) / 1000); M.lastT = now; };
  // the verdict, judged on speech over the last 5 s
  M.judge = now => {
    const sp = M.last.filter(x => x.speech).map(x => x.rms);
    const avg = sp.length ? sp.reduce((p, q) => p + q, 0) / sp.length : null;
    if (M.t0 == null) return "idle";                    // never fed: the backend sends no level yet
    if (now - M.sigT > 3000) return "none";
    if (now - M.clipT < 3000) return "clip";
    if (avg != null && avg < band.GOOD_LO - 5) return "quiet";
    if (avg != null && avg > band.GOOD_HI) return "loud";
    if (avg != null) return "good";
    if (now - Math.max(M.speechT, M.t0) > 8000) return "music";
    return M.key || "good";
  };
  M.bars = () => (M.bucket ? M.hist.concat([M.bucket]) : M.hist);
  return M;
}

export function verdictText(key) { return S.meter.verdict[key] || ["", ""]; }

const MARKUP = `
  <div class="lvl" data-meter>
    <div class="lvl-top">
      <span class="label dev" data-dev></span>
      <span class="sled" data-sled><b></b><span class="label">${S.meter.speech}</span></span>
      <span class="cled" data-cled><b></b><span class="label">${S.meter.clip}</span></span>
    </div>
    <canvas class="meter" aria-hidden="true"></canvas>
    <canvas class="hist" aria-hidden="true"></canvas>
    <div class="verdict" data-verdict aria-live="polite"><strong></strong><span></span></div>
  </div>`;

// Mount into el; returns {update(audio), key(), destroy()}.
export function mountMeter(el, band = BAND) {
  el.innerHTML = MARKUP;
  const q = s => el.querySelector(s);
  const cvM = q("canvas.meter"), cvH = q("canvas.hist"), v = q("[data-verdict]");
  const M = createLevel(band);
  const dpr = () => window.devicePixelRatio || 1;
  const fit = cv => {
    const r = cv.getBoundingClientRect(), w = Math.round(r.width * dpr()), h = Math.round(r.height * dpr());
    if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
    return cv.getContext("2d");
  };
  const xOf = (db, w) => Math.max(0, Math.min(1, (db - band.MIN_DB) / -band.MIN_DB)) * w;
  let lastKey = null;

  function drawMeter() {
    const c = fit(cvM), W = cvM.width, H = cvM.height, k = dpr();
    if (!W || !H) return;
    c.clearRect(0, 0, W, H);
    const barY = 14 * k, barH = H - barY - 12 * k;
    const gx0 = xOf(band.GOOD_LO, W), gx1 = xOf(band.GOOD_HI, W);
    c.fillStyle = "rgba(163,230,53,.10)"; c.fillRect(gx0, barY, gx1 - gx0, barH);
    c.strokeStyle = "rgba(163,230,53,.7)"; c.lineWidth = 1.5 * k;
    c.beginPath(); c.moveTo(gx0, barY - 2 * k); c.lineTo(gx0, 6 * k); c.lineTo(gx1, 6 * k); c.lineTo(gx1, barY - 2 * k); c.stroke();
    c.font = `600 ${9 * k}px -apple-system, system-ui, sans-serif`; c.textBaseline = "middle";
    c.fillStyle = "#0b1c24"; const tw = c.measureText(S.meter.good).width + 8 * k;
    c.fillRect((gx0 + gx1) / 2 - tw / 2, 1 * k, tw, 10 * k);
    c.fillStyle = "#a3e635"; c.textAlign = "center"; c.fillText(S.meter.good, (gx0 + gx1) / 2, 6 * k);
    c.fillStyle = "rgba(245,184,61,.75)"; c.textAlign = "left"; c.fillText(S.meter.quiet, 2 * k, 6 * k);
    c.fillStyle = "rgba(255,93,82,.8)"; c.textAlign = "right"; c.fillText(S.meter.loud, W - 2 * k, 6 * k);
    const n = 48, gap = 2 * k, sw = (W - gap * (n - 1)) / n;
    for (let i = 0; i < n; i++) {
      const db = band.MIN_DB + (i + 1) * (-band.MIN_DB / n), x = i * (sw + gap), lit = db <= M.val + 0.01;
      const col = db > band.HOT ? "255,93,82" : db > band.GOOD_HI ? "245,184,61" : db > band.GOOD_LO ? "163,230,53" : "70,214,236";
      c.fillStyle = lit ? `rgba(${col},${db > band.GOOD_LO ? 0.95 : 0.75})` : `rgba(${col},.10)`;
      c.fillRect(x, barY, sw, barH);
    }
    if (M.peak > band.MIN_DB) { const px = xOf(M.peak, W); c.fillStyle = "#e8fbff"; c.fillRect(px - 1.5 * k, barY - 2 * k, 3 * k, barH + 4 * k); }
    c.fillStyle = "#6f8b93"; c.textAlign = "center"; c.font = `${9 * k}px -apple-system, system-ui, sans-serif`;
    for (const d of W < 420 * k ? [-60, -40, -30, -20, -10, 0] : [-60, -40, -30, -20, -10, -3, 0]) {
      c.fillText(String(d), Math.min(W - 6 * k, Math.max(6 * k, xOf(d, W))), H - 5 * k);
    }
  }

  function drawHist() {
    if (!cvH.offsetParent) return;                       // hidden in the compact layout
    const c = fit(cvH), W = cvH.width, H = cvH.height, k = dpr();
    if (!W || !H) return;
    c.clearRect(0, 0, W, H);
    const bw = W / 120, base = H - 4 * k, hist = M.bars();
    c.strokeStyle = "rgba(163,230,53,.25)"; c.setLineDash([3 * k, 3 * k]); c.lineWidth = 1 * k;
    for (const d of [band.GOOD_LO, band.GOOD_HI]) {
      const y = base - (d - band.MIN_DB) / -band.MIN_DB * (base - 2 * k);
      c.beginPath(); c.moveTo(0, y); c.lineTo(W, y); c.stroke();
    }
    c.setLineDash([]);
    hist.forEach((b, i) => {
      const x = W - (hist.length - i) * bw, h = Math.max(0, (b.db - band.MIN_DB) / -band.MIN_DB) * (base - 2 * k);
      c.fillStyle = b.clip ? "#ff5d52" : b.db > band.GOOD_HI ? "#f5b83d" : b.db >= band.GOOD_LO ? "rgba(70,214,236,.85)"
                  : b.db > -70 ? "rgba(245,184,61,.55)" : "transparent";
      c.fillRect(x + 0.5 * k, base - h, bw - 1 * k, h);
      if (b.speech) { c.fillStyle = "#a3e635"; c.fillRect(x, base + 1.5 * k, bw, 2.5 * k); }
    });
    c.fillStyle = "#6f8b93"; c.font = `${9 * k}px -apple-system, system-ui, sans-serif`; c.textAlign = "left"; c.textBaseline = "top";
    c.fillText(S.meter.last30, 2 * k, 1 * k);
  }

  function paint() {
    const now = performance.now();
    M.decay(now);
    q("[data-sled]").classList.toggle("on", now - M.speechT < 400);
    q("[data-cled]").classList.toggle("on", now - M.clipT < 3000);
    const key = M.judge(now);
    if (key !== lastKey) {                               // only touch the aria-live text on change
      lastKey = key; M.key = key;
      const [word, hint] = verdictText(key);
      v.className = "verdict " + VERDICT_CLASS[key];
      v.dataset.key = key;
      v.querySelector("strong").textContent = word; v.querySelector("span").textContent = hint;
    }
    drawMeter(); drawHist();
  }

  const slow = matchMedia("(prefers-reduced-motion: reduce)").matches;
  let raf = 0, timer = 0, lastPaint = 0;
  const loop = t => {
    if (t - lastPaint >= (slow ? 200 : 33)) { lastPaint = t; paint(); }
    raf = requestAnimationFrame(loop);
  };
  raf = requestAnimationFrame(loop);
  timer = setInterval(() => { if (document.hidden) paint(); }, 1000);  // keep the verdict honest in background

  return {
    update(audio) {
      if (audio) q("[data-dev]").textContent = S.meter.sound(audio.device);
      M.push(audio, performance.now());
    },
    key: () => M.judge(performance.now()),
    destroy() { cancelAnimationFrame(raf); clearInterval(timer); },
  };
}
