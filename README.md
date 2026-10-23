# Live sermon translation (RO ⇄ EN), offline

Soundboard audio → speech-to-text → translation → ProPresenter, on one Apple Silicon Mac,
with no cloud services in the live path.

<!-- SUMMARY:START -->
## Summary (iteration 1, updated 2026-10-22 with 3 sermons)

**Status:** working end to end, tested with real sermon audio played as live input into a mock
ProPresenter. **Not yet tested:** a real ProPresenter, and a real English-speaking guest.

**Recommended live setup** (the default in `config.yaml`, "Quality" on the control page):
- **STT:** Jackrabbit 110M Romanian, CTC + 4-gram LM, beam 8. **7.1% WER on human-checked
  sermon audio** (Whisper large-v3: 6.7%, a tie; Jackrabbit is 4× faster and spells theological
  terms better). English speech and RO/EN detection use Parakeet-TDT-0.6B-v3.
- **Translation:** Gemma 4 12B, fed 2.5 s chunks with the previous 4 chunks as context. It
  repairs recognition slips ("bread from the wilderness", where NLLB wrote "bread to the Germans").
- **Delay:** 92% of captions have their first word on screen **within 6 s** of it being spoken
  (median 4.3 s, real sermon audio). Each short chunk is recognised with the previous 6 s of
  audio as context, which halves the errors short chunks would otherwise cause (live WER 7.3%).
- **English speakers (EN→RO):** Parakeet with context, 2.7% WER on a real English sermon,
  99% of first words within 6 s. Whistle is available as an alternative (6.7%).
- **Fast profile** (one click on the control page): NLLB instead of Gemma. 98% within 6 s
  (median 3.5 s), but translations are more literal.
- Dev only: Google Chirp 3 is used for scoring, never for training (Google terms §17b).

Details: [BENCHMARK.md](BENCHMARK.md). Every judgment call: [DECISIONS.md](DECISIONS.md).

**Open questions**
1. **Decided 2026-10-22:** accuracy first, within a 6 s ceiling. Gemma 4 is the default
   ("Quality"); NLLB is the "Fast" fallback.
2. **Done 2026-10-22:** human-checked sermon WER: Jackrabbit (live) 7.1%, Whisper 6.7% (a tie), Google 1.2% (optimistic).
   For a larger check, build a bigger packet (`src/review_packet.py --minutes 30`), then rescore
   with `.venv/bin/python src/review_packet.py rescore review_packet/corrections.json`.
3. **Google terms:** checked. §17(b) forbids training models on Speech-to-Text output, so
   training corrections use the Google-free mode (`src/correct_tool.py --google none`). A written
   OK from Google Cloud Sales would change this.
4. **Licensing:** Jackrabbit and NLLB are CC-BY-NC-4.0, fine for church-internal use.
   Commercial alternatives: SpeD (Apache-2.0) and Gemma.
