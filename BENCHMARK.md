# Benchmark — iteration 1 (2026-10-21)

Machine: Apple M3 Max, 36 GB. All timings were taken on an otherwise idle machine
(`scripts/quiet_measurements.sh`).

> **Read this first.**
> - All accuracy numbers below come from **clean read speech**: a low-bitrate Romanian audio
>   Bible (Cornilescu), 29.6 min across 8 books. Preaching is harder (spontaneous speech, room
>   acoustics, emotion, sermon vocabulary), so expect higher error rates on sermons.
> - **Sermon numbers (added 2026-10-22)** come from 3 sermons (86 min; 24.7 min evaluation set).
>   They are measured against Google Chirp 3, not a human transcript, so they stay
>   **provisional until a Romanian speaker corrects `review_packet/`**.
> - Translations were scored against the KJV. Every system has partly **memorised Bible verses**:
>   when STT misheard *jugul* as *sucul* in Matt 11:29, all four translators still wrote "yoke".
>   So Bible chrF++ overstates sermon quality, especially for the LLMs.

## Recommendation

| Role | Choice | Why |
|---|---|---|
| Live STT (RO) | **Jackrabbit 110M, CTC + 4-gram LM, beam 8** (`jackrabbit-ctc-lm-b8`) | Best local Bible WER family (4.96%, within 0.2 pt of beam 32), best term accuracy (95%), 0.2 s per 6 s clause with the encoder on the Apple GPU |
| Live STT (EN) + language detection | **Parakeet-TDT-0.6B-v3** (MLX) | Multilingual, 0.14 s per 6 s clause; used for English clauses and for the RO/EN decision |
| Live translation (default "quality" profile) | **Gemma 4 12B** (MLX 4-bit), 2.5 s chunks, 4 chunks of context | Repairs STT slips, 100% glossary adherence on sermons; **92% of first words on screen within the 6 s ceiling** (Phase 3b) |
| "Fast" profile | NLLB-200-distilled-1.3B (CTranslate2 int8), 2.5 s chunks | 98% within 6 s and lower delay, but translates STT errors literally ("bread to the Germans") |
| Not live (yet) | EuroLLM-22B | Best translations (chrF++ 66) but 3.2 s median end to end on this Mac |
| Silver reference (dev) | **Google Chirp 3** | Only system better than the best local model on the Bible (3.78% vs 4.78% WER) |

Jackrabbit is CC-BY-NC-4.0: fine for internal church use, not for commercial redistribution.
The Apache-2.0 fallback is SpeD 110M (9.0% WER).

## Phase 1 — Romanian STT (Bible test set)

> 2026-10-22: "Iisus" (older/Orthodox spelling, used by Whisper and Google) is now scored as
> the same word as "Isus", so only systems that write "Iisus" changed (by 0.2–0.4 pt). Term
> accuracy still requires the modern "Isus". The "vs Google silver" column predates this change.

WER/CER use a phonetic key that neutralises Cyrillic-transliteration and spelling variants
(â/î, ea/ia, …) but keeps ă/ș/ț. `WER-nodiac` also folds diacritics. Term accuracy: occurrences
of the 361 terms.txt terms that are spelled exactly (diacritics included); "near" means the
right word with wrong diacritics or a close misspelling. RTF and latency are on the path the
live engine uses; "clause 6 s" is the STT time for a 6 s clause, and "stream latency" adds the
0.35 s pause wait.

