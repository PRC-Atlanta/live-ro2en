# Translation ideas

A running list kept by Claude while monitoring sessions (`/startlive`). Each idea has the
evidence that prompted it (log, time, the phrase), what it would change, and a rough effort.
Nothing here is done unless marked **done**. Newest evidence is added to an existing idea
instead of repeating it.

Status: `idea` → `next` (worth doing soon) → `done` / `dropped`.

---

## 1. Quickest: use Gemma with short phrases instead of NLLB — `dropped` — benched 2026-10-24: Gemma on Quickest 3/4 vs NLLB 4/4 on the sermon stretch and 4× slower; Quickest stays on NLLB (the option remains, stream E)

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

## 2. Today's names should reach the Romanian recognizer — `done` — stream G: today's names boosted in the Romanian beam search + near-miss repair before translation (35 fixes, 0 false on four logs) · 2026-10-25: stock church phrases boosted with the names (`engine.CHURCH_PHRASES`)

- **Evidence:** names were the main recognition error in both modes: Puha → Pha / Puia / Gua /
  Puma / "Buha" ("Friends"); Paraschiva → Prascheva / "s-a parascheva" ("a parachute") /
  Răscreva; Dragoș → Drago, "frate de Dragoș" → "Brother Dragon".
- **More evidence (2026-10-24 11:42, one prayer):** Puha → Puca, PA, Pva, PUV.
- **Today:** Names & words (terms) feed the glossary, spellings and the *English* recognizer
  (Whistle keywords). The Romanian recognizer (Jackrabbit CTC + 4-gram LM, beam 8) gets nothing.
- **Change:** boost today's names in the CTC beam search (word boosting / hotwords in the LM
  decoder), and add fuzzy spelling fixes for near-misses (Pha → Puha) before translation.
- **Effort:** medium (decoder support needs checking in NeMo's beam decoder).
- **Church phrases, not just names (2026-10-25):** "slăvit să fie Domnul" is heard as "**să vii
  să fie Domnul**" again and again (09:50:46 → "come let the Lord be", 10:47:16 → "that the
  Lord should come"), "Frați și surori" as "Fra și surori" (09:27, 09:21:53), "Lăudat" as
  "Ludați" (10:10:18), "Doamne" as "domnule" → "sir" (09:55:42, 11:11:33), "vrednic de laudă"
  as "vremii de laudă" (11:32:23, Ps 145:3), "Slăviți să fie Domnul" as "**Loviți** să fie
  Domnul" → "may the Lord be with us" (10:58:37), "**Iov** a fost" as "Eu v-a fost" → "**I** was
  blessed… His flocks" (11:45:18; Bible names, not only today's). **Change:** add a short list of stock church phrases (slăvit/lăudat să
  fie Domnul, frați și surori, binecuvântat să fie, pace vouă…) to the same beam boost and
  near-miss repair as today's names. Small; bench on today's clips.

- **More evidence (2026-10-25 evening, recogniser; captions were paused so nothing was shown;
  marked):** 18:35:24–18:35:44 "vino afară… **Ești** din confortul tău, **Ești** din
  religiozitatea ta, **ești** din nebunia…" — the speaker says «**ieși** din…» (come out of…),
  a Lazarus call; translated as heard it becomes "You are from your comfort". 18:35:55
  "botezați cu **un sân**" for «cu Duh Sfânt»; 18:30:27 / 18:36:10 «**Dul**» for «Duhul» /
  «Domnul». Candidates for the church-phrase boost: «Duh Sfânt», «Duhul Sfânt», «ieși afară»,
  «vino afară», **«m-am pocăit» / «pocăință»** (19:26:30 and 19:26:43 «când **m-am păcălit**» = «m-am pocăit», "when I repented" → would read "when I was fooled", twice; also «Modim» for «Nicodim» 19:26:17; but 19:42:05 «numai tu **pocăită**? tu ești **păcălită**» is a real taunt, pocăit/păcălit wordplay: boost «pocăit», never rewrite «păcălit»); and a repair rule: «Ești din» at the start of a piece after «vino afară» → «Ieși
  din». Effort: tiny for the phrases; bench on the marked clip.
  Also the sermon's own topic word: «mânie» heard as «mie» (19:45:20 «piciorul pe frână la
  mie», 19:52:41 «la mie aprinsă», 19:53:01 «nu se mie») and «îmbăie» (19:52:53 «să nu se
  mânie»). Too common to boost alone (the comment in `engine.CHURCH_PHRASES`); phrases would do:
  «la mânie», «se mânie», «iute la mânie», «încet la mânie». «Iacov» heard as «Iov» (19:44:43,
  James → Job) and «ca Nabal» as «canabal» (19:51:08): the day's Bible names could join today's
  names when a reference is announced (James 1, 1 Samuel 25).

