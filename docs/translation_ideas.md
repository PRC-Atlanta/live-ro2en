# Translation ideas

A running list kept by Claude while monitoring sessions (`/startlive`). Each idea has the
evidence that prompted it (log, time, the phrase), what it would change, and a rough effort.
Nothing here is done unless marked **done**. Newest evidence is added to an existing idea
instead of repeating it.

Status: `idea` → `next` (worth doing soon) → `done` / `dropped`.

---

## 1. Quickest: use Gemma with short phrases instead of NLLB — `next`

- **Evidence (2026-10-23_203552):** almost every nonsense line came from NLLB on correct
  Romanian fragments: "Cântări au fost foarte" → "Weights were very"; "La revedere, frate
  Dragoș" → "See you. Brother, Sweetie."; "Isus Hristos." → "Jesus Christ. What are you
  doing?"; Hebrews 6:10 split into 10 pieces read "So that they may forget your striving"
  (the opposite of the verse). Best (Gemma) on the same kind of speech was faithful.
- **Change:** profile `fast` → `translator: mlx:…gemma…`, keep the short phrases (2-3.5 s),
  `context_clauses` 4. Gemma took ~2 s on 5 s phrases; on 2-3 s phrases expect ~1-1.5 s, so
  first word ~4-5 s instead of ~3.2 s.
- **Check:** replay the 21:02-21:12 stretch (audio needed) or A/B in a rehearsal; compare
  first-word time and read both transcripts.
- **Effort:** small (config + test), plus a memory check (one model instead of two loaded).

## 2. Today's names should reach the Romanian recognizer — `idea`

- **Evidence:** names were the main recognition error in both modes: Puha → Pha / Puia / Gua /
  Puma / "Buha" ("Friends"); Paraschiva → Prascheva / "s-a parascheva" ("a parachute") /
  Răscreva; Dragoș → Drago, "frate de Dragoș" → "Brother Dragon".
- **Today:** Names & words (terms) feed the glossary, spellings and the *English* recognizer
  (Whistle keywords). The Romanian recognizer (Jackrabbit CTC + 4-gram LM, beam 8) gets nothing.
- **Change:** boost today's names in the CTC beam search (word boosting / hotwords in the LM
  decoder), and add fuzzy spelling fixes for near-misses (Pha → Puha) before translation.
- **Effort:** medium (decoder support needs checking in NeMo's beam decoder).

## 3. Bible references: show the published verse — `idea`

- **Evidence:** readings are announced ("Evrei, capitolul 6, versetul 10", "Proverbe,
  capitolul 19, versetul 22", "2 Corinteni 5 cu 7") and then read slowly in fragments, the case
  where translation is worst. `data/mt/bible_verses.jsonl` already holds RO/EN verse pairs.
- **Change:** detect a reference in the Romanian, then match the next phrases against that
  verse's Romanian text and caption the published English (NKJV/ESV-style) instead of
  translating fragment by fragment.
- **Effort:** medium; needs a full RO/EN Bible text with a license that allows it.

## 4. Give Gemma its own previous translations as context — `idea`

- **Today:** the prompt shows the previous Romanian chunks only ("context only, do not
  translate again"), not how they were translated.
- **Evidence:** consistency slips across chunks, e.g. "Sorei, Pareschiva" left untranslated,
  "Brother"/"brother" and "the church Elim" vs "Elim Church" in the same speech.
- **Change:** include the last 2-4 (source, translation) pairs so names, register and sentence
  flow carry over; ask it to continue the sentence when the previous chunk ended mid-sentence.
- **Effort:** small; check the prompt stays short enough for ~2 s.

## 5. Numbers and facts: a guard — `idea`

- **Evidence:** "Nouă copii au rămas" (nine children were left) → "They left behind two
  children"; "în urmă cu aproximativ 9 ani" kept correctly in another chunk.
- **Change:** compare digits / number words in source and translation; on a mismatch,
  re-translate once or log it for review.
- **Effort:** small.

## 6. Words that are also names — `idea`

- **Evidence:** "Dragoș" (name, also "dear") → "Sweetie"; "Casa Dânșiilor" (their house,
  capitalised by the recogniser) → "the House of Dancers"; "Hayomin".
- **Change:** a do-not-translate list (people, churches, places) built from terms + the
  glossary, passed to the translator as names; lower-case recogniser capitals on common words
  ("Dânșiilor") before translation.
- **Effort:** small.

## 7. Glossary misses in Quickest — `idea`

- **Evidence:** "fratele Iacov … fratele Vasile" → "the brother James … the brother Vasile"
  (glossary says Brother X); 107/112 glossary terms kept overall, the 5 misses were NLLB.
- **Change:** goes away with idea 1; otherwise a post-edit rule "the brother <Name>" →
  "Brother <Name>".
- **Effort:** tiny.

## 8. Strip model control tokens from translations — `next`

- **Evidence (2026-10-24_112256, rehearsal, 11:24:2x, EN→RO, Best):** "the school is pushing
  out as a sexual e education." → "școala<image|>împinge ca o educație sexuală." Gemma
  emitted a special token (`<image|>`) and it went straight to the caption.
- **Change:** in `MLXLLMTranslator.translate`, remove anything shaped like a chat/control
  token (`<…|>`, `<|…|>`, `<start_of_…>`, `<end_of_…>`) and squeeze the spaces; better,
  decode with special tokens skipped. Add a test.
- **Effort:** tiny.