| Model | WER % | WER-nodiac % | CER % | Term acc % (near) | vs Google silver WER % | RTF | Clause 6 s (p90) | Sim. stream latency | License |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Google Chirp 3 (cloud, dev only) | **3.78** | 3.65 | 1.76 | 86.8 (13.2) | — | — | — | — | Google ToS |
| Jackrabbit CTC + LM (beam 32) | 4.78 | 4.71 | 1.95 | 95.1 (3.9) | 4.23 | 0.088 | 0.51 (0.56) | 0.91 s | CC-BY-NC-4.0 |
| Jackrabbit CTC + LM + phrase boost | 4.86 | 4.76 | 1.96 | **96.1** (3.4) | 4.26 | 0.129 | 0.78 (0.86) | 1.21 s | CC-BY-NC-4.0 |
| **Jackrabbit CTC + LM, beam 8 (live)** | 4.96 | 4.89 | 2.01 | 95.1 (3.9) | 4.33 | 0.035 | 0.20 (0.21) | **0.56 s** | CC-BY-NC-4.0 |
| Jackrabbit CTC + Bible LM (held-out) | 5.01 | 4.84 | 2.07 | 93.7 (5.4) | 4.85 | — | — | — | CC-BY-NC-4.0 |
| Google Chirp 2 + phrase set (dev only) | 5.16 | 4.96 | 2.62 | 87.8 (11.7) | 3.83 | — | — | — | Google ToS |
| Jackrabbit TDT greedy + phrase boost | 7.25 | 6.82 | 2.70 | 92.2 (5.4) | 6.30 | 0.006 | 0.04 (0.04) | 0.39 s | CC-BY-NC-4.0 |
| Jackrabbit TDT greedy | 7.38 | 6.93 | 2.68 | 90.7 (5.9) | 6.47 | 0.006 | 0.03 (0.04) | 0.39 s | CC-BY-NC-4.0 |
| Jackrabbit CTC greedy | 8.23 | 8.08 | 3.05 | 91.2 (7.3) | 7.47 | — | — | — | CC-BY-NC-4.0 |
| Whisper large-v3 (MLX) | 7.91 | 7.50 | 2.58 | 84.4 (15.1) | 7.19 | 0.086 | 0.85 (0.93) | 1.28 s | MIT |
| Jackrabbit 110M **streaming** (TDT, offline use) | 9.09 | 8.84 | 3.39 | 89.8 (6.3) | 8.54 | 0.007 | 0.03 (0.03) | 0.38 s | CC-BY-NC-4.0 |
| SpeD 110M TDT | 8.81 | 7.88 | 3.32 | 80.5 (13.7) | 8.26 | 0.006 | 0.03 (0.03) | 0.38 s | Apache-2.0 |
| Canary-1B-v2 (ASR) | 10.85 | 10.30 | 3.87 | 83.4 (10.7) | 9.88 | 0.229 | 2.37 (2.46) | 2.81 s | CC-BY-4.0 |
| SpeD 110M CTC | 11.18 | 10.25 | 3.99 | 75.1 (17.6) | 10.50 | — | — | — | Apache-2.0 |
| Parakeet-TDT-0.6B-v3 (MLX) | 14.56 | 13.93 | 4.57 | 73.2 (20.0) | 13.36 | 0.015 | 0.14 (0.14) | 0.49 s | CC-BY-4.0 |
| Parakeet Redux (1.58-bit) | 15.26 | 14.53 | 4.93 | 75.1 (16.1) | 14.14 | 0.019 | 0.07 (0.07) | 0.42 s | CC-BY-4.0 |
| whisper-large-v3-turbo_ro (TransferRapid) | 31.88 | 31.25 | 23.15 | 55.1 (13.7) | 32.23 | 0.072 | 0.82 (0.89) | 1.24 s | CC-BY-NC-4.0 |

Notes
- **Same reference words for every model** (2026-10-23): each excerpt's place in the book is
  the median of all models' alignments, and every model is scored on exactly those 3,971 words.
  Before, each model was scored only on the words it reached, so missing words at the excerpt
  edges were free. Rankings are unchanged; whisper-large-v3-turbo_ro moved most (31.44 → 31.88).