5. **Real ProPresenter:** only tested against the mock (built from the official OpenAPI spec).
6. **Fine-tuning data is ready to correct:** all 3 sermons are batch-transcribed (1,271
   segments, 99% pre-filled with Google's text) in `src/correct_tool.py`.
<!-- SUMMARY:END -->

## 1. Install

Requirements: an Apple Silicon Mac (tested on an M3 Max with 36 GB RAM), macOS, Homebrew,
and about 40 GB of free disk space.

```bash
./setup.sh          # everything: 5 venvs, models, tests (first run downloads ~35 GB)
./setup.sh live     # only what the live app needs
```

Why five venvs: NeMo (Jackrabbit, SpeD, Canary), MLX-LM (LLM translators) and Moondream pin
conflicting versions of the same libraries. The live app runs as two processes: the ASR engine
in `.venv-nemo` and the orchestrator/web page in `.venv-mt`. All settings live in
`config.yaml`; secrets live only in `.env`.

| venv | used for |
|---|---|
| `.venv` | scoring, Google dev tools, tests |
| `.venv-nemo` | Jackrabbit / SpeD / Canary, live ASR engine, batch transcription, fine-tuning |
| `.venv-mt` | NLLB (CTranslate2), MLX LLMs, live orchestrator + web page, correction tool |
| `.venv-mlx` | Parakeet and Whisper benchmarks (MLX) |
| `.venv-moondream` | Parakeet Redux benchmark |

## 2. Google Cloud setup (dev only, never used live)

Google Speech-to-Text produces reference transcripts for scoring. The live app does not import it.

1. In the Google Cloud console, create a project with billing enabled. Enable the **Cloud
   Speech-to-Text API** and the **Cloud Storage API**.
2. Create a private bucket in `us-central1` (uniform access, public access prevention on).
3. Create a service account (e.g. `sermon-transcriber`) with the role **Cloud Speech Client**
   on the project and **Storage Object Admin** on the bucket only.
4. Create a JSON key for it. Store it outside the repo, e.g.
   `~/.config/gcloud-keys/sermon-stt.json`, with permissions `chmod 600`.
5. `cp .env.example .env` and fill in the values:
   ```
   GOOGLE_APPLICATION_CREDENTIALS=/Users/<you>/.config/gcloud-keys/sermon-stt.json
   GCP_PROJECT_ID=<project id>
   GCS_BUCKET=<bucket name>
   GOOGLE_STT_LOCATION=us-central1
   ```
6. Optional: add a budget alert under Billing → Budgets & alerts, using alerts only
   (Speech-to-Text can't have a spend cap).

Regions (verified, see DECISIONS.md): `chirp_2` batch for ro-RO must run in `europe-west4`
(us-central1 rejects it), and `chirp_3` only in the `us`/`eu` multi-regions. The module routes
these automatically through `google.model_locations` in config.yaml.

```bash
.venv/bin/python src/google_stt.py submit work/bible/excerpts/*.flac --model chirp_3 --tag bible
.venv/bin/python src/google_stt.py poll        # re-attaches to pending ops after a restart
.venv/bin/python src/google_stt.py status      # pending ops + minutes used (cap: 600)
```

## 3. Run live

```bash
.venv-mt/bin/python src/live/app.py
```

Open **http://127.0.0.1:8765**. From there you can pick the audio input, start and stop,
choose the language mode (Auto / RO→EN / EN→RO), watch the live latency, see the last 10
source and translated lines, and clear the screen.

- Language: **Auto** detects RO or EN per clause and switches after 2 clauses agree. Lock it
  for a guest speaker if the auto-switching ever hesitates.
- Each service is logged to `logs/services/<date>_<time>.jsonl` (timestamped source,
  translation and latencies), for future fine-tuning.
- Rehearse with a recording instead of the mixer:
  `.venv-mt/bin/python src/live/app.py --file some_sermon.mp3 --autostart`

## 4. Connect ProPresenter 7

1. In ProPresenter: **Settings → Network → Enable Network**, and note the port.
2. Create a **Message** named `Live Translation` whose text is a single text token named
   `Text` (message text `{Text}`). Give it a theme with a lower-third text box sized for
   2 lines.
3. Set `propresenter.host` / `port` / `message_id` / `token_name` / `max_chars_per_line` /
   `max_lines` in config.yaml. Set `stage_message: true` to mirror the text on the stage display.
4. The app calls `POST /v1/message/{id}/trigger` with the token text on every new clause,
   `GET /v1/message/{id}/clear` for "Clear screen", and `PUT /v1/stage/message` for the stage.
   Unchanged text is not re-sent, and only whole lines scroll, so the captions don't flicker.

Test without ProPresenter: `.venv-mt/bin/python src/live/mock_propresenter.py --port 50001`.

## 4b. English speakers: Whistle (English → Romanian)

English speech is recognised by **Parakeet-TDT-0.6B-v3** by default (most accurate).
**Cactus Whistle** (`cactus-needle`, Apache-2.0, a 17 MB on-device CPU model) is the faster
alternative: `en_recognizer: whistle` in config.yaml, ~0.6 s sooner, somewhat less accurate.
Only English uses either; Romanian keeps Jackrabbit.

**Setup.** `./setup.sh` installs `cactus-needle` into `.venv-nemo` and fetches the engine
(`libneedle.dylib`) and weights (`whistle.cact`) **once** into `models/whistle/`. At runtime the
app pins those files (`NEEDLE3_LIB_PATH`, `NEEDLE_WHISTLE_WEIGHTS`), turns telemetry off
(`NEEDLE_TELEMETRY=0`, `DO_NOT_TRACK=1`), and forces Hugging Face offline mode before any model
loads. **Nothing goes to the network while it runs.** Use `LIVE_ALLOW_NETWORK=1` only when
updating models.

**How it works.** With the language locked to **EN → RO**, the engine streams audio to Whistle in
1 s chunks. Whistle commits words only once two passes agree, and **only committed words go
downstream**, so the Romanian on screen is never rewritten. Committed words are cut into clauses
at punctuation, at pauses taken from the word timestamps (`pause_s`), or at `max_chunk_s`, then
translated. Frames the voice detector doesn't score as speech (music, choir, noise) are sent as
silence, which Whistle returns as no text. In **Auto** mode, Romanian/English detection still
uses Parakeet + Jackrabbit; once a clause is English, Whistle transcribes it.

**Keywords file: `keywords_en.txt`.** One name or phrase per line, `#` for comments. It's loaded
when the app starts (restart after editing). It is seeded with biblical names and theological
terms. **Add your pastors', guest speakers' and ministry names at the bottom.**

**Settings** (`config.yaml`):

```yaml
live:
  en_recognizer: whistle      # A/B switch: whistle | parakeet (the previous English recognizer)
whistle:
  depth: full                 # decoder depth (see below)
  chunk_s: 0.5                # audio per streaming pass
  vad_threshold: 0.6          # below this voice score, audio is sent as silence (music/choir gate)
  pause_s: 0.35               # gap between words that ends a clause
  max_chunk_s: 2.5            # longest clause before it is sent anyway
```

**Depth.** Whistle's decoder can be cut to fewer layers (2–8) for speed, at some cost in
accuracy. In cactus-needle 3.2.8 that choice is fixed in the weights file: the Python API and
engine have no runtime depth switch. `depth: full` uses the shipped `whistle.cact`. To try a
shallower model, build one with `needle whistle build --layers N` (needs the `[train]` extra),
list it under `whistle.weights_by_depth` (e.g. `{4: models/whistle/whistle-d4.cact}`) and set
`depth: 4`.

**Test / A/B on a recording (offline).**

```bash
.venv-mt/bin/python scripts/test_en_sermon.py path/to/english_sermon.wav --recognizer whistle
.venv-mt/bin/python scripts/test_en_sermon.py path/to/english_sermon.wav --recognizer parakeet
scripts/en_ab.sh path/to/english_sermon.wav            # Parakeet vs Whistle, each with Gemma and NLLB
```

Each run plays the WAV in real time through the full pipeline (VAD → English STT → clauses →
translation → mock ProPresenter) **with outbound network blocked** by macOS `sandbox-exec`, and
writes `results/en_test/<name>/`:
- `transcript_en.txt` (timestamped English)
- `output_ro.txt` (the Romanian sent to the screen)
- `latency.csv` (per segment: audio end → sent to ProPresenter, plus first-word delay and stage
  times)
- `summary.json`, including `network_attempts` (must be 0) and English WER if `<wav>.txt` exists

## 5. Tests and benchmarks

```bash
.venv/bin/python -m pytest -q tests                      # normalisation, WER, segmentation, ProPresenter, Google (mocked)
.venv/bin/python src/score_bible.py                      # Phase 1 Bible scores -> results/bible_scores.csv
.venv-mt/bin/python src/mt_bench.py score                # Phase 2 -> results/mt_scores.csv
.venv-mt/bin/python src/live/e2e.py --audio <file>       # end-to-end latency through the mock ProPresenter
.venv-mt/bin/python src/live/e2e.py --mixed              # RO + English + RO, auto language detection
```

## 6. Next iteration

1. Put sermon recordings in `./Audio`, then run `scripts/run_sermon_pipeline.sh`
   (add `GOOGLE=chirp_3` to include Google references). This builds the sermon set, runs all
   models, writes consensus/term/diacritic scores, and creates `review_packet/`.
2. Have a Romanian speaker correct `review_packet/index.html`, then run
   `.venv/bin/python src/review_packet.py rescore review_packet/corrections.json`.
3. Re-check the model choice on the sermon numbers and update `live.stt_model` / `live.translator`.
4. Fine-tuning: see `src/finetune/README.md` (built, not run).

Project state for resuming work: `PROGRESS.md`. Every judgment call: `DECISIONS.md`.
Full results: `BENCHMARK.md`.