- **Bench (2026-10-25 evening): the church-phrase boost costs accuracy on the corrected
  sermons.** The 521 human-verified segments (br-random-1, nelu-suciu; 5,585 words) re-decoded
  with the live recogniser: no boost 3.96 % WER, saved names + `CHURCH_PHRASES` 4.08 % (+7
  errors). «Slăvit să fie Domnul», heard right without the boost, becomes «slăviți să fie
  Domnul» 3× (the «slăviți să fie» phrase wins); «frați, surori» / «frații surori» get an
  inserted «și» 2× («frați și surori»); «Frate, la același Domn» loses «Frate». Adding
  «Cornilescu» changed nothing (identical output; the word isn't in these sermons). **Change:**
  drop «slăviți să fie» (or only «Slăviți să fie Domnul» whole) and bench the rest phrase by
  phrase on these segments plus the clips where the phrases were misheard.

## 3. Bible references: show the published verse — `done` — stream D: announced references tell Gemma the verse (step 1); stream H: verses recognised by their words are captioned with WEB / Cornilescu (step 2) · 2026-10-25: paraphrase not spliced, short verse words held, a verse matched one piece late redoes the previous caption, trailing mis-heard verse words dropped

- **Evidence:** readings are announced ("Evrei, capitolul 6, versetul 10", "Proverbe,
  capitolul 19, versetul 22", "2 Corinteni 5 cu 7") and then read slowly in fragments, the case
  where translation is worst. `data/mt/bible_verses.jsonl` already holds RO/EN verse pairs.
- **More evidence (2026-10-24 11:40):** "Romani, capitolul 14 … versetul 7 până la versetul
  9" announced clearly, then read in 5 chunks. Best kept the meaning but not the published
  text: "none of us dies for himself", "if we live for the Lord, we live" (NKJV: "no one dies
  to himself. For if we live, we live to the Lord"). Exactly the case this would cover.
- **Unannounced quotes too (12:01, video greeting):** Philippians 1:21, "Hristos este viața și
  a muri este un câștig", came through as "I am a great Christian, Paul was saying. | **She
  died.** | it is a gain." Matching well-known verses by their words (not only after an
  announced reference) would catch the famous ones.
- **(12:02):** "Psalmul 139, versetul 16" and "Psalmul 56 … versetul 8" announced, then read
  in 5-6 chunks: "your eyes saw me, and in the book. | All the days that were allotted to me
  were written." and "sunt scrise … și au | fost scrise" → "are been written" (ungrammatical
  English from a split). The published NKJV text would have been clean.
- **Gemma already knows the verses (12:03):** for Revelation 14:13 it produced the published
  wording, and even ran ahead: "who said, 'Write: Blessed are the dead who die in the Lord
  from now on.'" then the next chunk repeated "of those who die in the Lord". Psalm 56:8
  ("You collect our tears in your bottle") was right too. A cheaper first step than a verse
  database: after an announced reference, tell Gemma the reference in the prompt ("the
  speaker is reading Revelation 14:13; use the NKJV wording, translate only this part") so
  it stays aligned instead of running ahead.
- **(12:16, Psalm 103:14-17, not announced):** "își aduce aminte că suntem țărână" (that we are
  dust; recognised "țări. | țărm") → "that we are **a nation** … earth"; "bunătatea Domnului
  ține în veci peste cei ce se tem de El" → "we hold fast to what we fear Him". A famous
  funeral psalm: matching by its words would have given the published text.
- **(12:21):** Job 5:7 quoted in a sermon, "Omul se naște ca să sufere, precum | scânteia ca să
  zboare" → "Man is born to suffer. … just as | the spark, so that it may fly" (NKJV: "man is
  born to trouble, as the sparks fly upward").
- **His favourite verse, reversed (12:28):** Psalm 71:3, "Fii o stâncă de adăpost pentru mine,
  unde să pot fugi totdeauna! Tu ai hotărât să mă scapi" (You have decided to *save* me),
  recognised "să mă scap" → "You have decided to **cast me off**": the opposite, about the
  verse the family chose for him. And Psalm 103:17 "ține în veci" (recognised "în vechi") →
  "holds in the old **[testament]**": Gemma added a bracketed guess of its own.
- **Wrong books (13:36):** Isaiah 34:16 ("Căutați în cartea Domnului și citiți! Niciuna nu va
  lipsi") was announced as "34 cu 16 din cartea **Poc**" (recognised) → "from the book of
  **Revelation**", then "pe Poc, capitolul 3, versetul 16" → "the book of **Job**, chapter 3,
  verse 16": two different wrong references on screen. When the book name is unclear, better to
  show the numbers only, or match the verse text ("Niciuna nu va lipsi") to find the book.
- **Change:** detect a reference in the Romanian, then match the next phrases against that
  verse's Romanian text and caption the published English (NKJV/ESV-style) instead of
  translating fragment by fragment.
- **Effort:** medium; needs a full RO/EN Bible text with a license that allows it.
- **A verse's first chunk misses the match (2026-10-25 09:48:32, Quality, Daniel 3:17-18,
  announced: "versetul 17 din 3, Daniel", "versetul 18 spune așa"):** "să știi, împărate, că
  noi vom sluji Dumnezeu." → "know, **emperor**, that we will serve God." then the verse
  matcher caught the next chunk: "your gods or worship the golden | image which you have set
  up." On screen: "we will serve God. your gods or worship the golden image": the verse's
  meaning ("we will **not** serve your gods") reversed. The recogniser dropped "nu" and heard
  "dumnezeilor" as "Dumnezeu." on the chunk boundary. **Change:** when a verse is matched,
  look back one chunk: if the previous chunk overlaps the verse's opening words (here "să
  știi, împărate, că noi"), replace/retract it (Remove last) and caption the verse from its
  start; also glossary `împărat(e)` = king (Bible usage). Small-medium (stream H code).
- **A paraphrase spliced with the published verse (09:49:32):** "și 139 și multe versete din
  Biblie sunt care. că el ascultă rugăciunea, de aceea, toți oam[enii]" matched Psalm 65:2
  (score 0.97, 9 words, no reference announced) and came out "…many verses in the Bible are
  which that **He You who hear prayer, all men will come to you. the**": third person turned
  to "You", the verse glued mid-sentence. **Change:** with no announced reference, take a
  matched verse only when the chunk *is* the verse (covers most of the chunk, starts near a
  clause start), else translate normally; never splice a verse into the middle of a chunk.
  Small (stream H thresholds), bench on this clip.
- **Junk where the spoken chunk and the verse don't line up (2026-10-25):** Genesis 32:28-31
  (10:11:59-10:12:21): "but you will be called Israel. **who fights against God but Israel**";
  "called the name of the place **on Him**, the face of God"; "as he passed over **Please**"
  (a cut "Pl[enuel]"); "the man touched **conclusions**" (the reader's self-correction
  "încheieri… încheietura"). Matthew 28:20 (10:43:39): "all things that I commanded **And
  behold.** | **you.** Behold, I am with you always": the chunk's own "Și iată" translated and
  then the verse's "Behold" again. **Change:** inside a matched verse, caption only verse
  text; drop the chunk's leftover words that fall within the verse span (keep words before
  its start / after its end), and never mix Gemma's output into the verse.

- **More evidence (2026-10-25 evening, marked):** 18:10:14 "Să nu vă înșelați, prea iubiții mei
  frați." → "**Let** Don’t be deceived, my beloved brothers.": the verse caption (James 1:16 WEB)
  was put after a stray "Let" left from the previous piece. Also 18:10:36 «Știți bine lucrul
  acesta, prea iubiții mei frați?» (James 1:19a) → "So, then, my beloved brothers, let": the
  verse text runs one clause ahead of the speaker, fine here but it reads as cut off.

- **(2026-10-25 evening, shown) 18:16:05** «să facem tot ce este bine și plăcut înaintea
  Domnului» → "to do everything **that which was** good and right in the Lord": a verse
  (Deuteronomy 12:28-style wording) spliced into a prayer phrase, its tense and its cut-off end
  ("…in the Lord['s sight]") kept. The no-reference rule in this idea's 09:49:32 entry would
  have translated it normally.

## 4. Give Gemma its own previous translations as context — `done` — stream D: Gemma sees its own previous (source, translation) pairs and continues cut sentences

- **Today:** the prompt shows the previous Romanian chunks only ("context only, do not
  translate again"), not how they were translated.
- **Evidence:** consistency slips across chunks, e.g. "Sorei, Pareschiva" left untranslated,
  "Brother"/"brother" and "the church Elim" vs "Elim Church" in the same speech.
- **More evidence (2026-10-24 service, 11:38):** "Este un rând. | la care la toți ne vine
  rândul" (a turn that comes to all of us) → "It is your turn. | at which time it is everyone's
  turn": a sentence cut across two chunks, each translated as if complete. "sora Paraschiva" →
  "Sora Paraschiva" again (Sister left untranslated at a chunk start).
- **More evidence (2026-10-24 11:43, prayer):** "cât de trecători suntem" → "how fleeting we
  are" (correct); a few chunks later the recogniser wrote "trăgători" and Gemma guessed "how
  prone we are to wander". With its own earlier "fleeting" in the prompt it would likely repeat it.
- **More (11:56, obituary in Romanian):** "împreună cu soția sa, Paraschiva, | mama noastră,
  au crescut toți cei 14 copii" (together with his wife … they raised all 14 children) →
  "our mother, all 14 children grew up": the subject sat in the previous chunk, so "au
  crescut" was read as intransitive. "El amintea mereu soției" (he always reminded his wife)
  → "He always remembered his wife". Both meaning changes; the English version a few minutes
  earlier had them right.
- **(11:57):** the idiom "în | repetate rânduri" (repeatedly) was cut after "în", and the next
  chunk "repetate rânduri în spitale" → "repeated **rows** in hospitals".
- **(11:58):** "Vegheat de iubita | sa soție, Paraschiva" (watched over by his beloved wife)
  → "He was a beloved husband. | his wife, Paraschiva."; "a călătorit" (he travelled) → "I
  traveled" (the subject, Dragoș, was chunks back).
- **(12:01):** "În opinia noastră, da, este de vreme. | devreme" (it is too early) → "it is
  about time"; "de ce a plecat așa devreme" → "why **she** left" (about Dragoș).
- **(12:06):** "Una dintre dorințele | fratelui Dragoș a fost să cânt cu fata" (to sing with
  my daughter; "fata" = the girl, "fața" = the face) → "One of the desires. | one of the
  desires of brother Dragoș was to sing with **his face**": the first chunk repeated, and the
  diacritic-less word read the wrong way. With the previous pair in the prompt it can
  continue instead of repeating.
- **(12:20):** "viața noastră … este extrem. | extrem de fragilă" → "is **fleeting**. |
  extremely fragile.": Gemma completed the cut-off chunk with a word of its own, despite the
  prompt's "translate it as a piece". Keeping its previous output in view (or holding a chunk
  that ends on an adverb like "extrem") would avoid inventing the end.
- **(12:50):** about a business deal he refused, "dar nu a fost ok cu Biblia, a zis, nu vreau"
  (but it wasn't right by the Bible; he said, I don't want it) → "but **he** wasn't okay with
  the Bible; he said, I don't want it": the subject (the deal) was in English a phrase
  earlier, and the caption now says he rejected the Bible. Context across the language switch
  matters too.
- **(13:08):** "n-a ținut ură" (he held no hatred; said three times a minute earlier and
  translated right) was recognised once as "n-a ținut **tură**" → "did not keep his **watch**";
  "pe scaunul din spate" (the chair at the back of the room, his usual place) → "in the back
  **seat**". With the previous pairs in the prompt both would likely have been repaired.
- **(13:26):** "Dacă nu știm asta, nu suntem | pregătiți pentru momentul morții" (we are not
  prepared) → "If we don't know this, **we don't exist**. | prepared for the moment of death."
- **Change:** include the last 2-4 (source, translation) pairs so names, register and sentence
  flow carry over; ask it to continue the sentence when the previous chunk ended mid-sentence.
- **Effort:** small; check the prompt stays short enough for ~2 s.

## 5. Numbers, negations and facts: a guard — `done` — stream A: `src/live/guards.py` (numbers to digits before EN→RO, number/negation check with one re-translation) · 2026-10-25 follow-up `done`: retry with a note, NLLB fallback, a still-flagged negation never shown; «mai» / «nu-i așa că» notes; no false alarm on «a doua oară» / «ne-» words; glued and longer numbers, odd figures hidden

- **Evidence:** "Nouă copii au rămas" (nine children were left) → "They left behind two
  children"; "în urmă cu aproximativ 9 ani" kept correctly in another chunk.
- **EN→RO (2026-10-24 11:51):** "on the morning of october fifteenth" → "în dimineața zilei de
  **cincizeci** de octombrie" (fifty, not fifteen): the date of his death, wrong on screen.
  Number words both ways are the risk: write dates and numbers as digits first ("October 15"),
  then translate.
- **Recogniser side (11:53):** "cel mai mare dintre cei doisprezece frați" was recognised as
  "cei **doi** frați" → "the eldest of the two brothers and sisters" (the English reading a
  minute earlier said twelve). A guard can't catch that; boosting number words in the
  Romanian decoder or comparing with the earlier English reading could.
  **Again 2026-10-25 11:53:50**, same word: "Iacov, tatăl celor **doisprezece** fii" → primary
  recogniser "celor **doi** fii" → "the father of the **two** sons"; the second recogniser
  (the `multi` field in the log) heard "celor **12** fii". **Change:** when the two transcripts
  disagree on a number, prefer the longer number word (doisprezece ⊃ doi, paisprezece ⊃
  patru…) or the multi transcript's digits; cheap and both are already computed.
- **RO→EN, translator side (11:58):** "cei paisprezece copii" → "their **fifteen** children"
  (fourteen; the next chunk "celor 14 nepoți" → "14" was right). Number *words* are the risk
  in both directions; a guard comparing number words/digits would have caught this one.
- **Negation invented (12:22):** "Și a pus hotarul acesta | ca locuință, între cele două ne
  mișcăm" (between the two we move) → "that we do **not** move between the two as a dwelling
  place". A "not" with no "nu" in the source reverses the meaning; the same guard can count
  negations (nu/n-/nici ↔ not/no/never).
- **(13:29):** "dar privită de sus, nu-i așa?" (but seen from above, isn't it [different]?) →
  "but seen from above, **it isn't**, is it?": the tag question "nu-i așa" read as a negation.
  Again 2026-10-25 11:44:47: "**nu-i așa că** uneori avem impresia că binecuvântare înseamnă
  doar…?" (isn't it true that sometimes…?) → "**it is not that** sometimes we have the
  impression…". And 11:10:35 "nu de asta v-am cumpărat adidași, [să-i] purtați?" (isn't that
  why…?) → "I didn't buy you these sneakers just to wear them". The guard ignores tag
  questions (so it can't catch these); an idiom row "nu-i așa că … ?" = "isn't it true that
  …?" and a prompt line on rhetorical "nu … ?" questions would.
- **Change:** compare digits / number words in source and translation; on a mismatch,
  re-translate once or log it for review.
- **Effort:** small.
- **After the guard, both tries wrong (2026-10-25 09:10:26, Quality, prayer):** "Ne-ai **mai**
  dat o zi binecuvântată." (You have given us **another** blessed day) → "You have **not** given
  us a blessed day." The guard flagged it (source false, translation true), the retry said
  "You haven't given a blessed day." too, and `kept: first` showed the reversed meaning.
  Gemma reads "mai" as "nu mai". **Follow-up:** when the retry fails the same check, (a) retry
  with a hint in the prompt ("the source has no negation; 'mai' here means again/another"),
  or (b) fall back to the other translator (NLLB) for that line, or (c) strip the "not" and
  show the result; never show a flagged negation unchanged. Also an idiom row: "ne-ai mai dat"
  → "you have given us another". Small.
- **Again, on ProPresenter (2026-10-25 11:00:50):** "Doamne, dacă tu ne-ai chemat să ne mutăm
  în Filipine, dacă tu ne-ai arătat… terenul" → "Lord, if **You didn't call us** to move to
  the Philippines, if **You didn't show us**…". The guard flagged it; the retry was negative
  too; `kept: first` put the reversed meaning on screen. Likely cause: the same prayer a
  minute earlier (10:59:19) was misheard as "dacă tu **nu** ne-a chemat" (the second
  recogniser even heard "tu nu ai chemat" now), and Gemma followed its context. Three
  occurrences today → the follow-up above is worth doing first: retry *without* context and
  with the "no negation in the source" hint, and never show a still-flagged negation.
- **"mai" = "nu mai" again (11:04:56, ProPresenter):** "domnule Manuel, ne **mai** trebuie fonduri"
  (we need **more** funds) → "we **don't need any more** funds"; flagged, retry identical, kept.
  Two of today's four reversals are bare "mai" (more/again/another). **Change:** before
  translating, when "mai" has no "nu"/"n-" before it, add the hint "'mai' here means
  more/again, not 'no longer'" — or substitute "încă" for the translator.

- **Negation inside a verse-matched chunk (2026-10-25 10:13:47, prayer):** "pentru ascultă
  rugăciunea. de aceea. toți oamenii vor reveni la tine, **de ne putere** să ascultăm" (dă-ne
  putere: give us strength) → "for hear the prayer. all men will come to you. that we have
  **no power** to listen" (Psalm 65:2 matched). The guard skips phrases with a verse
  (`pipeline.py`: "a published verse is not checked"), so the invented "no" in the
  non-verse part passed. **Change:** check the part of a chunk outside the matched verse.

- **A spoken range run together (10:51:41):** "sunt în jur de vreo patru, cinci, șase, șapte
  mii de copii" (about 4-7 thousand) was written by the recogniser as "**4567.000**" →
  "about **4,567,000** children". The guard flagged it (4567 vs 4567000), the retry said the
  same, `kept: first`. Two fixes: the recogniser's number formatting should not glue a list of
  single digits ("4567" from "patru cinci șase șapte"), and when guard and retry both fail on
  a number, show the digits as heard ("4-7 thousand") or drop the number rather than keep it.
  Again on ProPresenter at 11:07:55: "cinci, șase, șapte mii de dolari" → "50067.000" →
  "worth **50,067.000 dollars**" (the next chunk said "5.000-6.000 7.000" correctly). Same
  speaker habit twice in 15 minutes: worth fixing in the number formatting.
- **A money figure in the announcements (11:22:01, hidden):** the church loan balance, said
  slowly in pieces ("un milion două sute șaptezeci și unu de mii șapte sute"), came out across
  three chunks as "1,207." | "1.271701 million. 271,700,000." | "dollars." (likely
  $1,271,700). Numbers spoken over a chunk boundary need to be joined before formatting;
  failing that, a figure the recogniser formats oddly (decimal point inside "milion",
  9+ digits) should be flagged, not shown.

- **False alarm that costs seconds (11:03:09):** "Domnul a [întrebat] a **doua** oară" → "the
  Lord asked me a **second** time" was right, but the guard counts "doua" as 2 and finds no
  "2"/"two" in the English (ordinals not mapped) → retry, translation 8.9 s, first word 19 s.
  **Change:** map ordinals both ways (prima/primul = first/1st, a doua = second, a treia =
  third… and "1st/2nd") before comparing. Tiny; cuts guard retries (idea 24).
- **Another false alarm (12:05:20, closing prayer):** "dacă sunt suflete nete. ne mântuite"
  (= nemântuite, unsaved; split by the recogniser) → first answer "souls who are **not** saved"
  was right, but the guard saw no "nu" in the source, retried (translation 10.2 s, first word
  22 s) and kept the retry "souls lost". **Change:** count the Romanian "ne-" prefix of a
  known adjective (nemântuit, necredincios, nepocăit, neascultător…) and a lone "ne" before
  a participle as a negation; also skip the retry when the only "not" in the English is
  "not" + a participle matching such a word.

- **More evidence (2026-10-25 evening, captions paused so nothing was shown; marked):**
  - 19:25:43 Ezekiel 36:26 «Vă voi da o inimă nouă… Vă voi da inima **nouă**» came out of the
    recogniser as «Vă voi dau inima **9**.»: Jackrabbit itself writes the digit, so the guard's
    «nouă» = new rules (o nouă viață, ne-a spus nouă) never see the word. Would read "I will give
    you heart 9" (or a number-guard retry). **Change:** before translation, a lone «9» right after
    a noun/adjective and not followed by a counted noun (zile, ani, oameni, capitolul, versetul…)
    goes back to «nouă»; same for «a 9-a» style only with a counted noun. Tiny.
  - 19:19:30 «ca **un** păcătos» (as a sinner) → recognised «ca și **nu** păcătos»; 19:20:44
    «**Noi am** fost vânduți robi păcatului» (likely) → «**N-am** fost vânduți…». Two negations
    the speaker didn't say, in one sermon; the guard checks that RO and EN agree, so it would pass
    both. The verse matcher could catch the second (Romans 7:14 «vândut rob păcatului»). Nothing
    simple for the first; note for the recogniser fine-tune list.

## 6. Words that are also names — `done` — stream D: today's names and glossary names are kept as written · 2026-10-25: «Fratele» alone no longer brings in saved names; «fratele Ilie Puha» kept; Iacov = Jacob in Old Testament context

- **Evidence:** "Dragoș" (name, also "dear") → "Sweetie"; "Casa Dânșiilor" (their house,
  capitalised by the recogniser) → "the House of Dancers"; "Hayomin".
- **More (2026-10-24):** "Zaharia" → "Zechariah" and "Bala" → "Paul" (people's names turned into
  Bible names); in EN→RO, "Jonathan" → "Ionatan", "Joseph" → "Iosif" for the family's children.
- **Change:** a do-not-translate list (people, churches, places) built from terms + the
  glossary, passed to the translator as names; lower-case recogniser capitals on common words
  ("Dânșiilor") before translation.
- **Effort:** small.
- **(2026-10-25, announcements, on ProPresenter):** 11:18:42 "fratele Benu și fratele **Ilie**
  Puha" → "Brother **Elijah** Puha" (a person's name, translated as the prophet); 11:17:27
  "luați legătură cu fratele Emmanuel și Dumnezeu [să binecuvinteze]" → "get in touch with
  **Fratele Emanuel și Dumnezeu**" — left in Romanian, as if "Fratele Emanuel … și …" were one
  of today's names (the name repair matched "Fratele Emanuel Pop" and swallowed "și
  Dumnezeu"). **Change:** Romanian given names before a surname (Ilie, Ioan, Petru, Pavel,
  Iacov…) stay as written when preceded by frate/sora or followed by a capitalised surname;
  the near-miss repair must not extend a name across "și" + a common word.