- **Known baseline**: Jackrabbit TDT greedy was quoted at ~2.8% on Genesis 1–3. Our Genesis
  excerpt (first 4 min) gives 3.9%. The difference is mostly segment-edge hallucinations ("…a
  fost 301.") at our ≤20 s silence cuts, which are counted as insertions.
- Google's term accuracy is low because it writes *Iisus* (Orthodox spelling) and drops
  diacritics on some names. Its plain words are very accurate.
- whisper-large-v3-turbo_ro stops early after pauses (about 23% of words missing). That is model
  behaviour on this audio, not a pipeline fault (checked). Whisper large-v3 writes cedilla ş/ţ
  (25 characters on the set). Every NeMo model writes the correct comma-below ș/ț.
- The Bible LM (held out: every test chapter ±1 excluded) is worse than the general LM. Pure
  Bible text is too narrow. Merging Bible text into the general LM (KenLM) is the next step.
- **Consensus check** (`results/bible_noref_scores.csv`): ranking models by WER against Google
  Chirp 3 gives nearly the same order as true WER. That supports the silver/consensus method for
  sermons. Consensus favours families with many variants, so the sermon pipeline lets only one
  variant per family vote.
- Canary uses the NeMo file API on CPU. Its AED decoder was not ported to the GPU-encoder path.
- Diacritic correctness (share of words needing ă/â/î/ș/ț that are spelled correctly) is
  94–97% for every model. It doesn't separate them; term accuracy does.

## Phase 1c — Sermons, human-checked (review packet, 2026-10-22)

A Romanian speaker corrected the 98 highlighted disagreements in `review_packet/` (5 clips,
12.3 min, 1,824 words). They changed 16 spots relative to Google. Everything was rescored against
the corrected text (`results/review_scores.csv`):

| Model | WER % | CER % |
|---|---:|---:|
| Google Chirp 3 (cloud, dev only) | 1.21† | 0.53 |
| Whisper large-v3 (MLX) | **6.74** | **2.21** |
| **Jackrabbit CTC + LM, beam 8 (live)** | 7.07 | 2.63 |
| Jackrabbit CTC + LM (beam 32) | 7.24 | 2.68 |
| Jackrabbit CTC + LM + phrase boost | 7.35 | 2.74 |
| Jackrabbit TDT | 8.06 | 2.75 |
| Jackrabbit CTC + Bible LM | 9.27 | 3.63 |
| Canary-1B-v2 | 10.75 | 3.49 |
| SpeD 110M TDT | 10.86 | 3.71 |
| Jackrabbit streaming | 11.18 | 4.26 |
| Parakeet Redux | 13.32 | 4.21 |
| Parakeet-TDT-0.6B-v3 | 18.97 | 6.29 |

† Optimistic for Google. The reviewer started from Google's text and checked only the spots
where Google and Jackrabbit disagreed. A word both systems got wrong the same way stays wrong in
the "corrected" text and counts as correct for both.

- **This confirms the silver ranking.** Whisper and Jackrabbit are tied on sermons: the 0.4-point
  gap is about 6 words in 1,824. Jackrabbit stays the live model: it is 4× faster per clause and
  spells theological terms better (91 vs 84% on sermons).
- Jackrabbit's most frequent errors are small Romanian contractions and function words:
  *s-a / să*, *l-a / îl*, *a / au*, a dropped or added *de*/*în*. They rarely change the
  translation. Content-word errors ("demodată" → "de odată") are the ones that matter, and they
  are what fine-tuning on corrected sermons should fix.
- The phrase boost and the beam-32 search give no gain on sermons. That supports beam 8 for live use.

## Phase 1b — Sermons (3 recordings, 24.7 min evaluation set)

Ten 2.5-min excerpts (at 20/50/80% of each sermon), cut at pauses into 87 segments. Google
Chirp 3 transcribed the full sermons (split into ≤19-min parts, see DECISIONS.md), and its
words were cut to each excerpt by timestamp. "vs Google" is WER/CER against that silver
reference: it includes Google's own errors (4.1% on the Bible), so it measures disagreement,
not true error. Consensus: leave-one-out ROVER of one model per family (Jackrabbit CTC+LM,
Jackrabbit streaming, SpeD, Canary, Whisper, Parakeet). Models in the same family as a voter
score optimistically on it.

| Model | vs Google WER % | vs Google CER % | Consensus WER % | Term hit % (near) | Diacritics % |
|---|---:|---:|---:|---:|---:|
| Whisper large-v3 (MLX) | **7.86** | 3.02 | 7.56 | 83.7 (14.2) | 96.3 |
| Jackrabbit CTC + LM (beam 32) | 7.94 | 3.18 | 5.62 | 91.0 (9.0) | 96.1 |
| **Jackrabbit CTC + LM, beam 8 (live)** | 7.97 | 3.17 | 2.83* | **91.2** (8.8) | 96.3 |
| Jackrabbit CTC + LM + phrase boost | 8.07 | 3.23 | 3.05* | 91.2 (8.8) | 96.2 |
| Jackrabbit TDT + phrase boost | 8.61 | 3.10 | 4.93* | 89.2 (9.6) | 96.1 |
| Jackrabbit TDT | 8.72 | 3.09 | 4.93* | 88.3 (10.0) | 96.2 |
| Jackrabbit CTC + Bible LM | 9.52 | 4.02 | 5.99* | 90.0 (9.6) | 96.4 |
| SpeD 110M TDT | 10.80 | 4.16 | 9.19 | 80.2 (15.3) | 96.9 |
| Jackrabbit streaming | 11.60 | 4.48 | 8.03 | 84.4 (11.4) | 96.1 |
| Canary-1B-v2 | 11.95 | 4.15 | 8.44 | 81.4 (11.8) | 95.7 |
| Parakeet Redux | 14.57 | 5.04 | 10.81* | 81.7 (11.7) | 95.9 |
| Parakeet-TDT-0.6B-v3 | 19.44 | 6.82 | 16.89 | 71.8 (19.5) | 94.6 |
| Google Chirp 3 (reference) | — | — | 7.73 | 87.1 (12.9) | 96.2 |

\* not a voter, but a sibling votes, so the number is optimistic.

(With "Iisus" = "Isus" scoring, 2026-10-22, the vs-Google numbers drop by up to 0.2 pt for
models that write "Isus" (live model 7.97 → 7.75%). Current values: `results/sermon_noref_scores.csv`.
Ranking unchanged.)

- Sermons are about twice as hard as the Bible audio for every model (Jackrabbit 5.0 → ~8% vs
  Google). The ranking at the top changes: **Whisper large-v3 ties Jackrabbit on raw words** in
  ≤20 s segments (`condition_on_previous_text=False`). The earlier bad Whisper experience was
  probably long-form decoding. Jackrabbit still spells terms better (91 vs 84%), is 4× faster
  per clause, and writes comma-below ș/ț. **Recommendation unchanged.**
- Term hit rate drops from 95% (Bible) to 91% on sermons.
- The held-out Bible LM is worse on sermons too (9.5%). Sermon speech isn't Bible text.
- Whisper wrote no cedilla characters on the sermons (it did on the Bible).
- Side by side: `results/sermon_side_by_side.md` (10 passages, top 4 models + Google) and
  `results/bible_side_by_side.md`. Human check: `review_packet/` (5 clips, 12.3 min, 98
  highlighted disagreements between Google and the live model).

## Phase 2 — Translation

Input: 177 Bible verses from the test excerpts. "Clean" uses the Cornilescu text; "STT" uses
the Jackrabbit CTC+LM transcript aligned to each verse; EN→RO uses 40 KJV verses against
Cornilescu. Metric: chrF++ (sacrebleu, word_order=2). Glossary: share of glossary terms present
in the source that were rendered as glossary.csv prefers.

| System | chrF++ clean | chrF++ STT | chrF++ STT (doc) | EN→RO chrF++ | Glossary RO→EN / EN→RO | Latency per verse, median (p90), quiet | License |
|---|---:|---:|---:|---:|---:|---:|---|
| EuroLLM-22B-Instruct (MLX 4-bit) | **66.4** | **65.6** | **70.3** | **51.8** | 93.8 / 76.7 | 2.22 (2.88) s | Apache-2.0 |
| Gemma 4 12B-it QAT (MLX 4-bit) | 54.0 | 53.3 | 61.1 | 37.2 | **98.2** / **83.3** | 1.65 (2.33) s | Gemma terms |
| NLLB-200 3.3B (CT2 int8) | 50.9 | 50.5 | 57.8 | 47.3 | 87.6 / 70.0 | 1.94 (3.17) s | CC-BY-NC-4.0 |
| **NLLB-200 distilled 1.3B (CT2 int8)** | 49.8 | 49.5 | 57.1 | 47.5 | 86.7 / 70.0 | **1.14 (1.55) s** | CC-BY-NC-4.0 |
| Canary-1B-v2 direct speech→EN | — | — | 48.2 | — | — | 2.4 s / 6 s clause | CC-BY-4.0 |

(Verses average 19 words. Live clauses are shorter, so live translation time is lower: NLLB's
median in the live runs was 0.70 s.)

- STT errors barely move the scores (clean → STT drops 0.3–0.8 points). Partly that's genuine
  robustness, partly verse memorisation (see the note at the top).
- The LLMs follow the "KJV/NKJV style" prompt ("ye shall find rest unto your souls"), which
  KJV-referenced chrF++ rewards. NLLB writes modern English. For sermon English that is not
  worse, just a different register. Judge by `results/mt_side_by_side.md`, not the number alone.
- **Sermons** (60 clauses from the live STT transcript, `results/mt_sermon.csv`, samples in
  `results/mt_sermon_side_by_side.md`). Back-translation = RO→EN→RO with the same system,
  chrF++ against the source:

  | System | Back-translation chrF++ | Glossary % (42 terms) | Latency per clause, median (p90) |
  |---|---:|---:|---:|
  | EuroLLM-22B | **62.1** | 100 | 1.62 (2.52) s |
  | Gemma 4 12B | 59.3 | 100 | 1.17 (2.06) s |
  | NLLB 1.3B | 57.4 | 88.1 | **0.76 (1.53) s** |
  | NLLB 3.3B | 57.3 | 95.2 | 1.46 (2.80) s |

  **The LLMs repair STT errors and NLLB doesn't.** STT "pâinea din *germana*" (for *pustie*):
  NLLB → "gave bread **to the Germans**"; Gemma → "bread **from the wilderness**". STT "bogat sau
  *sărat*" (for *sărac*): NLLB → "rich **or salty**"; Gemma and EuroLLM → "rich or poor". On a
  church screen that difference matters more than the 0.3–0.9 s gap. See the open question in
  README.md.
- Canary direct speech translation is clearly weaker (48 vs 57 at document level for
  Jackrabbit→NLLB) and slow on CPU. It isn't recommended.

**Winner for live: NLLB-200-distilled-1.3B.** It is the only system that keeps end-to-end latency
inside 1.5–2 s on this Mac. Gemma 4 is one config line away (`live.translator`) when ~0.7 s more
delay is acceptable. EuroLLM would be the quality pick on faster hardware or with sentence-level
caching.

## Phase 3 — Live pipeline, end to end

Bible audio played in real time as live input → engine (Silero VAD, pause commits,
`jackrabbit-ctc-lm-b8`, Parakeet for language detection) → orchestrator (translation, glossary,
rolling captions) → **mock ProPresenter** over HTTP. Latency = caption accepted by ProPresenter
minus the end of the last voiced frame of the clause.

| Input | Mode | Translator | Clauses | Median | p90 | ≤ 2 s | Components (median): pause / STT / MT | Language sequence |
|---|---|---|---:|---:|---:|---:|---|---|
| Ephesians 2 (3 min) | RO→EN | **NLLB 1.3B** | 38 | **1.57 s** | **2.23 s** | **79%** | 0.35 / 0.22 / 0.70 s | all RO |
| RO + English TTS + RO (2.4 min) | Auto | **NLLB 1.3B** | 33 | 1.66 s | 3.11 s | 73% | 0.35 / 0.38 / 0.63 s | `r…r eeeee r…r` ✓ |
| Ephesians 2 | RO→EN | Gemma 4 12B | 38 | 2.25 s | 2.71 s | 40% | 0.35 / 0.19 / 1.34 s | all RO |
| RO + EN + RO | Auto | Gemma 4 12B | 33 | 2.51 s | 4.55 s | 24% | 0.35 / 0.43 / 1.28 s | ✓ |
| Ephesians 2 | RO→EN | EuroLLM-22B | 37 | 3.23 s | 4.65 s | 3% | 0.35 / 0.20 / 2.18 s | all RO |
| RO + EN + RO | Auto | EuroLLM-22B | 31 | 3.51 s | 5.79 s | 3% | 0.35 / 0.45 / 2.21 s | ✓ |

**Real sermons as live input** (5 min: br-recalo + nelu-suciu excerpts, Auto mode):

| Translator | Clauses | Median | p90 | ≤ 2 s | STT / MT (median) | False language switches |
|---|---:|---:|---:|---:|---|---|
| **NLLB 1.3B** | 70 | **1.54 s** | 2.72 s | 67% | 0.37 / 0.61 s | 0 |
| Gemma 4 12B | 71 | 2.48 s | 3.79 s | 37% | 0.38 / 1.18 s | 0 |

The median sits above the sum of the parts because fragments under 4 words wait (up to 1.2 s)
to merge with the next clause, and clauses queue behind a translation still in progress.
ProPresenter calls took about 1 ms (mock). Auto mode adds about 0.15 s (Parakeet runs too). The
English section was detected immediately, and the switch back to Romanian took one clause.
Raw logs: `logs/services/*.jsonl`. Summaries: `results/e2e_*.json`.

## Phase 3b — The 6-second ceiling (2026-10-22)

Requirement from the church: **the first word of each caption must be on screen within 6 s of
being spoken**. Beyond that, bilingual listeners stop following the sermon. "Latency" above
was measured from the *end* of each chunk, which hides how long its first word waited. All runs
below use the same 5 min of real sermon (Auto mode) and measure from the first voiced frame
(v3+).

| Setup | Words per chunk | First word: median | p90 | worst | ≤ 6 s |
|---|---:|---:|---:|---:|---:|
| Original fast profile (NLLB, pause commits, ≤7 s phrases) | 10.4 | 4.8 s | 8.9 s | 11.3 s | 66% |
| Whole-sentence commits (4–12 s) + Gemma | 17.2 | 10.1 s | 13.2 s | 17.3 s | 0% |
| Whole-sentence commits + EuroLLM | 17.2 | 12.0 s | 15.3 s | 18.6 s | 0% |
| Gemma, chunks ≤ 4.5 s | 8.5 | 5.7 s | 7.2 s | 9.6 s | 53% |
| EuroLLM, chunks ≤ 3.5 s | 7.4 | 6.6 s | 10.4 s | 12.1 s | 36% |
| Gemma, chunks ≤ 3.5 s | 7.4 | 5.1 s | 6.7 s | 8.9 s | 79% |
| Gemma, chunks ≤ 3.0 s, 250 ms pause | 6.3 | 4.4 s | 5.9 s | 7.9 s | 92% |
| **Gemma, chunks ≤ 2.5 s, 250 ms pause (quality profile, default)** | 6.0 | **4.2 s** | **5.9 s** | **6.8 s** | **92%** |
| NLLB, chunks ≤ 3.0 s, 250 ms pause | 6.4 | 3.7 s | 5.1 s | 6.9 s | 95% |
| **NLLB, chunks ≤ 2.5 s, 250 ms pause (fast profile)** | 6.0 | **3.5 s** | **5.2 s** | **6.2 s** | **98%** |

- **Chunk length is what controls the delay, not the translator.** The first word waits for
  the whole chunk, plus the pause, STT and translation. Whole-sentence translation is
  incompatible with a 6 s ceiling.
- Short chunks don't ruin the translation, because Gemma gets the previous 4 chunks as
  context. It still repairs STT slips ("moi, se du-te și vorbește" → "Go, and speak"; NLLB:
  "soft, go and talk"). The cost is occasional sentence breaks mid-thought.
- EuroLLM-22B is too slow for this ceiling on an M3 Max.
- Jackrabbit beam 8 is used in both profiles. The heavier decoders gave no gain on sermons
  (Phase 1c).

## Phase 3c — English → Romanian with Cactus Whistle (2026-10-22)

Test input: a 1 min 54 s English sermon written for the test (Habakkuk 2, with Nebuchadnezzar,
Melchizedek, Gethsemane, Golgotha, propitiation, …) and voiced with macOS `say`. The script is
known exactly, so English WER is exact. **It is synthetic speech.** Re-run `scripts/en_ab.sh` on
a real guest-speaker recording before trusting the accuracy column. Every run used the full
pipeline, EN→RO locked, under `sandbox-exec` with outbound network denied:
**0 non-local network attempts in all runs** (the only connections were to the local mock
ProPresenter).

| English STT | Translator | English WER | Latency (audio end → ProPresenter), median / p90 | ≤ 2 s | First word ≤ 6 s |
|---|---|---:|---:|---:|---:|
| Parakeet (previous) | Gemma 4 | **1.2%** | 2.89 / 3.74 s | 5% | 73% |
| Whistle, 1.0 s chunks | Gemma 4 | 5.3% | 3.48 / 6.33 s | 0% | 64% |
| **Whistle, 0.5 s chunks** | **Gemma 4 (quality, default)** | 3.9% | **2.21 / 3.50 s** | 38% | **98%** |
| Parakeet (previous) | NLLB 1.3B | 1.2% | 1.96 / 3.02 s | 51% | 89% |
| **Whistle, 0.5 s chunks** | **NLLB 1.3B (fast)** | 3.9% | **1.37 / 2.46 s** | **79%** | **98%** |

- **Whistle is faster, Parakeet is more accurate (on this clean, synthetic voice).**
  Whistle's commit-on-agreement streaming gets text out about 0.6–0.7 s sooner, and with NLLB it
  is the only setup inside the 1.5–2 s target. About a third of Whistle's "errors" are one
  harmless split ("watchtower" → "watch tower", 3×). The real ones were "Paul" → "hall",
  "prayed" → "craig/grade", "Golgotha" → "galgothur".
- **Keyword biasing works:** Whistle got Habakkuk, Nebuchadnezzar and Melchizedek right
  (Parakeet wrote "Habakuk"). Both missed Golgotha.
- **0.5 s chunks beat 1.0 s** on both delay and accuracy (more passes, so earlier agreement).
  Now the default.
- **Translator for EN→RO:** NLLB is faster but sometimes drops Romanian diacritics ("si
  profetul nu putea intelege") and invents text on fragments ("it." → "- Nu, nu, nu."). Gemma
  writes correct Romanian in Cornilescu style ("proorocul", "Nebucadnezar"). Same choice as
  for RO→EN: **Quality = Gemma (default), Fast = NLLB.**
- Short committed fragments ("it.", "his faith.") sometimes go out alone when merging them
  would break the length cap. That is the cost of committing early.
- Outputs per run: `results/en_test/<run>/` (transcript_en.txt, output_ro.txt, latency.csv,
  summary.json).

## Phase 3d — Real English sermon, and the short-chunk accuracy fix (2026-10-22)

**English A/B on a real sermon** (`Audio/EN_Alex_T.ogg`, 10-min excerpt from 4:06; reference =
Google Chirp 3 en-US, dev-only; full pipeline, EN→RO locked, network blocked, 0 attempts):

| English STT | Translator | WER vs Google | Latency median / p90 | ≤ 2 s | First word median | ≤ 6 s |
|---|---|---:|---:|---:|---:|---:|
| Whistle (0.5 s stream) | Gemma 4 | 6.7% | 1.99 / 3.07 s | 51% | 4.2 s | 100% |
| Parakeet, 2.5 s chunks, no context | Gemma 4 | 12.4% | 2.15 / 3.15 s | 44% | 3.8 s | 98% |
| **Parakeet + 6 s context (default)** | **Gemma 4** | **2.7%** | 2.26 / 3.04 s | 39% | **3.8 s** | **99%** |
| Whistle | NLLB | 6.2% | 1.29 / 2.39 s | 75% | 3.4 s | 100% |
| Parakeet, no context | NLLB | 19.3% | 1.31 / 2.18 s | 85% | 3.1 s | 99% |

- On real speech, Whistle's speed advantage disappears (Parakeet's first word is sooner).
  Once Parakeet gets context it is far more accurate (2.7% vs 6.7%). **Parakeet is the
  English default.** Whistle stays as an option.

**The short-chunk problem (affected Romanian too).** The 2.5 s chunks introduced for the 6 s
ceiling (Phase 3b) roughly doubled the error rate, because each chunk was recognised without
context. Measured against Google:

| | Long segments (≤ 20 s) | 2.5 s chunks alone | **2.5 s chunks + 6 s context** |
|---|---:|---:|---:|
| Romanian, Jackrabbit (5 min, live pipeline) | 5.7% | 12.5% | **7.3%** |
| English, Parakeet (10 min, live pipeline) | 2.7% | 12.4% | **2.7%** |

Fix (`src/live/context_stt.py`, `live.context_s: 6.0`): each chunk is recognised together
with the previous 6 s of audio, and only the words after the already-committed text are
kept. In Auto mode, Jackrabbit and Parakeet now run in parallel, so the extra work fits:
**Romanian first word ≤ 6 s: 92% (p90 5.9 s), WER 7.3%.** (4 s of context: 7.9% WER, 90%.)

## Google data use (terms checked 2026-10-22, not legal advice)

Google Cloud Service Specific Terms §17 ("Use Restrictions for AI/ML Services"; Speech-to-Text
is an AI/ML Service) forbids using its output "to … create or improve models similar to a
Google Model". So Google transcripts are used here **only as a scoring reference**, never as
training text, even after human edits. Training corrections are made in the Google-free mode
of `src/correct_tool.py` (`--google none`): the text starts from Jackrabbit, and the
highlights show where Whisper large-v3 disagrees.

## Phase 4 — Fine-tuning prep (built, not run)
Batch transcription (`src/batch_transcribe.py`), correction web tool (`src/correct_tool.py`,
NeMo manifest export), sermon-level train/holdout split that refuses Bible audio and Google-only
text pending the terms check, NeMo trainer (`--check` validated on MPS), and holdout evaluation
with the Phase 1 scorer. See `src/finetune/README.md`.

## Reproduce
```bash
.venv/bin/python src/bible_testset.py && .venv/bin/python src/score_bible.py
scripts/run_stt_queue.sh .venv-nemo work/bible/manifest.csv work/bible/segments bible jackrabbit-ctc-lm-b8
.venv/bin/python src/mt_data.py && scripts/run_mt_queue.sh
scripts/quiet_measurements.sh        # timings; run on an idle machine
```
