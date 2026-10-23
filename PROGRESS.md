# Progress

Resume: open this folder in Claude Code and say "resume from PROGRESS.md".
Venvs (uv, Python 3.12): `.venv` (core/scoring/Google), `.venv-nemo` (Jackrabbit/SpeD/Canary, live engine),
`.venv-mlx` (Parakeet/Whisper), `.venv-moondream` (Parakeet Redux), `.venv-mt` (NLLB, MLX LLMs, live orchestrator).

## Iteration 1 — DONE (2026-10-21, 22:05)
- Phase 0: src/google_stt.py (+ mocked tests). Bible set transcribed by chirp_2 (europe-west4) and chirp_3 (us). 68.1/600 Google minutes used. Chirp 3 = silver reference.
- Phase 1: 15 local configurations + Google scored on the 29.6-min Bible set → results/bible_scores.csv, stt_latency.csv. terms.txt (361).
- Phase 2: NLLB 1.3B/3.3B, Gemma 4 12B, EuroLLM-22B, Canary AST → results/mt_scores.csv, mt_latency.csv, mt_side_by_side.md.
- Phase 3: live app (src/live/), mock ProPresenter, web control page, service logs; e2e latency → results/e2e_latency.csv.
- Phase 4: batch transcription, correction tool, fine-tune scripts + README (not run).
- BENCHMARK.md, README.md (summary + open questions), DECISIONS.md, setup.sh, requirements/*.txt. Tests: 28 pass.

## Iteration 1b — sermons DONE (2026-10-22 00:30)
- 3 sermons in ./Audio (86 min). Sermon set work/sermon (24.7 min, 87 segments); 12 local models run; Chirp 3 on full sermons (split ≤19 min, merged); scores in results/sermon_noref_scores.csv; sermon MT (results/mt_sermon.csv); sermon live e2e (results/e2e_latency.csv rows sermon_*); review_packet/ built; archive batch-transcribed (transcripts/local/jackrabbit-ctc-lm-b8/archive). Google 241.2/600 min.

## Done 2026-10-22 (morning)
- Human corrections (review_packet/corrections.json) scored: Jackrabbit b8 7.13%, Whisper 6.74%, Google 1.21% WER.
- 6-second first-word ceiling (user). Quality profile tuned and made default: Jackrabbit b8 + Gemma 4, 2.5 s chunks, 250 ms pause, 4 chunks of context, so 92% of chunks are within 6 s (results/e2e_latency_v3.csv, v4_gemma_c2.5).
- Google terms §17(b): no training on Google output, even edited. correct_tool.py has a test-set mode (Google) and a clean training mode (`--google none`, Whisper highlights, data/finetune/corrections_clean/).

## Done 2026-10-22 08:30
- Whisper second-opinion transcripts of the archive (transcripts/local/whisper-large-v3-mlx/archive) → clean correction mode works.
- Fast profile retuned: NLLB, 2.5 s chunks, so 98% within 6 s. BENCHMARK.md (Phase 3b + Google terms), README summary, DECISIONS updated.

## Waiting on the user
- Send the Google terms question (draft in conversation); correct sermons in clean mode for training data.
- Rehearsal on the church Mac with the soundboard and the real ProPresenter.
- More sermons: drop into ./Audio and rerun `GOOGLE=chirp_3 scripts/run_sermon_pipeline.sh`.
- Human corrections of review_packet → `src/review_packet.py rescore review_packet/corrections.json`.

## Next iteration ideas
0. **Requirement (user, 2026-10-23): package as a Mac app eventually.** See the packaging notes in the conversation / DECISIONS.md.
1. Re-pick STT/translator on sermon numbers; consider Gemma 4 if ~2.3 s is acceptable.
2. Merge Bible text into the general Jackrabbit LM (needs KenLM lmplz build).
3. Reduce latency: NLLB beam 1, shorter hold for fragments, translate while the next clause records.
4. Test against the real ProPresenter on the church machine.

## Background jobs
- None running. Google ops all done (`.venv/bin/python src/google_stt.py status`).
