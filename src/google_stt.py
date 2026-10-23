"""DEV-ONLY Google Speech-to-Text V2 reference transcription (Chirp 2 / Chirp 3).
Never imported by the live app.

  python src/google_stt.py prepare FILE...           # FLAC 16k mono + upload to gs://BUCKET/input/
  python src/google_stt.py submit FILE... [--model chirp_2] [--tag bible] [--dry-run]
  python src/google_stt.py poll [--once]             # re-attach to pending ops, download results
  python src/google_stt.py status                    # pending ops + minutes used

Operation names are saved to logs/google_ops.json the moment they are created, so a
restart re-attaches instead of resubmitting. Every request is logged to
logs/google_usage.csv and new requests are refused once the minute cap is reached.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
OPS_FILE = ROOT / "logs" / "google_ops.json"
USAGE_FILE = ROOT / "logs" / "google_usage.csv"
OUT_DIR = ROOT / "transcripts" / "google"
FLAC_DIR = ROOT / "work" / "flac"
DECISIONS_FILE = ROOT / "DECISIONS.md"

# Models that are not offered in the default regional endpoint.
MODEL_LOCATIONS = {"chirp_3": "us"}


class CapExceeded(RuntimeError):
    pass


def _offset_s(v) -> float:
    """'1.500s' (JSON) or {'seconds':..,'nanos':..} or timedelta -> seconds."""
    if v is None:
        return 0.0
    if isinstance(v, str):
        return float(v.rstrip("s") or 0)
    if isinstance(v, dict):
        return float(v.get("seconds", 0)) + float(v.get("nanos", 0)) / 1e9
    if hasattr(v, "total_seconds"):
        return v.total_seconds()
    return float(v)


def parse_results(data: dict) -> tuple[str, list[dict]]:
    """BatchRecognizeResults JSON -> (full transcript, segments with timings)."""
    segs, prev_end = [], 0.0
    for r in data.get("results", []):
        alts = r.get("alternatives") or []
        if not alts or not alts[0].get("transcript", "").strip():
            prev_end = _offset_s(r.get("resultEndOffset", r.get("result_end_offset"))) or prev_end
            continue
        a = alts[0]
        words = [{"word": w.get("word", ""),
                  "start": _offset_s(w.get("startOffset", w.get("start_offset"))),
                  "end": _offset_s(w.get("endOffset", w.get("end_offset")))}
                 for w in a.get("words", [])]
        end = _offset_s(r.get("resultEndOffset", r.get("result_end_offset")))
        start = words[0]["start"] if words else prev_end
        segs.append({"start": start, "end": end, "text": a["transcript"].strip(),
                     "confidence": a.get("confidence"), "language": r.get("languageCode"),
                     "words": words})
        prev_end = end
    return " ".join(s["text"] for s in segs), segs


class GoogleSTT:
    def __init__(self, cfg: dict | None = None, env: dict | None = None,
                 speech_factory=None, storage_client=None, ffprobe=None):
        self.cfg = cfg or yaml.safe_load((ROOT / "config.yaml").read_text())["google"]
        if env is None:
            from dotenv import dotenv_values
            env = {**dotenv_values(ROOT / ".env"), **{k: v for k, v in os.environ.items()
                                                       if k.startswith(("GOOGLE_", "GCP_", "GCS_"))}}
        missing = [k for k in ("GOOGLE_APPLICATION_CREDENTIALS", "GCP_PROJECT_ID", "GCS_BUCKET") if not env.get(k)]
        if missing:
            raise RuntimeError(f"missing env vars: {missing}")
        os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", env["GOOGLE_APPLICATION_CREDENTIALS"])
        self.project = env["GCP_PROJECT_ID"]
        self.bucket_name = env["GCS_BUCKET"]
        self.location = env.get("GOOGLE_STT_LOCATION") or self.cfg.get("location_default", "us-central1")
        self._speech_factory = speech_factory
        self._speech: dict[str, object] = {}
        self._storage = storage_client
        self._ffprobe = ffprobe or self._ffprobe_minutes

    # --- clients ----------------------------------------------------------------
    def location_for(self, model: str) -> str:
        return {**MODEL_LOCATIONS, **(self.cfg.get("model_locations") or {})}.get(model, self.location)

    def endpoint_for(self, location: str) -> str:
        return f"{location}-speech.googleapis.com"

    def speech(self, location: str):
        if location not in self._speech:
            if self._speech_factory:
                self._speech[location] = self._speech_factory(self.endpoint_for(location))
            else:
                from google.api_core.client_options import ClientOptions
                from google.cloud.speech_v2 import SpeechClient
                self._speech[location] = SpeechClient(
                    client_options=ClientOptions(api_endpoint=self.endpoint_for(location)))
        return self._speech[location]

    @property
    def storage(self):
        if self._storage is None:
            from google.cloud import storage
            self._storage = storage.Client(project=self.project)
        return self._storage

    def recognizer(self, model: str) -> str:
        return f"projects/{self.project}/locations/{self.location_for(model)}/recognizers/_"

    # --- usage cap ----------------------------------------------------------------
    @staticmethod
    def _ffprobe_minutes(path: pathlib.Path) -> float:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", str(path)], capture_output=True, text=True, check=True)
        return float(out.stdout.strip()) / 60

    def minutes_used(self) -> float:
        if not USAGE_FILE.exists():
            return 0.0
        with open(USAGE_FILE) as f:
            return sum(float(r["minutes"]) for r in csv.DictReader(f))

    def _log_usage(self, rows: list[dict]) -> None:
        USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        new = not USAGE_FILE.exists()
        with open(USAGE_FILE, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["file", "minutes", "model", "strategy", "timestamp", "operation"])
            if new:
                w.writeheader()
            w.writerows(rows)

    # --- audio prep ---------------------------------------------------------------
    def flac_for(self, src: pathlib.Path) -> pathlib.Path:
        if src.suffix.lower() == ".flac":
            return src
        FLAC_DIR.mkdir(parents=True, exist_ok=True)
        dst = FLAC_DIR / (src.stem + ".flac")
        if not dst.exists():
            subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
                            "-ac", "1", "-ar", "16000", "-c:a", "flac", str(dst)], check=True)
        return dst

    def split_long(self, flac: pathlib.Path) -> list[tuple[pathlib.Path, float]]:
        """BatchRecognize rejects files over 20 min. Cut longer FLACs into <= max_file_minutes
        parts at the quietest 20 ms frame within +-15 s of each boundary.
        Returns [(part_path, offset_seconds)]; a short file is returned as-is."""
        max_s = float(self.cfg.get("max_file_minutes", 19)) * 60
        total = self._ffprobe(flac) * 60
        if total <= max_s:
            return [(flac, 0.0)]
        import numpy as np
        raw = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(flac), "-ac", "1", "-ar", "16000",
                              "-f", "f32le", "-"], capture_output=True, check=True).stdout
        x = np.frombuffer(raw, np.float32)
        fr = 320
        n = len(x) // fr
        rms = np.sqrt((x[: n * fr].reshape(n, fr) ** 2).mean(1))
        n_parts = int(np.ceil(total / max_s))
        cuts = [0.0]
        for k in range(1, n_parts):
            target = k * total / n_parts
            lo, hi = int((target - 15) * 50), int((target + 15) * 50)
            cuts.append((lo + int(np.argmin(rms[lo:hi]))) / 50)
        cuts.append(total)
        FLAC_DIR.mkdir(parents=True, exist_ok=True)
        parts = []
        for k in range(n_parts):
            part = FLAC_DIR / f"{flac.stem}__p{k:02d}.flac"
            if not part.exists():
                subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", f"{cuts[k]:.3f}",
                                "-to", f"{cuts[k + 1]:.3f}", "-i", str(flac), "-c:a", "flac", str(part)], check=True)
            parts.append((part, cuts[k]))
        return parts

    def gcs_uri(self, flac: pathlib.Path) -> str:
        return f"gs://{self.bucket_name}/{self.cfg['input_prefix']}{flac.name}"

    def prepare(self, files: list[pathlib.Path]) -> list[tuple[pathlib.Path, str]]:
        bucket = self.storage.bucket(self.bucket_name)
        out = []
        for f in files:
            flac = self.flac_for(f)
            blob = bucket.blob(f"{self.cfg['input_prefix']}{flac.name}")
            if not blob.exists():
                blob.upload_from_filename(str(flac), timeout=900)
                print("uploaded", flac.name, flush=True)
            out.append((flac, self.gcs_uri(flac)))
        return out

    # --- request ------------------------------------------------------------------
    def phrases(self) -> list[str]:
        p = ROOT / "terms.txt"
        if not p.exists():
            return []
        return [l.strip() for l in p.read_text().splitlines() if l.strip() and not l.startswith("#")]

    def build_config(self, model: str):
        from google.cloud.speech_v2.types import cloud_speech as cs
        boost = self.cfg.get("phrase_boost", 10)
        phrases = [cs.PhraseSet.Phrase(value=t, boost=boost) if boost else cs.PhraseSet.Phrase(value=t)
                   for t in self.phrases()]
        adaptation = None
        if phrases and model not in self.cfg.get("no_adaptation_models", ["chirp_3"]):
            adaptation = cs.SpeechAdaptation(phrase_sets=[
                cs.SpeechAdaptation.AdaptationPhraseSet(inline_phrase_set=cs.PhraseSet(phrases=phrases))])
        return cs.RecognitionConfig(
            auto_decoding_config=cs.AutoDetectDecodingConfig(),
            language_codes=list(self.cfg.get("language_codes", ["ro-RO"])),
            model=model,
            features=cs.RecognitionFeatures(enable_automatic_punctuation=True,
                                            enable_word_time_offsets=True),
            adaptation=adaptation,
        )

    def build_request(self, uris: list[str], model: str, run_id: str, dynamic: bool):
        from google.cloud.speech_v2.types import cloud_speech as cs
        req = cs.BatchRecognizeRequest(
            recognizer=self.recognizer(model),
            config=self.build_config(model),
            files=[cs.BatchRecognizeFileMetadata(uri=u) for u in uris],
            recognition_output_config=cs.RecognitionOutputConfig(
                gcs_output_config=cs.GcsOutputConfig(
                    uri=f"gs://{self.bucket_name}/{self.cfg['output_prefix']}{run_id}/")),
        )
        if dynamic:
            req.processing_strategy = cs.BatchRecognizeRequest.ProcessingStrategy.DYNAMIC_BATCHING
        return req

    def submit(self, files: list[pathlib.Path], model: str | None = None, tag: str = "",
               dry_run: bool = False) -> list[str]:
        model = model or self.cfg["model"]
        ops = load_ops()
        done_or_pending = {(f, o["model"]) for o in ops.values() if o["status"] == "pending"
                           for f in o["files"] + [v[0] for v in o.get("parts", {}).values()]}
        files = [f for f in files
                 if (pathlib.Path(f).stem, model) not in done_or_pending
                 and not (OUT_DIR / model / f"{pathlib.Path(f).stem}.json").exists()]
        if not files:
            print("nothing to submit (all pending or transcribed)")
            return []
        files = list(dict.fromkeys(pathlib.Path(f) for f in files))
        parts: dict[str, tuple[str, float]] = {}
        expanded = []
        for f in files:
            pieces = self.split_long(self.flac_for(f))
            if len(pieces) > 1:
                for p, off in pieces:
                    parts[p.stem] = (f.stem, off)
            expanded += [p for p, _ in pieces]
        files = expanded
        minutes = {f: self._ffprobe(self.flac_for(f)) for f in files}
        used, cap = self.minutes_used(), float(self.cfg["usage_cap_minutes"])
        if used + sum(minutes.values()) > cap:
            raise CapExceeded(f"cap {cap} min: used {used:.1f}, request {sum(minutes.values()):.1f}")
        if dry_run:
            print(f"dry run: {len(files)} files, {sum(minutes.values()):.1f} min, model={model}, "
                  f"recognizer={self.recognizer(model)}")
            return []
        prepared = self.prepare(files)
        names = []
        step = int(self.cfg.get("max_files_per_request", 5))
        for i in range(0, len(prepared), step):
            chunk = prepared[i:i + step]
            run_id = f"{tag or 'run'}-{model}-{dt.datetime.now():%Y%m%d-%H%M%S}-{i // step}"
            op, strategy = self._submit_one([u for _, u in chunk], model, run_id)
            names.append(op.operation.name)
            ops[op.operation.name] = {
                "model": model, "location": self.location_for(model), "strategy": strategy,
                "files": [p.stem for p, _ in chunk], "uris": [u for _, u in chunk],
                "parts": {p.stem: parts[p.stem] for p, _ in chunk if p.stem in parts},
                "output": f"gs://{self.bucket_name}/{self.cfg['output_prefix']}{run_id}/",
                "submitted": dt.datetime.now().isoformat(timespec="seconds"), "status": "pending"}
            save_ops(ops)          # persist immediately
            ts = dt.datetime.now().isoformat(timespec="seconds")
            self._log_usage([{"file": p.name, "minutes": round(minutes[p0], 3), "model": model,
                              "strategy": strategy, "timestamp": ts, "operation": op.operation.name}
                             for (p, _), p0 in zip(chunk, files[i:i + step])])
            print("submitted", op.operation.name, strategy, len(chunk), "files", flush=True)
        return names

    def _submit_one(self, uris, model, run_id):
        from google.api_core import exceptions as gexc
        client = self.speech(self.location_for(model))
        want_dynamic = self.cfg.get("strategy", "DYNAMIC_BATCHING") == "DYNAMIC_BATCHING"
        if want_dynamic:
            try:
                return client.batch_recognize(request=self.build_request(uris, model, run_id, True)), "DYNAMIC_BATCHING"
            except (gexc.InvalidArgument, gexc.FailedPrecondition) as e:
                if not any(k in str(e).lower() for k in ("dynamic", "processing", "strategy")):
                    raise
                note = (f"- {dt.date.today()} — Dynamic Batch rejected for {model} in "
                        f"{self.location_for(model)} ({str(e)[:160]}); fell back to standard batch.\n")
                with open(DECISIONS_FILE, "a") as f:
                    f.write(note)
                print("dynamic batching rejected, falling back:", str(e)[:200])
        return client.batch_recognize(request=self.build_request(uris, model, run_id, False)), "STANDARD"

    # --- polling ------------------------------------------------------------------
    def check(self, name: str, info: dict) -> bool:
        """True when the operation is finished (results downloaded or error recorded)."""
        client = self.speech(info["location"])
        op = client.transport.operations_client.get_operation(name)
        if not op.done:
            return False
        ops = load_ops()
        if op.HasField("error") and op.error.code:
            ops[name]["status"] = f"error {op.error.code}: {op.error.message[:200]}"
            save_ops(ops)
            return True
        errors = {}
        try:
            from google.cloud.speech_v2.types import cloud_speech as cs
            resp = cs.BatchRecognizeResponse.deserialize(op.response.value)
            errors = {pathlib.Path(uri).stem: r.error.message[:300] for uri, r in resp.results.items() if r.error.code}
        except Exception:
            pass
        self.download(info)
        ops[name]["status"] = "done" if not errors else f"done, {len(errors)} file(s) failed"
        if errors:
            ops[name]["file_errors"] = errors
            for f, msg in errors.items():
                print("FILE FAILED", f, msg, flush=True)
        save_ops(ops)
        return True

    def download(self, info: dict) -> None:
        bucket = self.storage.bucket(self.bucket_name)
        prefix = info["output"].split(f"gs://{self.bucket_name}/", 1)[1]
        dest = OUT_DIR / info["model"]
        dest.mkdir(parents=True, exist_ok=True)
        for blob in self.storage.list_blobs(self.bucket_name, prefix=prefix):
            if not blob.name.endswith(".json"):
                continue
            stem = next((f for f in info["files"] if pathlib.Path(blob.name).name.startswith(f)), None)
            if stem is None or (dest / f"{stem}.json").exists():
                continue
            data = json.loads(blob.download_as_bytes())
            text, segs = parse_results(data)
            (dest / f"{stem}.txt").write_text(text + "\n")
            (dest / f"{stem}.json").write_text(json.dumps({"file": stem, "model": info["model"],
                                                           "segments": segs}, ensure_ascii=False, indent=1))
            print("saved", dest / f"{stem}.txt", flush=True)
        self.merge_parts(info, dest)

    @staticmethod
    def merge_parts(info: dict, dest: pathlib.Path) -> None:
        """Join <stem>__pNN transcripts into <stem>.json/.txt, shifting timings by each offset."""
        by_parent: dict[str, list] = {}
        for part, (parent, off) in info.get("parts", {}).items():
            by_parent.setdefault(parent, []).append((off, part))
        all_parts = {}
        for o in load_ops().values():                  # parts may span several requests
            for part, (parent, off) in o.get("parts", {}).items():
                all_parts.setdefault(parent, set()).add((off, part))
        for parent in by_parent:
            items = sorted(all_parts.get(parent, set()))
            if (dest / f"{parent}.json").exists() or not all((dest / f"{p}.json").exists() for _, p in items):
                continue
            segs = []
            for off, p in items:
                for sg in json.loads((dest / f"{p}.json").read_text())["segments"]:
                    sg = {**sg, "start": sg["start"] + off, "end": sg["end"] + off,
                          "words": [{**w, "start": w["start"] + off, "end": w["end"] + off} for w in sg.get("words", [])]}
                    segs.append(sg)
            (dest / f"{parent}.json").write_text(json.dumps({"file": parent, "model": info["model"], "segments": segs,
                                                             "parts": [p for _, p in items]}, ensure_ascii=False, indent=1))
            (dest / f"{parent}.txt").write_text(" ".join(sg["text"] for sg in segs) + "\n")
            print("merged", parent, "from", len(items), "parts", flush=True)

    def poll(self, once: bool = False) -> None:
        delay = float(self.cfg.get("poll_initial_s", 30))
        deadline = time.time() + float(self.cfg.get("poll_timeout_h", 30)) * 3600
        while True:
            pending = {k: v for k, v in load_ops().items() if v["status"] == "pending"}
            for name, info in pending.items():
                try:
                    if self.check(name, info):
                        print("finished", name, load_ops()[name]["status"], flush=True)
                except Exception as e:      # transient: keep polling
                    print("poll error", name, type(e).__name__, str(e)[:200], flush=True)
            if once or not {k for k, v in load_ops().items() if v["status"] == "pending"}:
                return
            if time.time() > deadline:
                print("poll timeout; ops remain pending in", OPS_FILE)
                return
            time.sleep(delay)
            delay = min(delay * 1.5, float(self.cfg.get("poll_max_s", 900)))


def load_ops() -> dict:
    return json.loads(OPS_FILE.read_text()) if OPS_FILE.exists() else {}


def save_ops(ops: dict) -> None:
    OPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = OPS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(ops, indent=1))
    tmp.replace(OPS_FILE)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prepare"); p.add_argument("files", nargs="+")
    s = sub.add_parser("submit"); s.add_argument("files", nargs="+")
    s.add_argument("--model"); s.add_argument("--tag", default=""); s.add_argument("--dry-run", action="store_true")
    s.add_argument("--language", help="BCP-47 code, e.g. en-US (default: config google.language_codes)")
    q = sub.add_parser("poll"); q.add_argument("--once", action="store_true")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    g = GoogleSTT()
    if a.cmd == "prepare":
        g.prepare([pathlib.Path(f) for f in a.files])
    elif a.cmd == "submit":
        if a.language:
            g.cfg["language_codes"] = [a.language]
        try:
            g.submit([pathlib.Path(f) for f in a.files], a.model, a.tag, a.dry_run)
        except CapExceeded as e:
            print("REFUSED:", e)
            return 2
    elif a.cmd == "poll":
        g.poll(a.once)
    else:
        for k, v in load_ops().items():
            print(v["status"], v["model"], v["strategy"], len(v["files"]), "files", k)
        print(f"minutes used: {g.minutes_used():.1f} / {g.cfg['usage_cap_minutes']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
