# Translation improvement plan

Built from two monitored services (2026-10-23 evening, 2026-10-24 funeral, ~2,300 phrases read
in full) and the evidence in `docs/translation_ideas.md` (idea numbers in brackets). Ordered by
harm prevented per hour of work. Each step is measured on the same replay before it ships.

What already shipped on 2026-10-24: word-paced reveal + smooth roll-up [21], keep-warm,
level/gain logging, home-biased language switching, middle phrase lengths.

---

## Step 0 — A test bench from today's recording (do first, ~half a day)

Everything below is judged on the same material instead of by impression.

- `logs/recordings/2026-10-24_124200.wav` (raw input, 12:42 onward: sermons, an English/Romanian
  mixed eulogy, an English reader, songs, an 89-year-old speaker) plus the service log with the
  same times.
- A replay script that feeds the recording through the real engine + pipeline (file mode) at a
  chosen gain and settings, and writes a log like a service.
- An error list to score against: the ~60 concrete failures noted today (wrong facts, reversed
  meaning, garbage captions, wrong language, names, dropped lines), each with its time.
- Score per run: wrong-meaning captions, garbage captions, dropped content, wrong-language
  lines, name errors, first-word delay, last-word delay.

## Step 1 — Stop wrong and offensive captions (~1 day)

The worst things on screen today were not translation choices but garbage: "Glory to gambling",
"the gay will polarize", "the curse of the family", "drunk", "Jesus in Paris", model tokens.

1. **Clean the model's output** [8, 22]: keep only the text after Gemma 4's thinking channel,
   strip `<…|>` tokens, cut "(Note: …)" asides; check the mlx-lm/tokenizer versions (the
   `<image|>` token sits where a space belongs, ~10 times today).
2. **Confidence gate** [17]: before translating, reject a phrase when the share of real
   Romanian/English words is low, it is mostly single letters, or the Romanian and multilingual
   transcripts of the same audio disagree completely; never send 1-2 character fragments.
   A missing caption is better than an invented one.
3. **Song switch** [18]: one key / button that holds captions (shows ♪) while a song plays;
   later, automatic detection.
4. **Numbers and negations guard** [5]: write dates/numbers as digits before translating;
   compare numbers and "nu/not" between source and output; re-translate once on a mismatch.
   (Today: "fifteenth" → "fiftieth", 14 → 15 children, "we move" → "we do not move".)

## Step 2 — Context and phrase boundaries (~1-2 days, biggest quality gain)

About half of the meaning changes today came from a sentence cut in two.

1. **Give Gemma its own last translations** [4]: the previous 2-4 (source, translation) pairs,
   and "continue the sentence if the previous piece ended mid-sentence". Fixes "we don't exist",
   "I married him", "he wasn't okay with the Bible", "did not keep his watch".
2. **Cut by words, not seconds** [20]: at least ~6 words per phrase; never end on din/de/la/și/că
   unless the maximum length is reached.
3. **Trim repeated overlap** [16] (English recogniser repeats the end of the previous chunk).
4. **Language switching** [12, 10]: use the Romanian recogniser's transcript as a second vote;
   an empty Romanian result with a clean English one counts as English (and is translated, not
   dropped); a length floor for switching back. Mixed speakers kept up well; the cost was the
   first 1-2 sentences after each switch.
5. **Keep name lists** [15] instead of dropping them as "untranslated".

## Step 3 — Church vocabulary, names and Scripture (~1 day + data)

1. **Glossary rows** [9] (data only): sora → Sister, priveghi → wake service, sicriu → coffin,
   arhanghel → archangel, mire → bridegroom, necrolog → obituary, Împărăția lui Dumnezeu,
   ucenici, a se lepăda de sine, credincios, răsplată; the 66 Bible book names RO↔EN applied to
   "<Book> <n>" before translation (today: Hebrews → "Ieremia", Isaiah → Revelation/Job).
2. **Idioms and the pronoun for God** [11]: a short list in the prompt (te rugăm frumos, a găsi
   cu cale, în repetate rânduri, scara blocului, liniuța, domnule vs Doamne, tata X ≠ Father X)
   and one rule for You/you.
3. **Names stay names** [6, 14, 2]: today's names (Names & words) become a do-not-translate list
   (Ilie ≠ Elijah, Petru ≠ Peter, Zaharia ≠ Zechariah, the children's names); fuzzy-map the
   English recogniser's spellings back (Dragosh → Dragoș, Souchava → Suceava); later, boost the
   names in the Romanian decoder.
4. **Scripture** [3]: first tell Gemma the announced reference ("reading Revelation 14:13, use
   NKJV/Cornilescu, translate only this part"), which keeps it aligned (it knows the verses);
   later, match well-known verses by their words and show the published text (Dragoș's favourite
   verse, Psalm 71:3, came out reversed: "cast me off" for "save me").

## Step 4 — English → Romanian, Quickest, automatic gain (~2 days, measured)

1. **EN→RO model choice** [13]: Gemma's Romanian had agreement errors and Spanish/Portuguese
   words ("onze", "botezit", "zelo", "tata meu"). A/B on the English parts of the recording:
   Gemma with a stricter prompt vs NLLB for EN→RO only; keep the better one per direction.
2. **Quickest on Gemma** [1]: short phrases, Gemma instead of NLLB (NLLB caused most nonsense on
   2026-10-23); check first-word delay stays ≈ 4-5 s.
3. **Automatic gain** [19]: level each speaker toward −20 dBFS from speech-only frames, slowly,
   with a fast limiter (today's speakers ranged −41 to −5 dB; the gain was moved 5 times by hand).
   Check on the recording whether it changes recognition, not just the meter.

---

## What I would not do yet

- A Bible-verse database before step 3.4's prompt version is measured.
- Fine-tuning a recogniser: names and boundaries are cheaper wins.
- Reading-time line dwell (no effect at 42 characters per line).

## Order and rough effort

| Step | Work | Effort | Main effect |
|---|---|---|---|
| 0 | Test bench from the recording | ½ day | every change measured |
| 1 | Output cleanup, confidence gate, song switch, numbers guard | 1 day | no garbage, no wrong facts |
| 2 | Context, word-based cuts, overlap, language votes | 1-2 days | meaning across chunks |
| 3 | Glossary, idioms, names, Scripture reference | 1 day | church vocabulary, names, verses |
| 4 | EN→RO choice, Quickest on Gemma, automatic gain | 2 days | the weaker direction, speed, level |

Each step: one branch, tests, the replay score before/after, then a restart between services.