- **Iacov = Jacob or James (2026-10-25 11:57:18, sermon on Genesis 32):** after 20 minutes of
  "Iacov" = Jacob, "în versetul 30, Iacov a pus locului acelui numele" → "**James** changed
  that name" (Gemma's other reading of Iacov). **Change:** today's context decides: when the
  announced/matched passage is Old Testament (or "Iacov" co-occurs with Israel, Esau, Laban,
  Isaac), hint "Iacov = Jacob"; with apostle/epistle context, "Iacov = James". A one-line
  prompt hint from the last matched verse's book.

## 7. Glossary misses in Quickest — `done` — via the glossary rows (stream D) and NLLB only where it is good

- **Evidence:** "fratele Iacov … fratele Vasile" → "the brother James … the brother Vasile"
  (glossary says Brother X); 107/112 glossary terms kept overall, the 5 misses were NLLB.
- **Change:** goes away with idea 1; otherwise a post-edit rule "the brother <Name>" →
  "Brother <Name>".
- **Effort:** tiny.

## 8. Strip model control tokens from translations — `done` — stream A: special tokens banned at generation (mlx-lm ignored the model's suppress list), output cleanup

- **Evidence (2026-10-24_112256, rehearsal, 11:24:2x, EN→RO, Best):** "the school is pushing
  out as a sexual e education." → "școala<image|>împinge ca o educație sexuală." Gemma
  emitted a special token (`<image|>`) and it went straight to the caption.
- **Live service too (2026-10-24 11:50, EN→RO):** "following our many prayers, your many
  prayers." → "în urma multor rugăciuni ale noastre, multor rugăciuni ale vo<image|><|channel>thought
  <channel|>în urma multor rugăciuni ale noastre, multor rugăciuni ale voastră." on screen:
  Gemma 4 opened its *thinking channel* (`<|channel>thought … <channel|>`), and the half
  answer, the tokens and the full answer all went up. The current cleanup only removes
  `<think>…</think>` (another model's format).
- **Not only Romanian (12:38, RO→EN):** "We, as a family, the Barbus,<image|>value you very much."
- **Pattern (11:50-11:52, 5 times, in Romanian output):** "în<image|>eternitate",
  "năde<image|>jde", "școala<image|>împinge": the token stands where a space or a word piece
  should be. That looks like a tokenizer/decoding mismatch for this Gemma build rather than
  the model choosing it; simply deleting it would leave "năde jde" / "îneternitate". Check
  the mlx-lm and tokenizer versions against the model card, and test-decode Romanian text.
- **Change (Gemma 4):** keep only the text after the last `<channel|>`, then strip leftover
  tokens; also disable thinking in the chat template for this model.
- **Change:** in `MLXLLMTranslator.translate`, remove anything shaped like a chat/control
  token (`<…|>`, `<|…|>`, `<start_of_…>`, `<end_of_…>`) and squeeze the spaces; better,
  decode with special tokens skipped. Add a test.
- **Effort:** tiny.

## 9. Church terms the translator gets wrong: add them to the glossary — `done` — stream D: glossary rows + `data/bible_books.csv` · 2026-10-25: inflected forms matched (after exact ones); rows fiori, cântare, minister, Gosen, împărat

- **Evidence:** "slujbă de priveghi" (the wake / vigil service; the recogniser writes
  "privechi") → "the service of the elders" (2026-10-23 20:43 and 2026-10-24 11:39, both in
  Best). A one-line glossary entry would fix it every time.
- **Change:** glossary rows `priveghi`/`privechi` = `wake service` (or `vigil`), and collect
  more as they show up (e.g. "fii surori" from the recogniser → "sons and daughters", meant
  "frați și surori" = "brothers and sisters").
- **EN→RO too (11:46):** "God's kingdom" → "regatului lui Dumnezeu" (a political kingdom);
  church Romanian says "Împărăția lui Dumnezeu". "save souls" → "să salveze" (better "să
  mântuiască"). Glossary rows work in both directions.
- **More (11:53):** "necrologul" (the obituary; recognised "Negurolog") → "Nehemiah". Glossary
  row `necrolog` = `obituary`, plus the near-miss spelling.
- **"sora X" (12:17, 4th time today):** "sora Paraschiva" at the start of a chunk → "Sora
  Paraschiva". Glossary row `sora` = `Sister` (like `fratele` = `Brother`).
- **(12:19):** "sicriului" → "tomb" here, "coffin" at 12:16: glossary `sicriu` = `coffin`.
- **(12:28):** "sora Paraschiva" recognised "sură paraschiva" → "the **pale** Paraschiva" ("sură"
  = grey). A spelling rule `sură <Name>` → `sora <Name>` before translation.
- **(12:42, 1 Thess 4:16):** "cu glasul unui arhanghel" → "with the voice of **an angel**":
  glossary `arhanghel` = `archangel`.
- **EN→RO Bible words (12:51, Matthew 16:24):** "deny himself" → "să se **nege** pe sine"
  (Cornilescu: "să se lepede de sine"); "disciples" → "discipolilor" (church: "ucenicilor");
  "God is faithful" → "fidel" ("credincios"). Glossary rows for the EN→RO direction too.
- **Bible book names (12:54):** "In Hebrew thirteen seven" → "În **Ieremia** 13:7" (Jeremiah;
  should be "Evrei 13:7"): a wrong reference on screen. A fixed table of the 66 book names
  EN↔RO (Hebrews = Evrei, Thessalonians = Tesaloniceni, Romans = Romani…) applied to
  "<Book> <number>" patterns before translation. Same verse: "the outcome of their conduct" →
  "rezultatul **conducerii** lor" (leadership; should be "sfârșitul felului lor de viețuire").
- **(13:10, a song text read aloud):** "venirea mirelui Isus" (the coming of the bridegroom,
  Jesus) → "the **miracle** of Jesus"; "când mirele pe nori" → "when the **bride** on the
  clouds" (bridegroom → bride). Glossary `mire / mirele` = `bridegroom`.
- **Effort:** tiny (glossary.csv), no code.
- **Inflected forms miss the glossary (2026-10-25 09:45:19, Quality, after a funeral):** "cum
  se perindau **sicriile** în față" (how the coffins passed before us) → "how the **cicadas**
  were wandering in front"; glossary.csv has `sicriu` and `sicriul` but not the plural
  `sicriile`/`sicriele`, so Gemma got no hint. Same minute: "te **fiori**!" (shivers) → "the
  **flowers**!"; 09:44:52 "acum din **cântare**" (from the song) → "now from the heart".
  **Change:** match glossary rows on the stem/lemma (or list the common forms: -ul, -ului,
  -ele/-ile, -elor/-ilor) and add `fiori` = shivers, `cântare` = song/hymn. Small.
- **More glossary rows from 2026-10-25 (on ProPresenter):** "ce **minister**, ce biserică e în
  spatele dumneavoastră" (which ministry/organisation) → "which **ministers**" (11:04:30);
  "ani de zile" → "years **of days**" (11:09:38); "Te rog frumos" → "We humbly pray" (11:06:34,
  should be "please"); "pui" = puppies (not chicks) when dogs are the topic (11:08:12);
  "baza de misiune **Gosen**" = **Goshen** (Bible place; came out Gosen/Goshen/"broadcast base").

## 10. Empty-phrase fallback misses short Romanian words — `done` — stream B

- **Evidence (2026-10-24 11:39):** the Romanian recogniser returned nothing, the multilingual
  one heard "binecuvinteze." and it was still dropped: the fallback needs a Romanian-looking
  score > 0, and one word without diacritics or common words scores 0.
- **Change:** in RO→EN (locked, or auto with Romanian current) accept the multilingual text
  unless it scores as English (< 0).
- **Effort:** tiny.

## 11. Prayer language: idioms and the pronoun for God — `done` — stream D: `data/idioms.csv` injected when present; one rule for You/you

- **Evidence (2026-10-24 11:41, prayer, Best):** "te rugăm frumos" (we humbly / earnestly pray)
  → "we ask beautifully"; "fiorul Duhului Tău" (the stirring/touch of Your Spirit) → "the
  shiver of Your Holy Spirit"; in the same prayer "before you … for you are the God" and then
  "Your Holy Spirit", "You let Your comfort": capitalisation switches mid-prayer.
- **(12:18, sermon):** "așa a găsit El cu cale" (so He saw fit) → "so he found a way out of
  this garden" (idiom read literally; and "he" for God, after "He calls them home").
- **(12:23, sermon image):** "liniuța dintre anul nașterii și anul morții" is the well-known
  "dash between the dates" on a tombstone; it came out as "a line" and later "for his journey
  to come to an end" (the image dropped). Idiom list: "liniuța" (on a grave) = "the dash".
- **Everyday Romanian too (12:26, a story):** "în aceeași scară" (the same entrance/stairwell
  of an apartment block) → "on that same **ladder**"; "pe artera cealaltă" (the other street)
  → "on the other **artery**". Idiom list: "scara (blocului)" = "entrance / stairwell".
- **Preacher's connectives (12:27):** "în al treilea rând" (thirdly / third) → "in the third
  place"; "dacă vrei / dacă vreți" (if you will) → "if you want". Small, but they make the
  English sound translated.
- **(12:31):** "tot ce atârnă de voi" (everything that depends on you / all you can) → "everything
  that concerns you"; "mă voi întâlni cu Tatăl tău" (her husband, the preacher's father) →
  "your **F**ather" (capitalised as if God; same as "His footsteps" for Dragoș at 12:30).
- **(12:51):** "n-a umblat după scaune" (he never sought positions / places of honour) → "I'm
  not looking for chairs" (idiom literal, and the person changed).
- **(12:53):** "a stat în spărtură" (stood in the gap, Ezekiel 22:30; recognised "a dat în
  sfărtură / spărtură") → "gave in sacrifice"; EN→RO "reward" → "recompensă" (church Romanian
  "răsplată", Matthew 10:41).
- **(13:29):** "tata Dragoș" / "Tate Dragoș" (Dad Dragoș, the children's father, said with
  affection) → "**Father** Dragoș" (reads like a priest's title; also "Father, Dragoș" at 19:20
  yesterday); "fratele lui Dragoș" (Dragoș's brother) → "his brother Dragoș".
- **(13:37, a pastor's memories):** "l-am căsătorit, l-am botezat" (I officiated his wedding,
  I baptised him) → "then **I married him**"; "o familie model" (a model family; recognised
  "modernă") → "a **modern** family"; "sosind în Portland" → "Pollan".
- **"domnule" vs "Doamne" (12:29 and 13:43):** "Mulțumesc, domnule, pentru biserica de aici"
  (thank you, sir / brother, addressing the host pastor) → "Thank you, **Lord**". "Doamne" is
  God, "domnule" is a man; the prompt/glossary should keep them apart.
- **Change:** a short list of Romanian church idioms in the prompt (or glossary): "te rugăm
  frumos" = "we humbly pray", "fiorul Duhului" = "the touch of the Spirit", "slăvit să fie" =
  "praise be"…; one rule for addressing God (always You/Your, or always you/your).
- **Effort:** small; collect idioms as they appear.

## 12. Language detection: check the Romanian recogniser before switching — `done` — stream B: the Romanian transcript as a second vote, English heard only by Parakeet translated, length floor back home

- **Evidence (2026-10-24 11:42:5x, all-Romanian service, auto):** during a prayer the
  multilingual model wrote Romanian speech as "Family PUV, the most as court and the first
  time." (10 words, 5 of them common English words: score -0.5). That met the one-phrase
  rule (≥10 words, ≤ -0.5) exactly, so the app switched to EN→RO for 3 phrases and showed
  "Familia PUV, cea mai mare și prima dată." / "în acest, ei se uită la noi, ultimul meu." /
  "echipa." in Romanian before switching back.
- **Change:** before leaving Romanian, also score the Romanian recogniser's own transcript of
  the same audio (it is computed anyway, `ev["ro"]`): switch only if *it* also looks
  non-Romanian. A real English speaker makes the Romanian model produce nonsense with few
  Romanian function words; garbled Romanian still does. Also tighten the one-phrase bar
  (-0.6, 12 words). Until then: lock RO→EN for all-Romanian services.
- **More evidence (11:46, English obituary):** the opposite failure. While the English
  reader spoke, a 2-word phrase "pasă de." (one diacritic, so a strong Romanian score) switched
  straight back to Romanian, and two English sentences were then dropped as empty ("He didn't
  care about the earthly things, the earthly possessions", "Everything he did was for that
  purpose.") because the Romanian recogniser heard nothing. Returning home needs a length
  floor too (≥4 words for a one-phrase switch), and an empty Romanian transcript with a clearly
  English multilingual one should count as an English vote (and be translated EN→RO), not be dropped.
- **Mixed speaker (12:44-12:45, Romanian and English sentence by sentence):** the switch rule
  kept up well (one phrase lost per switch). It can't handle a switch *inside* a sentence:
  "He loved the Lord. Is it true? Dragoș a iubit pe Domnul" (said in Romanian) came out as
  "Dragosh a you be pedomun" from the English recogniser.
- **Effort:** small; replay both service logs to tune.

## 13. English → Romanian: grammar, wrong-language words, idioms — `done` — stream E: translator per direction; Best uses NLLB for EN→RO (bench: 21/22 vs 20/22, 1.45 s vs 2.25 s)

- **Evidence (2026-10-24 11:45, obituary read in English, Best):** "especially in these last
  few days" → "în special în acești ultimi câteva zile" (should be "în aceste ultime zile":
  gender/number agreement broken); "they missed the mountain for the molehill" → "au făcut o
  povară din nimic" (an idiom swapped for one with a different meaning; meant "they missed the
  big picture"); "recently." alone → "recentul." (a one-word fragment turned into a noun).
  Speech level -40 dB for this reader (the English speaker's mic is quieter).
- **Worse evidence (11:47, the life story):** "At age eleven" → "La vârsta de **onze** ani"
  (Spanish/Portuguese, should be "unsprezece"); "baptized" → "**botezit**" and "**batizat**"
  (should be "botezat"); "twelve siblings" → "douăspreze" (misspelt); "born … to Barnes
  Cicillia and Theodore" → "**către** Barnes Cicillia" (wrong preposition; "părinților" / "fiul
  lui"); "May nineteenth, nineteen sixty-three" → spelled out in words, then the next chunk
  repeats it as "19 mai 1963". The 4-bit Gemma's Romanian slips into other Romance languages.
- **More (11:48):** "reunited" → "s-a reunionit" (should be "s-a reunit"); "were born" →
  "născu" (cut short); "In nineteen ninety-six" → "În nouăsprezece de nouăzeci și șase"
  (1996 garbled; the recogniser spelled the year as words). Children's names half translated:
  Jonathan → Ionatan, Sarah → Sara, Joseph → Iosif, but Joshua, Abigail kept: a family's own
  names should stay as they are. Paraschiva Procopoi → "Potaskiva Prokopoy" (see idea 14).
- **More (11:49):** "thanked God" → "îi mulțumea **pe** Dumnezeu" (should be "lui Dumnezeu");
  "in the fear of the Lord" → "în **temearea** Domnului" (not a word: "frica / temerea");
  "his kingdom" → "regatului Lui" again (see idea 9).
- **More (11:50):** "zeal" → "**zelo**" (Italian/Spanish; Romanian "zel"); "your many prayers"
  → "rugăciuni ale **voastră**" (should be "voastre").
- **More (11:51):** "When Dragosh passed" → "a trecut" (literal; "a plecat la Domnul" /
  "a decedat"); "their fourteen children" → "**cele** paisprezece copii" (should be "cei");
  another `<image|>` token (idea 8). His wife's name came out Patasgeba / Pateskiva (idea 14).
- **More (12:45):** "can ring loud" → "poate **răsună**" (should be "poate răsuna"); "he loved the
  Lord" → "el **i-a** iubit pe Domnul" (should be "l-a iubit pe Domnul").
- **(12:46, Deut 6:5):** "with all your soul" → "cu **toată** sufletul tău" (should be "tot
  sufletul"); "God is faithful" → "Dumnezeu este **fidel**" (church Romanian: "credincios").
- **(12:47):** "my dad" → "**tata mea**", "**tata meu**" (should be "tatăl meu" / "tata"), four
  times in one minute; two more `<image|>` tokens ("Ș<image|>ști", "Adev<image|>Ce").
- **(13:31, the younger brother's thanks):** "Dragosh **l-iubea** pe Domnul" (should be "îl
  iubea"); "a bigger heart than anyone" → "decât **cineva**" (should be "decât oricine"); the
  speaker's self-correction "sorry" → "**din păcate**" (unfortunately); "share Jesus" → "să îi
  împărtășească pe Isus" (literal). His opening ("Hello, my name is Claudiu, I'm Dragoș' younger
  brother") was dropped at the language switch (idea 12).
- **(13:32):** "He never gave up on us" → "El nu s-a **lăsat niciodată în jos** pentru noi"
  (word for word; "nu a renunțat niciodată la noi"); "This was God's timing" → "**Aceasta** a fost
  timpul lui Dumnezeu" (agreement). Also a 3-word "cu ce, și." flipped the app back to Romanian
  mid-English (idea 12's length floor).
- **Also try:** NLLB for EN→RO only (its Romanian grammar is clean; its weakness was RO→EN
  fragments), or a Romanian-checked word list; write dates and numbers as digits.
- **Change:** EN→RO prompt: ask for natural, grammatical Romanian with correct agreement,
  idioms translated by meaning; merge one-word fragments with the next phrase (raise
  `hold_min_words` for EN→RO); consider a short Romanian grammar pass if it stays fast.
- **Effort:** small (prompt); medium if a second pass.

## 14. Romanian names from the English recogniser: map back to Romanian spelling — `done` — stream D: English recogniser spellings mapped back to today's names

- **Evidence (11:47, English obituary):** the English recogniser spells Romanian names by
  sound and EN→RO keeps them: "Ragosh / Dragosh Puha" (Dragoș Puha), "Souchava / Suchava"
  (Suceava), "Vicovisus / Vigo Vogisus" (Vicovu de Sus), "Pua" (Puha).
- **(12:52, a mixed-language speaker):** Dragoș Puha → "Draguish Purha", "Dragosh Puha",
  "Dragoskuha" in three consecutive lines; Coban → "Kuban".
- **Change:** feed today's names (terms) to the English recogniser as keywords (already done
  for Whistle; check Parakeet, which is the current English recogniser), and add a fuzzy
  match from the English transcript to the terms list before translating (Dragosh → Dragoș).
  Add common places (Suceava, Vicovu de Sus, Botoșani…) to the spellings list.
- **Effort:** small.

## 15. Lists of names are dropped as "untranslated" — `done` — stream B

- **Evidence (11:47-11:48, the obituary's lists of siblings and children):** "Despra, Carmen,
  Dan, David, Silvia, Dir." and "Isaac, Emily, Michelle, Michael," were not shown
  (`mt_echo`): the output equals the input, which is right for names, so the echo filter
  threw them away. Those lists are what the family wants read out.
- **Again in Romanian (11:53):** "Despina, Carmen, Dan, David, Cecilia." and "Teodor,
  Daniela, Daniel, Claudiu." dropped the same way.
- **Change:** in `is_echo` handling, pass a phrase through when it is mostly capitalised
  words / names (or in the terms list) instead of dropping it.
- **Effort:** tiny.

## 16. Words repeated across chunk boundaries (English) — `done` — stream C

- **Evidence (11:47-11:49, English reader):** the end of one chunk comes back at the start of
  the next: "…May nineteenth, nineteen sixty-three." | "19, 1963, in Vicovisus…" | "Vigo
  Vogisus, Suchava…"; "In two thousand twelve" | "2012 they began…"; "…gave him praise" |
  "business." | "for the success of their business." The captions then say the same thing
  twice (once in words, once in digits).
- **Cause:** the English recogniser re-reads the previous seconds of audio as context
  (`context_s` 6 s) and the overlap is not always trimmed, especially for numbers written
  two ways.
- **Change:** before translating, drop a leading span that repeats the end of the previous
  chunk (compare normalised words, numbers as digits).
- **Effort:** small.

## 17. Don't caption garbled recognition: a confidence gate — `done` — stream B: `src/live/gate.py` (lexicon share, letter scraps, transcript disagreement)

- **Evidence (2026-10-24 11:59, the end of the obituary):** probably an English sentence (or
  a mixed one) recognised as Romanian gibberish, "Meid când ține tu blăstă pha familiei.",
  was translated as "**But when you carry the curse of the family.**" At a funeral. Also
  "revedere, empatia eternă" (recognised for "în patria eternă", the eternal homeland) →
  "eternal **empathy**"; "Bala" (a surname) → "Paul".
- **One-word scraps (all day):** "Adă." → "Add." (12:32), "Bala." → "Paul.", "F." → "Friends,",
  "A." → "A.", "îndată." → "immediately.": a single short word on its own is rarely worth a
  caption; hold it for the next phrase (the hold already exists for < 4 words but times out
  after 1.2 s) or drop it when nothing follows.
- **(12:46, mixed-language speaker):** Romanian said in English mode came out as "Neze was graved
  and celeste. Ashafus Dragosh." / "I show for Dragoș. Unnom Groudon Celeste." and was
  "translated" into equal nonsense; "mi-as fi tonic, T, GS, M." → "my son tonic, T, GS, M.".
  Strings of non-words and single letters are an easy signal.
- **Worst case today (12:49):** English ("He renounced. Certain deals came on his desk…") heard
  by the Romanian recogniser during a language switch became "I., domnule, bă, Ikva pe mii." /
  "Gay va polua. vis nou, băi, gay va polare, my.s Ts Millions." and was captioned "**He will
  pollute it. new vision, brothers, the gay will polarize, my.s Millions.**" Offensive
  nonsense in a eulogy. Signals that were all there: single letters, "my.s", mixed-language
  tokens, words not in a Romanian lexicon, and the multilingual transcript being clean English
  (it was dropped as empty at the same moment). This is the strongest case for this idea.
- **(12:54):** "a mănăstirii Dragoș, puha" (garbled) → "to the **monastery** of Dragoș" (an
  evangelical funeral); "Zichaichia Peromanesticamay" ("zic-o și pe românește", let me say it in
  Romanian too, heard by the English recogniser) shown as is.
- **(12:56, worse):** "Slavă lui Dumnezeu" said in Romanian while in English mode, heard as
  "slava ludum nezukum tum", was translated EN→RO as "**Slava jocurilor de hazard**" (glory to
  gambling); "nezurkamai sum tsoom" → "**Nu mă interesează**" (I don't care), invented. The
  translator should never be asked to translate non-words; a lexicon check (share of tokens
  that are real English/Romanian words) would have stopped both.
- **Change:** drop or hold a phrase when the recognition looks unreliable: low decoder score
  (the CTC beam has one), many non-words, or the Romanian and multilingual transcripts of the
  same audio disagree completely. A missing caption is better than a wrong one, especially
  with words like curse/death/hell appearing out of nowhere.
- **Effort:** medium (expose the decoder score from the engine; tune on these two services).

## 18. Songs: don't caption garbled lyrics — `done` — stream C: Song key; automatic detection available but off (no measurable gain on the bench) · 2026-10-25: an «odd capitalised words» gate was tried and dropped (it passed the garbled song, "Baba" is a known name, and blocked a real list of names)

- **Evidence (2026-10-24 12:34, a duet):** sung Romanian is recognised badly and the
  translations are nonsense or misleading: "Ale, vorbește Dumnezeu!" → "But speak, God!";
  "înduială casei tale!" → "the foundation of your house!" (probably "Rânduiește-ți casa",
  Isaiah 38:1, set your house in order); "către ță cea din vale" → "to your valley". The level
  rose to -5 dB (singers and accompaniment), near clipping at +15 dB gain.
- **(12:35-12:36):** "îi naci în voane" → "was born in swaddling clothes", "îmbete" → "**drunk**",
  "g pecete." → "seal.": invented or misleading words; only some lines ("the king turned back
  weeping", "they prolonged his life") make sense.
- **(13:15, congregational song):** the recogniser even produced **Portuguese** ("Eu cuidarei
  isso, vou virar de mim" → "I will take care of that, I am going to turn away from it") and
  "spre rai străneie" → "to a **strange** heaven". Chorus lines were fine; verses garbled.
- **Change:** detect singing/music (sustained pitch, level, the voice detector's music cases)
  and show a ♪ or nothing while it lasts; or a one-key "song" toggle for the operator that
  holds captions. Lyrics, if wanted, belong on ProPresenter's own song slides.
- **Effort:** small for the toggle; medium for detection.
- **Garbage through the gate (2026-10-25 10:14:30-10:15:30, a song about sheaves/harvest,
  song detection off):** ~12 phrases like "snopul strâns la vederea lui, da, sav pe nasul
  Barta", "tăiește spinile Zodra nasul de la Baba, vai, tenes", "cumperi ta, Zgaal" all passed
  the confidence gate (real Romanian words mixed in) and became "Sav on Barta's nose", "cut off
  Zodra's spines from Baba's nose", "buy it, Zgaal, I still need you with me". Only hidden
  from ProPresenter by luck. **Change:** turn automatic song detection on for a bench run on
  this clip; add to the gate an "unknown capitalised words" count (Barta, Zodra, Baba, Zgaal,
  Balba, Shor: not in today's names, the lexicon or the Bible names) — 2+ in one phrase or
  over consecutive phrases = garbled.
- **(10:34:27, the gulag song sung partly in Russian):** "eu zic sa te gaspoddi, mă iubi mai
  ucite", "Upavanie mai pac" (Господи, Упование) went through as Romanian. A third language
  the recognisers don't know; the same unknown-word count would catch it.

## 19. Automatic gain: level each speaker to the ideal volume — `done` — stream F: on by default (bench: 26/52 vs 21/52 at a fixed +15 dB)

- **Evidence (2026-10-24 service, speech level per phrase):** -41 dB (first speaker, gain +22,
  before the operator lowered it to +15), ~-30 dB (English obituary reader), -12/-10 dB
  (video greeting), -5 dB (the duet, near clipping). One fixed gain cannot suit them all; the
  operator had to ride the knob.
- **Change (engine, capture side):** keep a running median of the level over the last ~20 s
  of frames the voice detector marks as speech; move the gain toward a target (−20 dBFS, the
  middle of the meter's good band) by at most ~1 dB/s; freeze during silence (no noise
  pumping); a fast limiter cuts the gain at once when peaks pass −1 dBFS; stay within
  −12..+30 dB. Report the gain in the `level` events so the knob follows it; an "Auto" switch
  beside the knob (manual knob = AGC off). Log it per phrase (gain_db already is).
- **Limits:** app gain does not improve signal-to-noise (the mixer send does); the gains are
  steadier speech detection, no clipping and a steady level for the recogniser. Singing counts
  as speech (pairs with idea 18).
- **Check:** replay today's service audio, if recorded, or a rehearsal with level changes;
  compare speech_db spread and empty/garbled phrases before and after.
- **Effort:** medium (engine controller + UI switch + tests).
- **Slow to come back after a long quiet (2026-10-25 09:39, Quality; the user noticed it too):**
  after the worship group and ~10 min of near-silence, the next speaker started at gain
  **-9.8 dB** (pulled down by the singing), speech **-54 dB** in. It climbed ~3.5 dB per
  phrase: -9.8, -6.5, -3.0, -1.0, +3.1, +6.9 → speech only reached -32 dB after ~25 s (target
  -20). The first 3-4 phrases went to the recogniser 30 dB too quiet. Cause, `AutoGain` in
  `src/live/engine.py`: after the pause the 20 s window is empty, so it waits 2 s of speech,
  then moves at `rate_db_s` 1 dB per second *of speech*, the same rate up as down.
  **Change:** (a) after a pause longer than the window (or when the error is > 10 dB), jump
  most of the way at once on the first 2 s median (the limiter already catches a peak on
  the very frame); (b) or a faster rate up while the error is large (e.g. 4 dB/s above 6 dB
  error, 1 dB/s near the target); (c) don't let singing pull the gain down for the next
  speaker: freeze or ignore frames while a song is detected/keyed. Small; bench on the
  09:39 clip (mark below) and a quiet-then-loud rehearsal.

## 20. Cut phrases by words, not seconds, for slow speakers — `done` — stream C: `phrase_min_words` 6 in Best, no phrase ending on a preposition · 2026-10-25: a one-word echo of the previous phrase's last word is dropped (`echo_word`)

- **Evidence (2026-10-24 12:43, an emotional speaker with long pauses):** "dragă comunitate
  din." | "Atlanta, frați și surori…" | "aduc condoleanțe din." | "partea familiei mele…" |
  "dragă familie Puha, iubit." | "iubiți, copilași…": 3-4 word pieces ending on a preposition,
  even with 3.5 s minimum phrases, because most of those seconds are pauses. Each piece is
  translated alone ("offer my condolences from.") and the line reads chopped.
- **Change:** besides min_commit_s, require a minimum number of *words* (e.g. 6) before a
  phrase goes to the translator, and never end a phrase on a preposition/conjunction
  (din, de, la, în, cu, pe, și, că, care…) unless max_commit_s is reached; the hold for short
  fragments (hold_min_words 4, hold_s 1.2) is too short for slow speech.
- **Effort:** small (pipeline hold logic + tests); watch the delay.
- **Still happens (2026-10-25 09:09:34, Quality, after the fix):** "…pentru cele trei familii."
  then "famili." alone → its own caption "families." A one-word echo of the previous phrase's
  last word (the speaker repeating, or the recogniser re-hearing the tail) passes as a phrase.
  Drop a ≤ 1-word phrase that repeats the end of the previous one, or merge it forward.
  Again 10:55:32 (on ProPresenter): "…experiențele mele trecute." then "trecut." alone →
  "past." on its own line. And 11:16:53: "…învățătoare de școală duminicală." then
  "dominicală." → "Sunday school." alone. Three times today, always the last word echoed.

## 21. Smoother caption flow: reveal words at speaking pace — `done` — word-paced reveal + smooth roll-up (2026-10-24); the display window also lays out its own lines

- **Measured (2026-10-24 service, 932 captions):** the screen changes in jumps: an update every
  4.4 s (median; 10% under 1.6 s, 10% over 7.4 s), each adding ~45 characters at once (about a
  full line; 14% add 2+ lines in one go). Overall rate ~88 words/min, so nothing is too fast
  on average; it is the *bursts* that feel jumpy.
- **Change 1, word-paced reveal (main idea):** when a translated phrase arrives, show the first
  few words at once (no extra delay) and reveal the rest over about the time the phrase took to
  say (`audio_s`), at ~2.5-3 words/s, in 2-3 word steps. If the next phrase arrives first, speed
  up and finish. ProPresenter gets ~1-2 updates/s instead of one jump every 4 s; the eye follows
  a steady stream, like a live stenographer. Lives in `output.py` + `captions.py` (the pacer
  timer is already there).
- **Change 2, smooth roll-up on the captions display window:** when a line leaves the top,
  slide the text up over ~250 ms (CSS transform) instead of a hard jump. Only for the
  display window/page; ProPresenter shows whatever text it gets.
- **Change 3, dwell by reading time:** keep each line up at least characters/17 s (reading speed)
  rather than a fixed 2.5 s, so long lines get more time and short ones don't hold things up.
- **Related:** idea 20 (no phrases ending on "din/de/și") removes the "half sentence, pause,
  rest" pattern that also reads as jumpy.
- **Check:** replay today's log through the caption buffer with and without reveal and count
  screen changes, max characters added per change, and the delay of the last word.
- **Effort:** small-medium (reveal timer + tests); display animation small.

## 22. Gemma's notes to the reader: cut them, keep the translation — `done` — stream A

- **Evidence (2026-10-24 13:34):** "and to die is gain. Dragur says run his race." (Philippians
  1:21, in a eulogy) → "și a muri este profit. Dragul spune să-și<image|><image|>*(Note: The
  source text "Dragur says run his race" appears to be a fragment or a specific name/reference.
  If "Dragur" is a name, the translation is: …)*". The over-long filter rightly refused it, so
  the whole line, including the verse, was lost. At 12:54 a one-letter phrase "T." came back as
  two full sentences from the *context* (the previous chunks re-translated); also dropped.
- **Change:** in `MLXLLMTranslator.translate`, cut anything from "(Note", "*(Note", "Note:",
  "Translation:" onwards, then the token cleanup (idea 8), then the filters; and don't send
  1-2 character fragments to the translator at all (idea 17).
- **Effort:** tiny.

## 23. Meaning slips in Quality: a dropped word, a misread time phrase, a wrong person — `idea` · 2026-10-25: idiom rows (de două zile, după-masa, ani de zile, te rog frumos = please); the coverage check (b) not done

- **Evidence (2026-10-25, Quality, sermon on the ten virgins; marked in the recording):**
  - 09:09:24 "Celelalte nechibzuite ce spune Biblia, plecați că nu vă cunosc." → "The others, as
    the Bible says, depart, for I do not know you." **nechibzuite** (the foolish ones) dropped:
    the key word of the parable is lost. Translator.
  - 09:09:49 "Domnul Isus a plecat de două zile." → "the Lord Jesus has departed for two days."
    Should be "left two days ago" (the speaker's two-millennia "days"). The Romanian "de + time"
    = "since/ago" is a known trap. Translator (prompt/idiom row).
  - 09:09:52 "…cum am înviat din morți a treia zi." → "as I rose from the dead". Almost
    certainly the recogniser hearing "a înviat" as "am înviat"; the translator faithfully made it
    first person. Recogniser, but a translator rule could catch it: Jesus/God as the subject of
    the sentence + a 1st-person verb with no quote → read as 3rd person.
  - 09:50:56 "să ne rugăm ca Dumnezeu din ei să scoată. | vase de lucrare, … oameni care să
    se folosească D[umnezeu de ei]" → "that God may **take them out**." | "… people who will
    **use God**.": the cut between "scoată" and its object turned "raise up from them vessels"
    into "take them out", and the cut-off end made God the object. Segmentation + translator;
    Gemma continuing the previous piece (idea 4) didn't fix the first half, already shown.
  - 10:33:39 "dacă într-o localitate **după** cimitir, **după** vorbă, poți să înțelegi cine au
    trăit acolo" (judging **by** the cemetery, by the speech…) → "**after** the cemetery. after
    the word": "după" = by/according to, not "after", when no time is involved. Idiom row.
    Also 10:56:08 "până după masa" (until the afternoon; "după-masa" = afternoon) → "until
    after the meal". Idiom row: după-masa / după-amiaza = afternoon.
  - 10:54:28 (on ProPresenter) "nu-i mai bine ca să. vină o dată pe an sau de două ori sau de
    trei ori pe an" → "wouldn't it be better if it came once a year **or does all these things,
    twice, yes three times, with a man,** per year": words with no source at all, in a long
    (12 s) phrase. 10:54:43 "nu-i mai bine casa în bani" (likely "cu bani") → "**turn the house
    into money**". Translator inventing; a length/word-coverage check (b) would flag the first.
  - 2026-10-25 evening 19:34:26 (paused, not shown; marked) «Suntem scutiți de ispite, Suntem
    scutiți de provocări. | nu sunt ele la tot pasul» — spoken as rhetorical questions ("Are we
    spared temptation? Aren't they at every step?"), recognised with full stops: translated as
    written it says "We are exempt from temptations", the opposite of the sermon. The recogniser
    doesn't mark questions; a rule could: a «Suntem/Sunt/Nu sunt … ?» clause followed by a
    contradicting clause (nu…, ba da) → keep the question mark. Hard in general; note only.
  - 2026-10-25 evening, shown: 18:12:05 «după masa aceasta **pentru cei mai mulți**, de prima
    dată pentru unii» (this afternoon for **most**, the first time for some) → "in the afternoon
    **for many more**". 18:16:21 «Sora Pina… vă zice **bun venit**» recognised «vă zice bun
    păstor» → "the good pastor says". 18:16:49 «va intona **un imn**» recognised «unim» → "will
    play **in unison**" (Gemma made sense of a recogniser slip).
- **Change:** (a) idioms row for "a … de N zile/ani" → "N days/years ago" / "for N days" by
  tense (perfect + de = ago); (b) a coverage check like the number/negation guard (idea 5):
  Romanian content words (adjectives/nouns) with no counterpart in the English trigger one
  re-translation; (c) an "am"/"a" before a past participle, when the subject is God/Jesus,
  fixed before translation.
- **Effort:** (a) tiny; (b) medium (needs a RO→EN word map or Gemma self-check, watch latency);
  (c) small but risky (false fixes when the speaker really speaks in the 1st person). Bench on
  the marked clips.

## 24. Translation got slower with the richer prompt: cache the fixed part — `done` (2026-10-25): prompt-prefix KV cache, median 2.18 → 1.68 s on 14 phrases, identical output

- **Evidence (median, Quality, same Gemma model):** 2026-10-24 service (11:31, before the
  14:33 prompt commit): translation **1.46 s**, first word on screen **6.35 s**. 2026-10-25
  09:05 / 09:28 logs: plain translation (no guard, no verse) **2.5 s / 1.86 s**, first word
  **7.6 s / 7.2 s**. Per word of output 0.19 → 0.20 s, so it is not longer answers: it is the
  longer prompt (previous pairs, idiom rows, names, Scripture hint) processed again for every
  phrase. On top: the negation/number guard re-translates (5 phrases at 09:05, 2.4-5.8 s each),
  and phrases are a little longer (median 8 → 10 words, audio 3.65 → 3.7-4.2 s: word holds).
- **Change:** (a) keep the fixed instructions + glossary/idioms as a cached prefix (mlx-lm
  prompt cache: compute once per service, reuse the KV cache; only the context pairs and the
  phrase are new); (b) send only the idiom/glossary rows that match the phrase (already done
  for some? check); (c) context pairs 2 → 1 if (a) is not enough; (d) run the guard's retry
  only when the first answer differs in negation *and* the phrase is not a question tag.
- **Effort:** (a) small-medium, measure on the bench (mt_s and the error list); (b-d) small.
- **Measured after the change (bench, 4 clips at 1×, old vs new code, 46-47 phrases):** first
  word on screen median 7.57 → 7.66 s (no change within noise), 90th percentile 15.8 → 11.2 s;
  translation median 2.44 → 2.26 s, 90th percentile 7.1 → 4.7 s. The cache's gain is partly
  spent on the longer prompts (notes, more glossary rows) and the extra guard tries; recognition
  varied ±0.15 s between runs (not the new phrase boost: the same without it).

## 25. A Romanian word cut in two at a piece boundary is translated as two words — `idea`

- **Evidence (2026-10-25 evening, Quality; marked in the recording):**
  - 18:11:09 "…vrea să ne păzim și noi de **auto**." | "**autoînșelare**. Ne putem înșela…" →
    "…protect ourselves from **the author**." | "self-deception.": the recogniser closed the
    piece in the middle of «autoînșelare», heard the first half as «autor», and the translator
    made a word of it. The next piece repeats the whole word, so the meaning is there once the
    cut-off half is dropped. Segmentation + recogniser.
  - 18:09:50 / 18:10:02 "din cartea **omului.** | Dumnezeu, Iacov" (din cartea omului lui
    Dumnezeu Iacov = from the book of **the man of God** James) → "from the book of **humans**"
    and again "the book of humans, God James": the cut fell inside «omului lui Dumnezeu», «lui»
    was lost, and neither half makes sense alone. Segmentation; a phrase row would also help
    (omul lui Dumnezeu = the man of God, the Romanian way to announce a Bible writer).
  - 19:32:36 again, same sermon: "…că poate să fie și **auto.**" | "**autoînșelare**. Și
    apostolul…": the same word cut at the same place, this time left as «auto» (paused, not
    shown). A cut word repeated in full at the start of the next piece is a reliable pattern.
  - More from the same service, shown: 18:16:30 "…împreună cu **Sf.**" | "soția lui" →
    "together with the **Holy**" | "wife" (a cut «S[ora]…/so[ția]» read as «Sfânt»);
    18:16:58 "fratele Mitică **Maf**" | "**Maftean**, și…" → "brother Mitică Maf" | "**tean**, and
    may he…" (the name split, the second half shown alone); 18:15:49 "**înați.**" → "ahead." (a
    re-heard tail of «înainte», a caption of its own: `echo_word` misses it because the
    fragment isn't the whole word).
- **Change:** (a) when a piece ends on a word that the next piece's first word starts with
  (autor / autoînșelare), or on a non-word fragment, drop that last word before translating and
  let the next piece carry it (like idea 16's English repeat check and idea 20's `echo_word`, the
  other way round); (b) don't close a piece after a genitive noun that usually takes «lui +
  name» (omului, cartea, Cuvântul) when the next word comes within ~0.5 s; (c) glossary row
  «omul lui Dumnezeu» → "the man of God", «cartea omului lui Dumnezeu X» → "the book of X".
- **Effort:** (a) small (string check on the last/first word, both are in hand before the
  translation); (b) small, segmentation rule, watch delay; (c) tiny. Bench on the marked clips.

## 26. Name repair turns a real word into today's name: «înșeală» → «Ionela» — `done` (2026-10-25): a lower-case word with no cue needs sound ratio ≥ 0.85 and is never a name after nu/ne/te/se/să…; bench on all 14 logs with the saved names: 12 → 6 repairs, the 6 false ones gone (Ionela ×4, Barbu, Dragoș), the 6 real kept

- **Evidence (2026-10-25 evening, Quality, on ProPresenter; marked):** 18:11:45 the recogniser
  heard it right, «care nu **înșeală** niciodată, Dumnezeu nu înșală, Duhul Sfânt nu **înșeală**»
  and «Evanghelia nu o să ne **înșeală** niciodată», and `names.repair_ro` rewrote both to the
  name «Ionela» (log `names_fixed: [["înșeală", "Ionela"]]`): on screen "which **Ionela** never,
  God does not deceive, the Holy Spirit, not **Ionela**" and "the Gospel will never **Ionela**
  us." A third time at 19:32:08 (paused) → "if someone like Ionela Rus". In a sermon about
  self-deception, the key verb became a woman's name. Also «tros» → «Dragoș» (19:44:00, «trosnet»
  context). Name repair, not recogniser or translator.
- **Cause:** a lower-case word not in `data/lexicon/ro.txt` is matched fuzzily against today's
  names. The lexicon has «insala» but not «inseala» (the common spoken form of «înșală»), so the
  verb counted as unknown, and «înșeală»/«Ionela» are close enough for `_ro_match`.
- **Change:** (a) add the verb's forms to the lexicon (înșeală, înșală, înșeală-te, înșelat, …)
  and rebuild it with the Romanian verb forms seen in the logs' translator output; (b) in
  `repair_ro`, an uncued **lower-case** word never becomes a name unless it is an exact
  case-fold of it (keep 'paraschiva' → 'Paraschiva'; drop fuzzy lower-case hits like
  înșeală → Ionela); (c) never replace right after a verb clitic or negation («nu», «ne», «te»,
  «o să», «se»): a name doesn't go there. Bench: the four logs used for idea 2 (35 fixes, 0
  false before) plus this one; (b) may lose a few real fixes like 'familia pha' (cued, so still
  allowed).
- **Effort:** small: (b)+(c) are a few lines in `src/live/names.py` with tests; (a) a lexicon
  rebuild.

## 27. Bench on the corrected sermons: the translation work since Sat 10-24 14:00 made Quality worse — fixes 1-3 `done` (2026-10-26), gap to Sat 14:00 remains

- **Setup (2026-10-26):** br-random-1 (29 min, all corrected) + nelu-suciu (first 8 min corrected),
  5,588 words of corrected Romanian transcript, replayed through each checkpoint (`scripts/bench.py run --code`,
  gain 0, x3, no `--terms`). English translation judged blind (a model judge, A/B shuffled, ~20 s windows,
  against the corrected Romanian transcript; the decisive cases checked by hand). Two runs of the same code
  differ in 15 windows (the noise floor). Runs: `logs/bench/runs/cmp-*`.
- **Overall:** c10a191 (Sat 14:00) vs now: English translation 31 wins to 14 (47 ties; major 8–2) for the old
  code; Romanian transcript WER 5.85 % → 6.05 %.
- **By step.** For each step: in how many ~20 s windows the English translation got better or
  worse *because of that step* (blind judge; the rest no clear difference), and serious errors
  (wrong/reversed meaning, invented or vulgar words, dropped verses) the step fixed or introduced.
  Steps with fewer than ~20 differing windows are within the noise floor (15).
  | step | windows changed | better | worse | no clear diff. | serious fixed | serious introduced | verdict |
  |---|---|---|---|---|---|---|---|
  | guards + gate (76d320f, f886409) | 11 | – | – | – | – | – | no measurable effect |
  | **prompt (2c51583)** | 92 | 13 | **18** | 61 | 0 | **5** | **hurt** |
  | phrase holds (bd79bb2) | 35 | 3 | 2 | 30 | 1 | 0 | no effect (the first "hurt" was a windowing artifact, see below) |
  | **verse captions (17854ae)** | 24 | 4 | **9** | 11 | 0 | **3** | **hurt** |
  | names (de15ea2; no names given) | 17 | 1 | 3 | 13 | 0 | 0 | no measurable effect |
  | auto gain on (42e113d) | 71 | 18 | 14 | 39 | 3 | 3 | mixed; Romanian transcript +12 errors |
  | review fixes (1d7dd5a) | 16 | 3 | 8 | 5 | 1 | 1 | no measurable effect |
  | **Sunday fixes (6fc52e4)** | 73 | 10 | **14** | 49 | 1 | **2** | **hurt** (negation fallback) |
  | *all, Sat 14:00 → now* | 92 | 14 | **31** | 47 | 2 | **8** | **worse overall** |
- **Causes seen:**
  - Prompt: the previous translations pull Gemma along: «pierzi premiul ceresc» → "you **receive**
    the heavenly reward"; «Policar» → "**Paul**" (named just before); the older prompt fixed the
    recogniser's «mărește-necredință» to "increase our faith", the new one writes "increase our
    **unbelief**". The glossary row «Vicovu de Sus» (added with the prompt) is pulled in by the
    plain word «sus»: "it leads into **Vicovu de Sus**".
  - Phrase holds: content dropped or chopped at the holds: «fii credincios până la moarte»
    (Rev 2:10) and «dincolo de moartea asta Hristos ne așteaptă» missing.
  - Verse captions: the spliced published text breaks the sentence: "says, I I have fought the
    good I am exhausted", "you were with me my body and bring it into submission", 2 Tim 4:7 split
    "the good | fight." across captions (near matches at score 0.7–0.8 replace the speaker's words).
  - Negation fallback (3 uses, 2 bad): «frică sfântă, nu cumva să greșim» ("lest") → NLLB "**Holy
    shit**, let's not… lose this sport"; recognition slip «Vă nu salută» → "It does not greet you".
- **Change (in order of how much harm each fix removes for its effort):**
  (a) **Negation check:** treat "lest" / «nu cumva» as a negation, and stop the NLLB fallback from
  replacing Gemma's English translation over a negation mismatch alone (show Gemma's first
  answer). Removes "Holy shit" and "It does not greet you". Tiny; small risk: a genuinely dropped
  "not" stays dropped, as it did before Sunday.
  (b) **Prompt:** keep the previous *Romanian transcript* pieces as context but take out Gemma's
  previous *English translations*, and match multi-word glossary names only as a whole («sus» no
  longer pulls in «Vicovu de Sus»). Aims at "receive the reward", "Paul" for Polycarp,
  "unbelief". Small to change; must be benched, since previous translations also helped cut
  sentences read on.
  (c) **Verse captions:** use the published verse only when the whole piece is the verse and the
  match is strong (score ≥ 0.9); otherwise translate normally. Removes the broken splices
  ("I I have fought the good I am exhausted"). Small.
  (d) **Phrase holds:** find why a held piece sometimes never reaches the screen ("be faithful
  until death" missing) and make sure it always gets flushed. Needs investigation first.
  Re-bench each on these two sermons (blind A/B as above).
- **Windowing artifact (found 2026-10-26):** the first judging cut fixed 20 s windows and put each
  caption in the window of its midpoint; phrase holds make longer captions, so a caption landed in
  the next window and the one it started in looked "dropped". Re-judged with windows cut only
  where both runs have a caption boundary (`blind3.py` in that session): phrase holds 2 – 3 with 30
  ties, Sat 14:00 vs now still 31 – 17 (major 10 – 4). Fix (d) needed no code change.
- **Fixes done (2026-10-26):** (a) `guards.py`: "lest" is a negation; NLLB is asked only for numbers
  or a "not" the translation invented, never for a source "not" it lacks. (b) `translate.py`: the
  previous pieces go into the prompt as Romanian transcript only, not Gemma's English translations;
  `names.mentioned`: a name with a small word («Vicovu de Sus») only as a whole. (c) `verses.py`:
  the published verse only when the whole piece is the verse and the match is ≥ 0.85 (`shown`),
  held pieces translated. Plus `guards.split_ne`: «mărește-necredință» → «mărește-ne credință»
  (added after the bench run, not in it).
- **Result (blind, fair windows):** fixed vs now: **29 better, 19 worse, 52 same; serious errors
  7 fixed, 0 added** ("Holy shit", "It does not greet you", "Vicovu de Sus", "receive the reward"
  gone). Fixed vs Sat 14:00: 14 better, 30 worse, 56 same; serious 0 fixed, 4 added (garbled names
  Sandurcal/Destina, 1 Cor 9:27 garbled, "unbelief" — now repaired by `split_ne`). The rest of the
  gap is small wording slips; candidates: auto gain (Romanian transcript +12 errors; the garbled
  names come with it) and the remaining prompt additions (idioms, notes, Scripture hint).
- **Variants (2026-10-26, fair windows; "fixed" = fixes a-c + `split_ne`):**
  | comparison | better | worse | same | serious fixed | serious added | Romanian transcript WER |
  |---|---|---|---|---|---|---|
  | fixed + auto gain off, vs fixed | 18 | 12 | 39 | 6 | 1 | 6.19 % → 5.94 % |
  | fixed without idioms/notes/Scripture hint, vs fixed | 7 | 12 | 26 | 0 | 2 | 6.19 % → 6.14 % |
  | fixed + auto gain off, vs Sat 14:00 | 11 | 21 | 69 | 2 | 2 | 5.85 % → 5.94 % |
  | (noise: fixed vs fixed rerun) | 8 | 9 | 27 | 1 | 1 | |
  Keep the prompt additions. Auto gain hurts on these recordings (clean, speech ≈ -28 dBFS at 0 dB):
  the garbled names, "Holy Mary", "the Bed of God" come with it. With auto gain off the serious
  errors are level with Sat 14:00; Saturday still reads slightly better in small wording (21 vs 11
  windows). Auto gain was chosen on the live feed of 10-24 (26 vs 21 of 52 known errors): before
  changing its default, bench it on a live recording (e.g. tonight's clips) too.

- **Auto gain stays on (decision 2026-10-26):** the services so far took the audio from every mic,
  crowd and room mics included, so the recogniser heard room reverb. From the next service the
  app gets its own feed: the speaking mics only, no effects, summed to one channel. Auto gain is
  judged again on a recording of that feed before its default changes.
