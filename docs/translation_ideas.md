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
- **More evidence (2026-11-01 09:13:07, log `2026-11-01_090734`, paused, marked):** the prayer
  list «familia [A], … sora [B] și sora [C], [first name]. Haideți…» came out
  as «sora [C], **[first name]aideți** împreună»: the name and the next word glued (the «H»
  of «Haideți» swallowed), so neither the name repair nor the glossary would match it. Today's
  names were not entered (Names & words empty); the family names are unchecked ([A]?
  [C]?). The glue is a recogniser artefact: a name followed by a vowel-initial word.
  Same service: 09:22:56 «slăvit să fie Domnul **Domnului**» (the boosted phrase plus a stray
  word; again 10:57:15 «lăudat să fie Domnul **lui Dumnezeu**» → "praise the Lord of God"), 09:22:26 «**einerarul**» (itinerarul, the start cut off), 09:23:13 «Târgu Jiu, de la
  **Rovare**» (Rovinari?), 09:21:40 «județul **Cluj-gădej**» (Cluj, la Dej?; marked). Place
  names could go in Names & words like people's names.
  Announcements 10:23:33-10:24:24 (shown, marked) are the clearest case for a standing list of
  the church's own names: «[local park], [local town]» → «la **[garbled]** … **[park misspelt]**, în
  **[town misspelt]**»; «fratele [first] [surname]» → «fratele [first]. **[surname]** și…» → "brother [first]. **join
  us**" (the surname translated as a verb); an English event name came out as «CRM 2020 …
  2027. Aur Gold. Goldh, Age, în Dallas» (unreadable); and «la părtășie» heard «părtăși» →
  "at the **defense**". A **standing** names list (local places, a park
  Hill Park; members; ministries), not only today's names, would fix most of these.
- **More evidence (2026-11-08 morning, log `2026-11-08_090143`, P2): the same announcement words
  as on 11-01, misheard the same way.** 11:08:35-11:08:49 «Samaritan's Purse, Operation Christmas
  Child, shoebox-urile» → "**Ser Spars. Stens Spst, Aon Christmas Child, The Shubaks Ghes**";
  «în **hallway** B3» → "in **Hollywood** B3" (11-01: "Hollywood" too); «balanța la **loan**» →
  "the balance at **Lon**" (11-01: «Lan»); 10:57:58 «first baby» → "the **Forst Baby** family";
  11:06:13 «Fund raser»; 11:06:53 the group «Talantul în negoț» → "the talent for business".
  A standing list (fundraiser, bake sale, hallway, loan, shoebox, Samaritan's Purse, Operation
  Christmas Child, B3, the church's group names) for the decoder boost and the glossary would end
  the repeats; they come every few weeks.
- **Seen again after done (2026-11-15 morning, logs `2026-11-15_081707` / `_094327` / `_102255`, P1): a standing names list would have fixed the guest's home.** «<city>, **<US state>**» heard as a Romanian woman's name 3× (10:25:30 "brother <surname> comes from **<name>**", 10:42:23 "the church in <city>, <name>", 11:41:32 "<name>'s pineapple"); Bible-name and people's-name slips are under idea 6. Places the guests come from could go in Names & words with the guests' names.

## 3. Bible references: show the published verse — `done` — stream D: announced references tell Gemma the verse (step 1); stream H: verses recognised by their words are captioned with WEB / Cornilescu (step 2) · 2026-10-25: paraphrase not spliced, short verse words held, a verse matched one piece late redoes the previous caption, trailing mis-heard verse words dropped · 2026-11-09: no verse twice within ~30 pieces, tail completed, «N cu M» · 2026-11-15: a verse matched one part late replaces it, caption ends at the speaker's break, «N cu M» across a stop or a piece

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
- **Verse pre-empted (2026-11-01 10:01:07, shown, marked):** after "1 Corinteni 11 … începând
  cu versetul 23." the next words were the preacher's own, «să fie cu luare aminte la Cuvântul
  Domnului» (let us pay attention to the Word of the Lord), but the caption was "**for I
  received from the Lord...**": the verse's opening, which the next piece then gave again in
  full. Announced-reference mode let Gemma put the verse in place of a phrase that wasn't it.
  Check: a piece shares few words with the verse → translate it, don't use the verse. Same
  passage, good: v. 23-24 then came out in the published wording. Also: the recogniser's
  mid-phrase periods carry into the caption ("God honors. our desire", "Scripture to. 1
  Corinthians"); drop a period followed by a lower-case word.
- **Same sermon, 10:45:05-10:45:09 (shown, marked):** the preacher retold v. 23-24: «în noaptea
  în care a fost **vândut**» → "on the night he was **sold**" (the verse's "betrayed" wasn't
  used: the retelling missed the verse match), then «El a luat o pâine. și după ce a mulțumit…»
  → "He took a loaf of bread. **took bread.** When he had given thanks, he broke it": the
  verse spliced in after the paraphrase, so "took bread" twice. When the verse is spliced,
  drop the paraphrase's matching words. Glossary: «vândut» near «noaptea» + Isus = betrayed.
- **More evidence (2026-10-30 rehearsal replay, logs `2026-10-30_172253` Best / `_172815`
  Quickest, P1):** Scripture read in pieces and not matched came out wrong: 17:27:52 John 15:2
  «pe orice **mlădiță** care este în mine» → "in every **young woman** who is in me" (Jesus'
  words; the next piece matched the verse, "Every branch that bears fruit"); 17:27:46 «adevărata
  **viză**» [viță] → "the true vine" (fine in Best) but Quickest 17:33:05 "the real **visa**",
  «vierul» → "**The owl**"; 17:25:25 John 6:32 «pâinea din **germana**» [din cer, mana?] → "the
  bread from **Germany**"; 17:24:50 «în **număr**, capitolul 21» (Numeri = Numbers 21) → "in 21"
  (book dropped; Quickest "in the number"); 17:25:47 «de 11 ori este scris» (written 11 times)
  → "chapter 15, **verse 11**" (an invented reference); «Moise și la **Ron**» (Aaron) → "Ron".
  Book names said in the plural/inflected form (Numeri/număr) and well-known verse words
  (mlădiță, vier, viță) could feed the verse matcher's book table and the decoder boost.
- **Seen again after done (2026-11-08 morning, log `2026-11-08_090143`, P2): verses doubled or
  spliced 10 times** (the full list is in "Rest of Sunday 2026-11-08" below). EN→RO: 10:34:30 Luke 7:47
  «…păcatele ei, **De aceea îți spun: Păcatele ei**…»; 10:43:08 Eph 4:30 «pecetluiți pentru ziua
  răscumpărării» twice; 10:45:24 John 8:11 NLLB's line then the Cornilescu with a stray «”)». RO→EN:
  11:43:06 Prov 27:18 "who takes care of a fig tree. Whoever tends the fig tree shall eat its fruit.
  **He** And there is"; 11:53:00 Luke 24:17 "What kind of words are these that to them, "What are you
  talking about"; 11:54:02 Luke 24:21 "we were hoping, But we were hoping"; 11:58:43 Phil 4:6 "Do not be
  anxious about anything, In nothing be anxious". Friday 2026-11-06 too (19:56:57 Mark 6:41, 20:38:36
  Ps 23:4). Missed matches that left Scripture wrong: 09:26:42-48 Luke 17:20-21 → "will not strike
  it… they are in joy"; 10:58:41-43 Gen 22:17 «sămânța ta va umple pământul» → "**a little land**".
- **Seen again after done (2026-11-08 evening, log `2026-11-08_173244`, Best, P2; clone run started 17:32, so probably with the afternoon's 40 % rule):**
  verse words still shown twice or as tails, ~10 times: 18:54:21 «cum mi se poruncise» → "**and in the evening my wife
  died.** So I did in the morning…" (Ezek 24:18 repeated from the line before); 19:40:55 "**to be moved. He who keeps
  you** will not slumber" (Ps 121:3 repeated 36 s later); 19:49:52 "**as the psalmist says.** “Be still…" (repeated);
  20:02:28 "**the elders obtained approval.** By faith…" and 20:02:50 "even though he is dead. **being dead, still
  speaks**" (Heb 11); 18:54:06 «picioare nuț» → "**feet yet.**" (a stutter got a verse fragment). Isaiah 8:18 caught one
  piece late ("**me** are for signs…", 18:52:49). References split at a piece end stay unconverted: 18:48:53 «[Matei 24] /
  cu 3» → "**with 3**", 20:04:18 «Proverbe 12. / 24 cu 10» → "**24 with 10**" (Prov 24:10). **Change:** remember the
  last captioned verse span and never caption it again within ~2 min unless re-announced; a verse fragment under 4 words
  needs an announced reference; map «X cu Y» to X:Y also when X ends the previous piece. Effort: small.
- **Done 2026-11-09 (branch `fix/2026-11-08-evening`):** a verse shown in the last ~30 pieces is not captioned again (unless the reference is announced again); when a verse match ends 1-3 words before the verse's end, the caption takes the verse to its end and those words are dropped from the next piece; «N cu M» after the book = chapter N verse M, «cu M» after an announced chapter = verse M; a fragment under 4 words is captioned only inside the announced reference. **Bench** (19:13:55 clips, 39 marked errors): still wrong 17 → 16 (Heb 11:2 tail no longer repeated; Proverbe 24 cu 10 still uncertain, the replay starts after the book was named); luigi-mitoi chrF++ 62.56 → 62.51, BLEU 35.11 → 35.15 (noise); Gemma 0.64/1.04 → 0.64/1.05 s, recogniser 0.23/0.26 s.
- **Seen again after done (2026-11-15 morning, logs `2026-11-15_081707` / `_094327` / `_102255`, P1): verses doubled, spliced or mis-referenced ~16 times** (full list in "Rest of Sunday 2026-11-15" below). Doubled: 09:54:12 John 7:37-38 ("I am the living water," He said, … “If anyone is thirsty…”), 09:55:50 John 10:9 "I am the door, I am the door", 10:41:57 Eph 1:3 "the Father of our Lord Jesus Christ, be the God and Father of our Lord Jesus Christ", 11:51:56 Jer 31:3, 11:55:12 John 1:18, 11:59:59 Rev 2:4 "But what I have against you is that But I have this against you". Spliced: 09:12:39 / 09:32:32 Isa 56:1 "…do what is right, **for my**" (verse tail where the speaker stopped), 09:15:53 "who holds it fast; **who keeps** And may God help…", 09:54:52 "John 6:35 **to them,** “I am the bread of life". References: 09:33:37 «26 cu 24» → "26 with 24", 10:41:36 «Coloseni 1 cu. 13 și 14» → "1 with. 13 and 14", 11:55:07 «cu 18» → "with 18", 11:51:10 «Romani 2 cu 4» heard «Romani 4, 2 cu 4» → "**Romans 4:2, 4**" (the wrong verse), 11:49:36 «Efeseni 5» → "**FS 5**". The doubles look like Gemma translating the verse from the source and the matcher then adding the published text: the published verse should *replace* Gemma's caption of the same words, not follow it; and «N cu M» needs to survive a full stop or a piece break between the numbers.
- **Done 2026-11-15 (branch `fix/2026-11-15-morning`):** a verse matched one part late (`replace_prev` on a later part of the same phrase) drops the translator's caption of the part before it when that part says the verse again (John 10:9, Eph 1:3, Rev 2:4); a redo across phrases keeps only the previous parts that don't repeat the verse (John 7:37); a caption ends at the published text's break when the speaker stopped there and never on my/your/his… ("for my", "who keeps"), and doesn't start on a 1-2 word fragment ("out, “If anyone"); «Geneza.» | «26 cu 24» and «Ioan 1, 18» | «cu 18» keep the reference, «1 cu. 13» loses the recogniser's stop, "N with M" in the English becomes N:M. The bench now scores a caption a redo replaced as not shown (it was counted twice). **Bench** (11-15 clips, 61 marked errors, still wrong on main → branch: 40 → 10; luigi-mitoi chrF++ 62.62 → 62.44, BLEU 35.36 → 35.15, WER 4.82 → 4.82 %; Gemma 0.68/1.23 → 0.69/1.21 s, recogniser 0.29/0.49 → 0.29/0.44 s median/p90). Still wrong 6 → 0: no doubled verse, "Genesis 26:24".

## 4. Give Gemma its own previous translations as context — `done` — stream D: Gemma sees its own previous (source, translation) pairs and continues cut sentences · 2026-11-09: an echo of the previous caption is retranslated without context

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
- **Seen again after done (2026-11-08 evening, log `2026-11-08_173244`, Best, P2):** 18:56:17 «poporului tău, poate chiar în biserica noastră, poate chiar în
  comunitatea noastră» → "a living sign before You and before" (the previous caption repeated word for word, the new
  sentence dropped). Gemma copied its own previous translation instead of translating the piece. **Change:** when the
  output equals (or is ≥ 80 % contained in) the previous pair's translation and the source differs, retranslate without
  the context pairs. Effort: tiny.
- **Done 2026-11-09 (branch `fix/2026-11-08-evening`):** a translation of ≥ 3 words whose words are ≥ 80 % in the previous translation, while its source shares < 60 % with the previous source, is translated again without context. **Bench:** no marked error changed (the 18:44 / 18:56 echoes are before the recording); luigi-mitoi chrF++ 62.64 → 62.65, BLEU 35.36 → 35.38; Gemma 0.64/1.04 → 0.64/1.03 s, recogniser 0.23/0.26 s. Kept: no harm, and it targets a failure the clips can't replay.

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
- **Money (2026-11-01 10:25:21-10:25:26, shown, marked; check against the audio):** «donațiile
  care se vor face la **cotă** [cutie?], fie online, vor fi folosite pentru a mai plăti din
  împrumutul de la bancă pentru. **B3**. Balanța de la **Lan** [loan] este de [suma] de
  **euro**» → "…made will be **at the quota**… for. B3. The balance from Lan is **[amount]
  euros**." An Atlanta church's loan is in dollars: if «euro» is the recogniser's, that is a
  wrong figure on screen. The guard checks numbers, not currencies; listen to the clip first.
- **Negation lost (2026-11-01 10:46:33, shown, marked):** «Aproape că nici **n-ar fi. trebui**[t]
  să facem această subliniere» (we hardly need to point this out) → "It would have been almost
  as if there were not. **we would need** to make this emphasis": the recogniser's period
  split «n-ar fi trebuit» and the «nu» went to the wrong half. The guard saw a «nu» in both
  (sort of) and let it through. Dropping mid-phrase periods (idea 3 note) would help first.
- **Romanian double negative read literally (2026-11-01 10:53:42, shown, marked):** «**Niciunul**
  dintre noi știm că **nu** putem fi plăcuți lui Dumnezeu prin abilitățile noastre» (we all
  know none of us can please God by our own abilities; Romanian negative concord) → "**None of
  us know** that we **cannot** be pleasing to God": the sense flips to "nobody knows". Prompt
  note: Romanian «niciunul… nu» / «nimeni… nu» / «nimic… nu» is ONE negation; the guard's
  negation count should treat negative concord as one.
- **More evidence (2026-11-01 morning, log `2026-11-01_090734`, captions page, P1):** two more
  negation slips the per-piece count let through. 09:11:04 «căile care ne. El ne **pune**
  înainte, să mergi, nici una nu-i strâmbă, nici una nu este» (the ways He sets before you, none
  is crooked) → "the paths that we He **does not put forward**, to walk, not one is crooked, not
  one is": a "not" invented on the first clause, hidden by the two real «nu» later in the piece.
  09:08:49 / 09:08:53 «Nu că Dumnezeu. | **n-are** suficienți înger[i] în cer» (not that God
  doesn't have enough angels) → "Not that God. | **are** sufficient angels in heaven": the
  negation lost across the cut. **Change:** compare negations per clause (split on the
  recogniser's periods and commas), not per piece; hold a piece that ends on «Nu că + subject».
- **More evidence (2026-11-08 morning, log `2026-11-08_090143`, P2): dates and amounts glued.**
  11:06:40 «pe data de 13-27 noiembrie» → "on November **1327**"; 11:07:57 / 11:08:02 «în noiembrie
  21… 21» → "in November **2021**"; 10:28:17 EN→RO "between a hundred and hundred and fifty verses" →
  «între **100 și 100 și 50**»; 11:51:45 «o sută și o mie de prieteni» → "**100,000** friends"; 10:28:54
  "now that I'm fifty years on the path" → «acum că **am 50 de ani**» (his age, not his years in faith).
  A day-month range («13-27 noiembrie» → "November 13-27") and a day repeated after the month need a
  rule before the digits are joined; "a hundred and fifty" should become 150 before NLLB.

## 6. Words that are also names — `done` — stream D: today's names and glossary names are kept as written · 2026-10-25: «Fratele» alone no longer brings in saved names; «fratele Ilie Puha» kept; Iacov = Jacob in Old Testament context · 2026-11-15: a Bible first name after fratele/sora or before a surname kept, put back where the English used the Bible form

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
- **Regression? (2026-11-01 11:07:02, shown):** «fratele păstor **Ilie** [surname]» → "brother pastor
  **Elijah** [surname]", although "fratele Ilie Puha" stays Ilie (the rule needs the name in Names
  & words or the glossary; today's were empty). Same list: «**pe** fratele păstor Mihai…» →
  "**for** / **on** brother pastor…" (the accusative «pe» translated as a preposition), and «următorii
  **frați**» → "the following **brothers and sisters**" (only men were named). The list itself
  is good material for a standing names file (12 full names; kept in the local log, not here).
- **Seen again after done (2026-11-15 morning, logs `2026-11-15_081707` / `_094327` / `_102255`, P1): people's names turned into Bible names again, ~14 times.** «Iosif <surname>» / «cu Iosif» → "**Joseph**" 3× (10:42:48, 10:46:08, 11:10:09; the same name kept as Iosif at 10:25:24 and 11:18:28, so it was known), «fratele Ilie» → "brother **Elijah**" (10:45:02), a child «Moise, Ilie <surname>» → "**Moses, Elijah**" (10:44:18), «Iulian» → "Julian" once (10:45:56, kept elsewhere). Invented or misheard names: 11:57:39 «pe tata» → "**Doru**", 12:02:15 «a transformat apa» (P…) → "**Pamphil**", 09:34:54 «Ghedeon» → "**Cheon**", 09:57:52 Charlie Kirk → "Charlie **Crick**", 09:50:33 / 09:51:08 a quoted preacher "Raal" / "Rael". **Change (still open from the 11-01 entry):** a first name after «fratele / sora / pe / cu» or before a capitalised surname stays as said; the welcome's names (guests named at 10:25) could be learned for the rest of the service like idea 26's repair, but only to keep them, never to rewrite other words.
- **Done 2026-11-15 (branch `fix/2026-11-15-morning`):** a Bible first name after «fratele / sora» (also alone: «fratele Ilie,»), before a capitalised surname anywhere («cu Iosif <surname>»), or listed just before such a person («pe Moise, Ilie <surname>») goes on the Names line, and is put back where the English still has the Bible form ("brother Joseph" → "brother Iosif"); a surname ending -ul/-lui (Tișbitul) is an epithet. **Bench** (11-15 clips, 61 marked errors, still wrong on main → branch: 40 → 10; luigi-mitoi chrF++ 62.62 → 62.44, BLEU 35.36 → 35.15, WER 4.82 → 4.82 %; Gemma 0.68/1.23 → 0.69/1.21 s, recogniser 0.29/0.49 → 0.29/0.44 s median/p90). Still wrong 3 → 0: "brother Ilie", "Iosif <surname>", "brother Iosif".

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

## 9. Church terms the translator gets wrong: add them to the glossary — `done` — stream D: glossary rows + `data/bible_books.csv` · 2026-10-25: inflected forms matched (after exact ones); rows fiori, cântare, minister, Gosen, împărat · 2026-11-09: evening rows (rânduit, trimitere, cauze, între gratii…) · 2026-11-15: rows răstignește, firesc, ferească Domnul de, înviora, s-au săturat de, cultul + gen., patimi, cursă, răpire, nefăcându-ți parte, chip, slavei

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
- **(2026-11-01 10:45:37, shown):** «lucrarea **împărtășirii**» (the Lord's Supper, today a
  communion Sunday) → "the work of **fellowship**". Row: împărtășire / Cina Domnului =
  Communion, the Lord's Supper (fellowship = părtășie).
  And «lucrarea **Cinei** Domnului» → "the work of **the Lord**" twice (10:55:07, 10:55:53):
  «Cinei» (genitive of Cina) dropped; 10:51:21 kept it ("The Lord's Supper") when it came
  without «lucrarea». Inflected-form row: Cinei Domnului = of the Lord's Supper.
  More rows from the same sermon (shown): «**fărădelege**», written by the recogniser as «fără
  de lege» → "who has not known **sin without the law**" (11:00:02; again 11:02:08 "separated
  from sin and **without the law**"; iniquity / lawlessness);
  «în urma **cercetării**» → "following **the research**" (10:59:44; self-examination, 1 Cor
  11:28, the sermon's own word, 10:58:13 got "examine ourselves" right); «Toți **cred** că
  admitem» (I think we all admit) → "**Everyone believes** that we admit".
  Same sermon: 10:47:40 «dorințele **sufletești** … înrădăcinate în inimă» (soulish, natural
  desires, set against «duhovnicești») → "our desires. **spiritual** ones": the opposite sense.
  Row: sufletesc (beside lăsăm / firesc / vs duhovnicesc) = natural, of the self / soulish,
  never "spiritual". And 10:47:07-10:47:16 «Părtășia nu este nimic altceva | decât comuniune»
  → "**Unity** is nothing else | than **fellowship**" (both church words shifted: a
  definition became circular).
- **More evidence (2026-11-01 morning, log `2026-11-01_090734`, captions page, P1): church
  words heard RIGHT and still translated wrong.** «rodul viței» (the fruit of the vine, every
  communion Sunday): 11:19:09 → "**The rod** of the vine", 11:19:15 → "the **cluster** of the
  vine", 11:14:57 «în rodul acesta» → "in this **lineage**" (and the misheard forms in idea 28).
  «cercetare» (self-examination, 1 Cor 11:28, the sermon's point): 10:57:36 → "the possibility
  of **searching**", 10:58:13 → "of **the search**" (10:59:44 "the research" above). 09:10:42
  Prov 29:25 «Frica de oameni este o **cursă**» → "the fear of people is a **race**" (snare).
  11:31:31 «ne raportăm la **îndurarea** ta» → "Your **endurance**" (mercy). 11:15:03 «puterea de
  **mijlocire**» → "the power of **mercy**" (intercession). 09:21:27 / 09:27:10 «rugăciunea de
  **cauze**» → "the prayer of **causes**" (prayer requests). «frați» for men only → "brothers
  **and sisters**" again: 09:23:54 (seven men, a father and his sons), 09:24:17, 09:25:50,
  11:06:55 «frați slujitori» → "brothers and sisters in ministry". **Rows:** rodul viței = the
  fruit of the vine; cercetare / a se cerceta = examination / to examine oneself; cursă = snare;
  îndurare = mercy; mijlocire = intercession; rugăciune de cauze / cauzele = prayer requests;
  prompt line: «frați» alone = brothers, "brothers and sisters" only for «frați și surori».
- **More evidence (2026-11-08 morning, log `2026-11-08_090143`, P1): heard right, translated wrong,
  in Scripture and in the sermon's point.** 11:53:45 «îngrijorarea… **a stins** cea mai frumoasă
  speranță» (put out) → "**became** the most beautiful hope" (reversed); 11:54:12 Luke 24:25 «O,
  **nepricepuților**!» (O foolish ones) → "Oh, you **unrighteous**!" (Jesus' words); 11:59:36 Col 2:22
  «trec odată cu **întrebuințarea** lor» (perish with use) → "pass away with their **madness**";
  11:44:15 the sermon's third point «nu vă îngrijorați **trăind**» → "do not worry by **betraying**";
  09:28:05 / 09:28:17 «**rodire**» → "be born" / "a birth"; 09:08:04 «familiile **rânduite**» →
  "**persecuted**"; 09:23:26 «**închinarea**» → "humility"; 11:09:57 «**am convenit**» (we agreed) → "it is
  **convenient**"; 11:57:07 «**lasă de dorit**» → "let us leave them alone"; 11:48:16-18 «alegeri»
  (the elections) → "choices" ×2; 10:08:34 / 11:15:13 «fanfara» → "fanfare" ×2; 11:29:57 «fratele **Ilie**»
  → "brother **Elijah**" again (idea 6). **Rows:** a stins = put out / quenched; nepricepuților = foolish
  ones; întrebuințare = use; trăind = living; rodire = bearing fruit; rânduit = scheduled; închinare =
  worship; am convenit = we agreed; lasă de dorit = leaves much to be desired; alegeri (with politics) =
  elections; fanfara = the brass band.
- **More evidence (2026-11-08 evening, log `2026-11-08_173244`, Best, P3): words and idioms read literally.** 18:47:04 «între **gratii**» (behind bars) →
  "among **the graces**"; 18:44:19 / 18:44:29 / 18:45:21 «**ca și** părinți / tați» (as parents) → "**just as** parents"
  ×3; 18:42:12 «mi-am **pironit** ochii» → "I **squinted**"; 19:29:18, 20:01:52 «ne-a **rânduit**» (ordained, appointed)
  → "**scheduled**" ×3 (morning: "persecuted"); 19:55:16 «**robii** logicii» → "**victims** of logic"; 19:44:17
  «trimitere» (the Great Commission) → "**authority**"; 19:58:33 «are **clopotul** lui» → "**is** His bell"; 19:28:50
  «tot felul de» → "**the same kind of**"; 20:06:36 «până la **liman**» → "until **the shore**" (safe harbour); 20:01:45
  «citim **5 versete**» → "we read **verse 5**"; 19:30:13 «Biserica **are viitor**» → "the church **is the future**" ×2;
  18:49:35 «un vas de lucru» → "a vessel of work"; 18:56:54 «cauze» (prayer requests) → "causes". Rows: între gratii,
  ca și (rol) = as, a-și pironi ochii, rânduit = ordained/appointed, robi = slaves, trimitere = commission, a avea viitor,
  până la liman, cauze = prayer requests. Effort: tiny.
- **Done 2026-11-09 (branch `fix/2026-11-08-evening`):** idioms între gratii, ca și (părinți…), a-și pironi ochii, a avea viitor, până la liman; glossary rânduit = ordained / appointed (scheduled only for people on a list), robi / robii, trimitere = commission, cauze / cauzele = prayer requests. **Bench:** evening still wrong 16 → 12 (ne-a rânduit ×2, Biserica are viitor, trimitere now right; tot felul de, robii logicii, are clopotul lui, citim 5 versete still wrong); luigi-mitoi chrF++ 62.65 → 62.62, BLEU 35.38 → 35.36 (noise); Gemma 0.64/1.03 → 0.64/1.04 s (evening 0.64/1.07 → 0.65/1.07), recogniser 0.23/0.26 s.
- **Seen again after done (2026-11-15 morning, logs `2026-11-15_081707` / `_094327` / `_102255`, P1): church words and idioms read the opposite way, heard right, ~11 times.** 11:59:28 «aceiași oameni… să zică **răstignește**» (Crucify!) → "**repent**"; 12:05:19 «biserica **firească**» (the carnal church, Laodicea) → "the church, **the faithful**"; 12:04:37 «te voi păzi… **nefăcându-ți parte**» (sparing you from it) → "by **making you a part**"; 11:56:21 «**ferească** Domnul de predicatori care…» (God save us from) → "May the Lord **protect** the preacher"; 11:29:24 «ne mai **înviorăm**» (we're refreshed) → "we get **discouraged** again"; 11:40:38 «s-au **săturat** oamenii de vopsele» (people are fed up with) → "have **fed** people with paint"; 11:42:27 «Nu este extraordinar?» → "It is **not** extraordinary."; 12:06:59 «s-a dus vremea **cultelor** importanței» (the cult of importance) → "the time of **worship** has passed"; 10:59:54 «în **patimile** lor» (their vices) → "in their **sorrows** and in their joys"; 12:01:17 Prov 29:25 «o **cursă**» → "a **race**" again (row from 11-01 not yet in); 11:49:12 «o **răpire** sufletească» (rapture) → "a spiritual **kidnapping**". **Rows:** răstignește = crucify; firesc / firească (church) = carnal / worldly; a feri / ferească Domnul de = God forbid / God save us from; a se înviora = to be refreshed / revived; s-au săturat de = are fed up with; cultul (+ genitive) = the cult of; patimi = passions / vices; cursă = snare; răpire (church) = rapture; a nu face parte / nefăcându-ți parte = sparing you; prompt line: a «Nu este…?» question stays a question.
- **Done 2026-11-15 (branch `fix/2026-11-15-morning`):** glossary răstignește = crucify, firesc / firească / firești = carnal, worldly, patimi(le) = passions, vices, cursă = snare, răpire(a) = rapture, chip(ul) = image, likeness, slavei = of the glory; idioms ferească Domnul de = God save us from, a se înviora = to be refreshed, s-au săturat de = are fed up with, cultul + genitive = the cult of, nefăcându-ți parte = sparing you, domnule doctor, Cel Prea Înalt = the Most High; a «Nu este …?» question stays a question (prompt line + a note for a sentence that is only «Nu este + word»). **Bench** (11-15 clips, 61 marked errors, still wrong on main → branch: 40 → 10; luigi-mitoi chrF++ 62.62 → 62.44, BLEU 35.36 → 35.15, WER 4.82 → 4.82 %; Gemma 0.68/1.23 → 0.69/1.21 s, recogniser 0.29/0.49 → 0.29/0.44 s median/p90). Still wrong 13 → 1: crucify, carnal, God forbid, refreshed, tired of, vices, snare, rapture, image ×2, glory ×2 now right; «vremea cultelor.» still "worship" (the replay's recogniser dropped «importanței», so the row didn't apply).

## 10. Empty-phrase fallback misses short Romanian words — `done` — stream B

- **Evidence (2026-10-24 11:39):** the Romanian recogniser returned nothing, the multilingual
  one heard "binecuvinteze." and it was still dropped: the fallback needs a Romanian-looking
  score > 0, and one word without diacritics or common words scores 0.
- **Change:** in RO→EN (locked, or auto with Romanian current) accept the multilingual text
  unless it scores as English (< 0).
- **Effort:** tiny.

## 11. Prayer language: idioms and the pronoun for God — `done` — stream D: `data/idioms.csv` injected when present; one rule for You/you · 2026-11-09: capital only when about God, speaker's person kept (prompt; evening cases not yet fixed) · 2026-11-15: domnule / domnul + name = sir, Mr.; You/He lower-cased with no God word near

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
- **More evidence (2026-11-08 morning, log `2026-11-08_090143`, P3): the wrong person.** Capital
  He for something that is not God: 11:37:12 worry «așa se joacă cu noi» → "that is how **He** plays
  with us"; 09:11:12 the preacher → "**He** says **He** is speaking". NLLB (EN→RO) makes the male
  preacher feminine again: 10:38:35 «fii **atentă**», 10:42:30 «nu te lăsa **copleșită**», 10:55:35
  "God was proud of him" → «**eram mândră** de el» (Friday 2026-11-06: «sinceră», «ascultătoare»,
  «frustrată», «uimită»). Prompt line: capital He/You only when the referent is God; NLLB: prefer
  the masculine (or a speaker setting) for first-person adjectives.
- **Seen again after done (2026-11-08 evening, log `2026-11-08_173244`, Best, P2): a person's "you" and "he" written as God, and the speaker's "I" lost.**
  19:14:34 «N-ai schimbat nimic» (you, a person) → "for **You** have changed nothing"; 19:41:18 «uită-te pe mâna ta» →
  "look at **Your** right hand"; 19:49:38 «dacă mai există printre noi [gesturile acestea]» → "if **He still exists**
  among us" (God's existence put in doubt on screen); 19:35:47 the pastor's self-mocking "I must be prudent" monologue →
  "so that **He** won't take anything by surprise", 19:36:04-08 «că mi-a dat Dumnezeu… consum» → "God has given **you**…
  **you** can spend"; 19:41:52 «copiii cărora le spui» → "children whom **He**"; 18:42:29 «lăsam microfonul, plecam la
  mama» → "**we** would leave… **the mother's** bench". **Change:** prompt rule: keep the speaker's person; capitalise
  He/You only when the referent is God in this sentence; a «mai există» after a demonstrative refers to the thing, not
  God. Bench on these pairs. Effort: small.
- **Done 2026-11-09 (branch `fix/2026-11-08-evening`):** prompt rule: capital You/He only when the sentence speaks of or to God; keep the speaker's person (I stays I). **Bench:** evening unchanged at 16 still wrong (the four person-as-God lines still capitalised: the rule alone is not enough, a post-check is the next step); luigi-mitoi chrF++ 62.51 → 62.64, BLEU 35.15 → 35.36 (best of the series); Gemma 0.64/1.05 → 0.64/1.04 s, recogniser 0.23/0.26 s.
- **Seen again after done (2026-11-15 morning, logs `2026-11-15_081707` / `_094327` / `_102255`, P1): «domnul / domnule» (sir, Mr.) as "Lord" and capital You/He for people, ~13 times, most in one story.** 11:00:32 «Domnule <initial>…» → "**the Lord**", 11:00:48 "but why, **Lord** B?", 11:04:21 "the **Lord** B", 11:05:00 «Sunați-l pe domnul din America» (call the gentleman in America) → "**Hear the Lord from America**", 11:01:58 «Domnule, te-ai ținut de cuvânt» (said to the preacher) → "**Lord, You** are a man of **Your** word"; a drunk man speaking to the preacher gets "You" at 10:58:38, 10:58:42, 10:59:26, 11:03:43; 09:16:30 the devil → "that is what **He** teaches us"; 09:26:28 hands → "we do not lift **Him** up"; 09:06:51 the brother praying → "accompany **Him**". **Change:** «domnule», «domnul + name/initial», «domnul din…», «domnule doctor» = sir / Mr. (idiom rows + prompt line); capital You/He only when the sentence names God (the 11-09 prompt rule isn't enough: a post-check that lower-cases You/He when the piece has no God word and the previous piece didn't either).
- **Done 2026-11-15 (branch `fix/2026-11-15-morning`):** «domnule», «domnul + name/initial», «domnul din …» (not cer/ceruri), «domnule doctor» = sir / Mr. (prompt line + a note in the piece); post-check: You/Your/He/Him/His lower-cased (not at a sentence start, but after the recogniser's "because.") when neither this Romanian piece nor the previous one names God (Dumnezeu, Doamne, Domnul not before a name, Isus, Hristos, Tatăl, Duhul, a mid-sentence capital El / Lui…) and the English doesn't either (`live.pronoun_check`). **Bench** (11-15 clips, 61 marked errors, still wrong on main → branch: 40 → 10; luigi-mitoi chrF++ 62.62 → 62.44, BLEU 35.36 → 35.15, WER 4.82 → 4.82 %; Gemma 0.68/1.23 → 0.69/1.21 s, recogniser 0.29/0.49 → 0.29/0.44 s median/p90). Still wrong 8 → 4: "Mr. B", "the gentleman from America", the drunk man's "you" throughout now right; the four left all have a God word in the piece or the one before ("serve Him" next to «Cuvântul lui Dumnezeu», "lift Him up before God", "grow up with Him" after «Tată», "where He was not alive" after «Hristos a înviat»), as the rule allows. Risk: a prayer that goes two pieces without naming God gets lower-case "you".

## 12. Language detection: check the Romanian recogniser before switching — `done` — stream B: the Romanian transcript as a second vote, English heard only by Parakeet translated, length floor back home · 2026-11-10: Romanian said in English mode is captioned from the Romanian transcript, two in a row switch back · 2026-11-15: no EN→RO caption from a piece with 2+ non-English words (a lone fallback piece: any)

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
- **More evidence (2026-11-08 evening, log `2026-11-08_173244`, Best, P2): Romanian inside English mode captioned as garbage.** 19:07:10 "in **FSN capitolopatru,
  versetus shaptashpe**" (Efeseni 4:17) → copied into the Romanian caption; 19:11:49-58 "and **Sudhid** comes… in
  **Sadiqam Tot Simpichware**"; 19:05:21 "That chair pent through **Voi Parinsi**" (Romanian «voi, părinții»); 19:14:18
  "**Prashisa** Lord" (Praise the Lord). Back to Romanian took ~1 min at 19:17 (hidden). **Change:** when Parakeet's
  piece has a low English score, run the Romanian recogniser on it too and take the better-scoring text (or drop it).
  Effort: medium.
- **Plan (2026-11-09, not built):** the gate already switches to the Romanian transcript when the English piece fails the gate (`gate_switched`), but these pieces passed it: Parakeet's romanised words (FSN, capitolopatru, Parinsi) are not in the English lexicon yet are few enough. (1) In `Pipeline._screen`, when lang = en and `ev["ro"]` exists, compute the English lexicon share of Parakeet's text and the Romanian share of the Romanian text; take the Romanian text (log `gate_switched`) when Romanian's share is ≥ 0.2 higher and its `lang_score` > 0.15, even if the English text was kept. (2) Count two such switches in a row as a vote for Romanian in `lang.update`, so the way back from English takes seconds, not a minute (19:17). (3) Tests from the evening lines (Efeseni 4:17, voi părinții, Praise the Lord); replay the log offline (it holds both transcripts) for the false-switch count on English pieces of the 10-24, 10-25 and 11-08 logs, then bench the English part of the 19:13:55 clips. Effort: medium (1 day).
- **Built 2026-11-10 (branch `live-voice`):** parts (1) and (2) of that plan, in `pipeline._gate` (`romanian_inside_english`, log fields `gate_switched` + `ro_inside_en`, count `ro_inside_en`) and `LangTracker.go_home`. In auto mode with Romanian as the home language, an English-mode phrase is captioned from the Romanian recogniser's transcript when that transcript has ≥ 3 words, reads as Romanian (`lang_score` > 0.15) and its real-word share beats the English text's by ≥ 0.2; two such phrases in a row switch the language back to Romanian. `live.ro_inside_en: false` turns it off; a locked language never second-guesses. **Offline replay** (`scripts/idea12_replay.py`, both transcripts are in each log record): on the pure-English Alex sermon 0 of 104 phrases switched, in two runs (0 false switches of 208); on the 2026-10-24 eulogy 4 of 70, all four Romanian said in English mode («Oamenii lui Dumnezeu e greu de înțeles», «Dragoș Puha a finanțat… evanghelizări», «Slavă lui Dumnezeu că mai simțim»). One of the four is half English ("It's okay to cry because you loved your dad"), and that half is lost with the switch: a phrase is one language. **Not done from part (3):** the 2026-11-08 evening log and the 19:13:55 clips are not on this Mac (`logs/services/2026-11-08_173244.jsonl`, `logs/recordings/2026-11-08_191355.wav`), so the Efeseni 4:17 / «voi, părinții» / "Prashisa Lord" lines were not replayed; run `scripts/idea12_replay.py <that log>` where they are. «That chair pent through Voi Parinsi» would not switch (the phrase is mostly real English). For Live Voice this is the fix for the first known limit of `docs/live_voice_plan.md` §12 (Romanian inside an English chunk voiced as English), and the two-in-a-row vote shortens the second (the voice talking on over the speaker's Romanian).
- **Seen again after done (2026-11-15 morning, logs `2026-11-15_081707` / `_094327` / `_102255`, P1): two Romanian pieces taken as English and captioned in Romanian.** 09:33:37 Gen 26:24 read in fast Romanian, heard by Parakeet as "She shall kut the children." → «**Ea va tăia copiii.**» (she will cut the children, during Scripture); 09:37:14 «Ghedeon a coborât la apă cu 300…» → "Get one coborila up cu tre de woman." → «Ia un coborila în sus cu femeia.». Both a lone piece between Romanian ones, both with non-English tokens («kut», «coborila», «cu», «tre»); the app switched back at once. **Change:** a lone switch piece is captioned from the Romanian transcript when that reads as Romanian (the 11-10 `romanian_inside_english` rule, applied to the *first* English piece too), and never EN→RO from a piece with ≥ 2 non-English tokens.
- **Done 2026-11-15 (branch `fix/2026-11-15-morning`):** both wrong switches came through the "English the Romanian model missed" fallback with an empty Romanian transcript, so the 11-10 rule had nothing to compare (it already runs on the first English piece otherwise): that lone piece must now be all English words («kut» was the only foreign word in "She shall kut the children."), and in English mode a piece with 2+ lower-case non-English words is not captioned (`low_confidence`, reason `not_english`). Tests cover both live lines; the replay didn't switch on either, on main or the branch, so the bench can't show it. **Bench** (11-15 clips, 61 marked errors, still wrong on main → branch: 40 → 10; luigi-mitoi chrF++ 62.62 → 62.44, BLEU 35.36 → 35.15, WER 4.82 → 4.82 %; Gemma 0.68/1.23 → 0.69/1.21 s, recogniser 0.29/0.49 → 0.29/0.44 s median/p90).

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
- **More evidence (2026-11-01 09:38-09:40, English sermon on Genesis 37, Best = NLLB EN→RO,
  log `2026-11-01_090734`):** good overall (verse 2 matched Cornilescu). Slips:
  - Bible names left in English form: "Rachel" → «Rachel» (Rahela), "Belpha and Zilpha"
    (recogniser for Bilhah, Zilpah) → kept as heard (Bilha, Zilpa). 09:40:25 / 09:40:36 "Jacob", "Joseph" → «Jacob»,
    «Joseph» in long pieces although 09:38:49 gave «Iosif»: NLLB is inconsistent, a glossary
    row would fix it either way. Same pieces: a cut "…to his | child, to Joseph" → «…pentru el»
    | «Copil, pentru Joseph» (segmentation, as idea 25).
  - **Wrong words NLLB makes up (shown, marked):** 09:43:49 "…with our children. **Amen.**" →
    «…cu copiii noștri. **Amintește-mi.**» (remind me; again 09:45:23, every time «Amen» ends a long piece; **fixed 2026-11-01** in `NLLBTranslator`: «Amintește-mi» → «Amin» when the English said Amen, since NLLB ignores the glossary's amin row): a glossary row Amen = Amin, and a
    check that a final «Amen» stays «Amin». 09:44:14 "it doesn't matter what it is, **anything
    at all**" → «…nu contează ce este, **nimic**» (nothing): the opposite; the negation guard
    (idea 5) only runs RO→EN. 09:43:45 the recogniser made "gives us as parents, as fathers,
    as mothers, wisdom" into "gives us **his** parents… **his mother's** wisdom" → «să ne dea
    părinților săi… înțelepciunea mamei sale» (nonsense, faithfully translated). A Bible-names row set
    EN→RO (Rachel=Rahela, Leah=Lea, Bilhah=Bilha, Zilpah=Zilpa…) in the glossary.
  - Capital «El» for a man: 09:39:25 "he made him a robe" → «**El** i-a făcut o haină» (Jacob).
    The You/He-for-God rule (idea 11) leaks into EN→RO; NLLB has no context for it.
  - Filler kept and subject lost at a piece start: 09:40:17 "um didn't want to listen" (they)
    → «**Um nu a vrut**» (singular); 09:40:21 "had a plot" → «**A complotat**» (singular). Strip
    um/uh before translating; the plural subject was in the previous piece.
- **More evidence (2026-10-31 sound check with an English sermon text, logs
  `2026-10-31_163237` / `_171344`, Best = NLLB EN→RO, P2):** the key word of the text lost:
  "it's easy to have **faith**" → «e ușor să ai **încredere**» (trust) 4× (16:49:51, 17:17:15,
  17:26:49, 17:30:04) while "faith" alone gave «credința»; "Look at **Peter**" → «Uită-te la
  **Peter**» 3× (16:50:33, 17:17:55, 17:32:28; «Petru» in the next pieces); "God is silent" →
  «Dumnezeu **tacă**» 3× (should be «tace»); "We begin with confidence in God" → «**Încredeți-vă**
  în Dumnezeu» (an order); "step out of the boat and walk" → «să **cobor**… să **merg**» (1st
  person). **Change:** apply EN→RO glossary rows after NLLB the way the 2026-11-01 Amen → Amin
  fix does (faith → credință, Peter → Petru, the Bible names above), plus a short fix-up list
  of NLLB verb slips («tacă» → «tace»).
- **More evidence (2026-11-06 19:56:35-19:57:06, English reading of Mark 6:38-42, log `2026-11-06_195618`,
  installed app 0.1.2):** the reading was mostly good, with Cornilescu at 19:56:54. Slips:
  «**Discipolii**» / «discipolii» for "disciples", right after the verse's «ucenicilor» (row:
  disciples = ucenicii); «**cele două pești**» for "the two fish" twice, though the verse has
  «cei doi pești» (gender agreement); "sit down" → «toți **vor sta**» (future, not a command).
  Verse splice, marked: 19:56:49 already gave «…a luat cele cinci pâini… a binecuvântat», then
  19:56:54 repeated «cele cinci pâini și cei doi pești… a rostit binecuvântarea» (see idea 3).
  **Meaning reversed (2026-11-06 20:16:28, prayer, shown, marked):** "You've been **through with
  us** | through trial and tribulation" (You've gone through it with us) → «**Ai terminat cu
  noi.**» (You are finished with us), the opposite in a prayer: the cut left "been through
  with" reading as the idiom. Idea 25 applies in English too: keep "through with us" open
  when the next piece continues «through…». Otherwise this prayer was good.
  20:18:17 "the **ushers** will come up with the offering basket" → «**ofițerii** vor veni…»
  (officers): a church-word row (the term this church uses for ushers; ask). 20:17:25 "Would
  you allow your word" → «Vrei să-**mi dai** cuvântul tău» (give me). And "Amen. Amen." came out
  right («Amin, amin») inside 20:17:54: only some positions fail.
  **Word cut and misheard, on screen (20:18:58-20:19:12, announcements, marked):** "our church
  **pick** | **picnic** again at [a local park]" → «**Am ales biserica.**» (we chose the church) |
  «Picnic din nou…»; "we plan on doing a **bomb fire** | **bonfire**" → «plănuim să **ardem o
  bombă**» (we plan to burn a bomb), corrected by the next piece. The English recogniser ends a
  piece on a half-word and repeats it whole: idea 25's cut-word drop, English side (idea 16).
  A word list could keep "bonfire". The place name and the date came through.
  Same announcements (marked): 20:19:27 "on the twenty-seventh of November, we will **not have
  youth**" (no youth service) → «nu vom **mai avea tineri**» (we'll no longer have young people):
  row, youth (the meeting) = întâlnirea de tineret; 20:19:45 "Christmas **candles** | **carols**"
  → «cântăm niște **lumânări**» (we sing candles); "keep you guys heads up" → «a vă ține **pe
  capete**» (on heads). «Proclaim», the youth's December event, is a name to keep in English:
  the same word came out as «am proclamat» / «o proclamare». Dates and places were right.
  Talk that followed: 20:21:30 "we **sat around** before **service**" → «am stat **în jurul valorii**
  de înainte de **serviciu**» (NLLB's stock "around the value of"; serviciu = a job, here slujbă);
  20:22:06 "being asked to pretend to being | be prayed over" → «era cerut sa **pretinda**» (a
  false start translated as meaning, no diacritics). Rows: service (church) = slujbă.
  Sermon on Gen 39: 20:25:27 "**Potiphar**" heard "**pot of earth**" → «Casa de numit-o **oală de
  pământ**» (the name became a clay pot; marked), though the verse just before had «Potifar».
  The English recogniser could get the Bible names in the running passage as keywords (the
  announced reference gives the chapter). Also 20:25:39 "considered modern day" → «Consideră
  **ziua modernă**»; 20:25:58 "discipling" → «**ucenici**» (noun, the verb lost).
  The sermon's own key term shifts: "Speed Bumps" (title, 20:23:28, kept in English) → 20:29:55
  «**bariere de viteză**» (not a Romanian phrase; limitatoare de viteză). A title announced
  once could hold for the whole sermon. Also 20:29:33 "instances" → «**Instanțe**» (courts; ori,
  situații), 20:29:44 "providing" for God → «**furnizezi**» (supplier's word; îngrijești), and
  more «um/uh» left in (20:30:13, 20:30:20): the filler strip would clear all of these.
  **NLLB drops whole sentences from multi-sentence pieces (shown, marked):** 20:32:06 "**I speed
  up.** Not the little ones… like the humps. **Okay. What happens to you guys in a van?**" → only
  «Nu cele mici… cum ar fi cocoșele.»: the point of the story and the question are gone.
  20:30:28 "…I fell into a hole. **Sometimes it's our own foolishness.**" → the last sentence
  gone, and it was the point. Change: split a piece at sentence ends before NLLB and translate
  20:41:30 "**Pride.** And at seventeen years old…" → «Și la 17 ani…»: the one-word answer to "a
  spirit of what?" gone, so the congregation never saw the word the point turned on.
  20:51:43 "**is obedience.** You're gonna learn this the hard way." → only «O să înveți asta pe
  calea grea.»: the sermon's key word again, a short sentence at the start of a piece.
  20:53:10 "…this is what's going on. **I'm using you. I'm showing my glory.**" → the last two gone.
  each (small; time on the machine bench), or a length check that retries with the split.
  Word choice, same sermon: 20:32:23 "speed bumps or **humps**" → «obstacole sau **cocote**»
  (an unfortunate word in a sermon; 20:32:06 had «cocoșele»), and "speed bumps" now «obstacole»,
  «obstacole de viteză», «bariere de viteză»: a row per sermon term would end this. 20:33:03
  "Abraham" → «Abraham» (Avraam, for the Bible-names rows; 20:34:12 "Paul" → «Paul», Pavel).
  **«în jurul valorii de»**: 20:33:39 "takes them **around and around and around** for how many
  years?" → «le duce în jurul valorii de și în jurul valorii de și în jurul valorii de…»
  (marked; 2nd time tonight, after 20:21:30): a post-edit can't fix meaning here, but it could
  flag a repeat. A whole line of it is a reason to try Gemma on EN→RO again on the bench. Also
  20:34:07 "another speed bump" → «încă o **greșeală**» (mistake), and 20:33:56 lost diacritics.
  20:36:12 "work on your **character**" → «lucrează la **personajul** tău» (a role in a play),
  though 20:36:18 has «caracterul»: row character = caracter. 20:36:04 "fifty feet. That's…" lost
  the distance (a dropped start, as above). Local place names held.
  Church English taken literally: God "**moving**" → «de ce nu **se mișcă** Domnul?» (20:23:23), «**mă
  voi muta**» (I'll relocate; 20:37:49); "**pouring into** Alex" → «se **revarsă** în Alex» (20:38:03,
  investing in someone). Rows or an idiom list for EN→RO: God moves / is moving = Dumnezeu
  lucrează; pour into someone = a investi în cineva.
  A man's "I" comes out feminine: 20:52:14 «să fiu **ascultătoare** … voi fi **discernătoare**», 20:23:05
  «sunt **sinceră**» (the speaker is a man). NLLB has no speaker; a per-speaker setting is overkill,
  but the masculine could be preferred by default.
  20:52:42 "Scripture **would not tell us**" (conditional) → «Scriptura **nu ne spune**» (flat denial of
  what Scripture says); 20:52:50 "pressed in" → «a apăsat în mai mult».
  More rows: 20:54:55 "the **burning bush**" → «tufișul aprins» (rugul aprins); 20:54:43 "**on fire** for
  God" → «de foc pentru Dumnezeu» (arzător); 20:54:36 "open up **word**" → «o să spun ceva» (the Word,
  «Cuvântul», with no article in church speech).
  **Two that say something false, on screen (marked):** 20:38:53 / 20:38:58 "Till the day we meet our
  Maker and we end up | **in heaven, we're going to have trials**" → «…și ne sfârșim.» | «**În rai
  vom avea încercări** și necazuri» (in heaven we'll have trials): a piece break moved the clause.
  "in heaven" ending a sentence and a new clause after it: don't start a piece on a preposition
  phrase that finishes the last one (idea 25, English). 20:38:43 "He's **had** plenty of these
  days" → «**A băut** destule zilele astea» (he drank plenty these days). Verse: 20:38:27 gave Psalm
  23:4 literally, and 20:38:31 then the Cornilescu in full, so the first line twice (idea 3).
- **More evidence (2026-11-08 evening, log `2026-11-08_173244`, Best, P2; clone run, probably with the afternoon's sentence split): EN→RO (NLLB) still loses
  meaning.** 19:08:00 the recogniser heard "**do not** be renewed" (Eph 4:23, *to be renewed*) → «**să nu fiți
  reînnoiți** în trupul vostru» (reversed Scripture, and «în trupul vostru» invented); 19:13:40 "do not be foolish" →
  «**Nu fiți proști!**» (crude, in a verse; «nechibzuiți»); 19:13:26 "arise from the dead" → «**învierea** din morți»;
  19:11:21 "[allow God to] take control of our lives" → «**să ne controlam** viețile» (we control); 19:09:02 "if we are
  called" → «**pentru că** noi suntem chemați»; 19:09:04 "we are to be changed" → «**Vom fi** schimbați»; 19:10:51
  "Didn't say" → «**N-am spus**»; 19:09:14 "fully not Jesus" → «nu este cu totul Isus»; 19:09:33 "by the book" → «de
  carte»; 19:05:55 "fall" (autumn) → «și să cadă»; 19:10:10 "the way I used to live" → «modul în care am folosit pentru
  a trăi»; 19:08:45 no diacritics («se intampla… traiesc in ea»). Supports benching Gemma for EN→RO again on Scripture
  and sermon English. Effort: medium (bench).
- **Plan (2026-11-09, not built):** re-run the EN→RO bench of stream E (`logs/bench/en_ro_2026-10-24.jsonl`) with the evening's English lines added (19:05-19:13 from the log, transcripts as source, since that part has no recording) and the Friday clips: Gemma vs NLLB, scored by bad/good regexes per line (negation kept, «nechibzuiți», diacritics, no invented words) plus time per line. Then three options: keep NLLB; switch Best to Gemma for EN→RO if it wins by ≥ 3 lines and stays under ~1.5 s p90; or keep NLLB and route only verse-like English (Scripture caught by the verse matcher, else imperatives with «do not») to Gemma. Also add a negation guard for EN→RO (idea 5 checks RO→EN only). Effort: medium (half a day bench, half a day change).
- **Not built 2026-11-10 (branch `live-voice`), and why:** this plan is a bench and a decision, not a change that can be built from it. Its first step needs the evening's English lines (19:05-19:13) from `logs/services/2026-11-08_173244.jsonl`, which is not on this Mac, and bad/good patterns per line written by someone who heard them; the choice between its three options depends on that bench's numbers. The branch does change the ground under it: with the AI engine on (Claude Haiku 5.5), English → Romanian goes to Haiku in both profiles, so the Gemma-or-NLLB question is only about Local. The EN→RO negation guard it also asks for is still open.

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
- **Miss (2026-11-01 09:41:55 / 09:42:00):** "…one of many **colours**." | "**colors**, surely a
  luxury" → «…una de multe culori.» | «**culori**, cu siguranta un lux»: the repeat check
  missed it because the recogniser spelled the word two ways (British/US). Normalise
  colour/color, favour/favor… before comparing. Same piece: «cu siguranta» lost its «ț»
  (NLLB writes some words without diacritics: a small fix-up list).

## 17. Don't caption garbled recognition: a confidence gate — `done` — stream B: `src/live/gate.py` (lexicon share, letter scraps, transcript disagreement) · 2026-11-09: Romanian scraps of 1-2 words dropped

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
- **Miss, EN side (2026-11-01 11:31:47, shown, marked):** in the middle of a Romanian prayer one
  piece came out as English garbage «Parat to take up a poral toe. Second.» and was captioned
  in Romanian, "Pregătiți-vă să luați un deget poral." (prepare to take a poral finger). The
  next piece (11:31:48) was Romanian again. The gate let it through: most words are real
  English. Add: a lone English piece between Romanian ones, with a non-word («poral») or
  under ~8 words, is dropped (idea 12's language-switch rule already wants 3 phrases).
- **More evidence (2026-10-31 sound checks, logs `2026-10-31_154925` / `_171344`, P1):** English
  speech heard by the Romanian recogniser while the app was still in RO→EN, captioned as
  invented English: 17:26:10 «urs, simplu, bv.» → "your, simple, well."; 17:26:13 «Usate,
  blueving Usate, sate, simple, blueving, doar există.» → "Use, believe, use, believe, simple,
  believe, it just exists."; 15:49:31 «Veți că Hed Start le dați wy.» → "You will see that He
  **starts giving you**." All three have 2+ tokens that are in neither lexicon (bv, Usate,
  blueving, Hed, wy). The other way, 17:24:22 «Ce este credința?» was taken as English and
  shown untranslated. **Change:** the gate drops a piece with ≥2 non-lexicon tokens when it
  has under ~8 words, whatever the share.
- **More evidence (2026-11-08 morning, log `2026-11-08_090143`, P2): 7 garbage captions shown.**
  English sermon, the Romanian aside heard as English-ish names: 10:29:26 «Hens, Fages, Brad, Aurelio
  Ghems, Twi Streets…» → «Găinile, Fages, Brad…»; 10:42:36 «Gaz, Grace Mercy, Many Times, World.»;
  10:52:07 «Nexthe, din World, Ine Bichon de So Gain.». Romanian blessing: 10:58:02 «m-a G, Manor» →
  "I am Manor"; 10:58:15 «Oh Singham. Mega Gy, Menor, Okingham, și Dumnezeu să binecuvinteze…» (the
  clean second half let the first through: gate per clause). Song tails: 10:19:18 «învintez acest
  copil» → "I am **winning** this child"; 10:27:07 «Media de post, pictură» → "Post media, painting.".
  Change: a clause with ≥ 2 capitalised tokens that are in neither lexicon nor today's names is
  dropped, even when the rest of the piece is clean.
- **More evidence (2026-11-08 evening, log `2026-11-08_173244`, Best, P3): fragments shown.** 19:53:07 «Appius în creiere» → "**Appius in the mind**"; 19:58:52
  «rom.» → "rom."; 20:06:04 «să-l.» → "**call out.**" (invented from two letters); 19:52:21-25 «n-amar 4 / nambăr»
  (English *number 4*) → "not 4. / number"; 19:58:55 «rostim cuvinte de tine, nu se alege, ci din nimic…» → a garbled
  sentence. **Change:** drop a piece of ≤ 2 tokens that is not a sentence on its own (no verb, ends in «-l.» or is a
  letter scrap), and gate per clause (morning note). Effort: small.
- **Done 2026-11-09 (branch `fix/2026-11-08-evening`):** Romanian scraps dropped before translation (logged `scrap`): ≤ 2 words that are all function words, or a single word of ≤ 3 letters not on a short list (da, nu, amin…); questions, exclamations and numbers kept. **Bench:** evening still wrong 12 → 11 («să-l.» → "call out." gone; «Appius în creiere» has 3 words, still shown); 448 → 447 shown; luigi-mitoi unchanged (chrF++ 62.62, BLEU 35.36, 431 shown); Gemma 0.64/1.04 s, recogniser 0.23/0.26 s.
- **Seen again after done (2026-11-15 morning, logs `2026-11-15_081707` / `_094327` / `_102255`, P2), with idea 20: echoes and scraps shown, ~12.** Scraps: 10:42:32 «col.» → "col.", 10:52:59 «Lorenbl.» → "Lorenbl.", 11:05:16 «La primul botez de la Ag.» → "of the Ag.", 12:10:38 «orașase.» (ora șase) → "**cities.**", 11:56:24 «9, Matter what» → "9, Whatever happens", 11:58:33 «Gua». Echoes (idea 20) below.

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
- **More evidence (2026-11-01 morning, log `2026-11-01_090734`, P1): four songs captioned as
  speech, ~75 pieces, no Song key in the log.** ProPresenter was hidden, but with
  `display.show_hidden` on, the captions page showed them. 10:14:26-10:15:26 (Romanian), 10:16:45-
  10:21:25 (English, EN→RO), 10:28:00-10:32:04 (Romanian then English), 11:15:57-11:18:01
  (communion song «Crucea»). Samples: 10:28:17 «rămân în **pește**» → "remain in **the fish**";
  10:29:31 «doar la votare. pe ochi. baie» → "only to be **voted**. on the eyes. **bath**";
  10:17:44 the English lyric heard as «hh, de sforă» → «Îți mulțumesc, Isus, pentru **situația
  asta**. - **Mulțumesc, domnule.**»; 10:19:01 → «M-a spălat **când eram alb**»; 11:17:28
  «care, tată ne-a iubit» → "who, **Dad** has loved us"; 10:14:59 «vine o zi neceru» → "a day
  when He will **demand**". The English side even shows the Romanian recogniser's scraps
  ("G.", "Wiking.", "bifor, de Khme de S.") as the source. **Change:** turn automatic song
  detection on (bench it on these four clips), or make Song part of the operator's run sheet;
  the level is a hint too (worship team speech_db ≈ -12..-8 vs speakers ≈ -20).

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

## 20. Cut phrases by words, not seconds, for slow speakers — `done` — stream C: `phrase_min_words` 6 in Best, no phrase ending on a preposition · 2026-10-25: a one-word echo of the previous phrase's last word is dropped (`echo_word`) · 2026-11-15: 2-3 word echoes of the previous piece's last 5 words dropped

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
- **Miss (2026-11-01 10:44:15 / 10:44:24):** "…sub titlu mai mult decât **suferință.**" |
  "**suferința.** Când noi…" → "…more than suffering" | "**suffering.** When we think…": the
  title word echoed into the next piece with a different ending (ă/a), so `echo_word` missed
  it. Compare the echo without the final vowel / article.
- **Seen again after done (2026-11-15 morning, logs `2026-11-15_081707` / `_094327` / `_102255`, P2): multi-word echoes pass `echo_word`.** 09:38:26 «madularele ei.» → "her limbs." (after "…her body and her limbs."); 10:00:07 «îmbolnăvești de inimă.» → "you will suffer from heart disease." (after "…fall ill from your heart."); 12:04:05 «a eclesia.» → "the church."; 12:05:57 «cu toate.» → "with everything."; 12:03:52 «toate» → "all". **Change:** drop a piece of ≤ 3 words whose words (without endings/diacritics) are all in the last 5 words of the previous piece.
- **Done 2026-11-15 (branch `fix/2026-11-15-morning`):** a piece of 2-3 words, right after the previous one, whose words (first 5 letters, no diacritics) are all in that piece's last 5 words is dropped as `echo_word`. **Bench** (11-15 clips, 61 marked errors, still wrong on main → branch: 40 → 10; luigi-mitoi chrF++ 62.62 → 62.44, BLEU 35.36 → 35.15, WER 4.82 → 4.82 %; Gemma 0.68/1.23 → 0.69/1.21 s, recogniser 0.29/0.49 → 0.29/0.44 s median/p90). Still wrong 1 → 0 ("you will suffer from heart disease" twice).

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

## 23. Meaning slips in Quality: a dropped word, a misread time phrase, a wrong person — `idea` · 2026-10-25: idiom rows (de două zile, după-masa, ani de zile, te rog frumos = please); the coverage check (b) not done · 2026-11-15: narrow coverage check (God named in the English, not in the source) built; the full check (b) planned

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
- **Idiom (2026-11-01 09:30:03, shown):** «am fost la **firul ierbii**» (we went to the
  grassroots, to ordinary people) → "we were at **the blade of grass**". Idiom row: la firul
  ierbii = at the grassroots / among ordinary people.
- **Idiom (2026-11-01 11:11:19, shown):** «Și asta vorbesc, în primul rând, **în dreptul meu**»
  (I say this first of all about myself) → "first, **in my own right**". Row: în dreptul meu =
  as for me / about myself.
- **Idiom (2026-11-01 11:06:22, shown, marked):** «nu este niciunul dintre noi care să se
  poată **bate cu cărămida în piept**» (boast of being righteous, holier-than-thou) → "who can
  **put a brick in his chest**". Row: a se bate cu cărămida în piept = to boast of one's own
  righteousness / to beat one's chest. Next piece, Rom 3:23 «sunt lipsiți de slava lui
  Dumnezeu» → "are lacking the glory of God" (verse not matched: "fall short").
- **Slip of the tongue kept, the correction lost (2026-11-01 10:58:56 / 10:59:00, shown, marked):**
  «care să trateze în mod **juratic** | **ușuratic** lucrarea aceasta» (the preacher misspoke and
  corrected himself: treat it lightly) → "who treats in a **swearing manner** | this work":
  the non-word «juratic» was guessed as «jurat» (sworn), and the correction at the start of
  the next piece was dropped as a repeat. When a piece starts with a word that sounds like the
  previous piece's last word, prefer the second. Also 10:58:35 «**neputincioși** și bolnavi»
  (1 Cor 11:30, weak and sick) → "**unrighteous** and sick": the retold verse wasn't matched.
- **Idiom cut in two (2026-11-01 10:50:32 / 10:50:35, shown):** «fără a **călca.** | **în picioare**
  pe e-ul nostru» (to trample our ego) → "without stepping on. | **on our own feet** and on
  our own pretensions": the idiom split across pieces (idea 25), and «eu-l» heard «e-ul». Row:
  a călca în picioare = to trample; don't close a piece after «a călca» (needs its object).
- **(2026-11-01 10:22:04, shown):** «activități administrative, ca să nu le numim **firești**, că
  doar noi nu mai suntem firești» (not to call them *carnal / worldly*, since we're no longer
  carnal) → "so that we don't call them by their **proper names**, for we are no longer flesh":
  the pun on «firesc» (natural ↔ of the flesh) lost; the next piece used "flesh" right. A
  glossary note: firesc/firești in a church context = carnal, worldly (1 Cor 3). Same minute:
  «**PC Kit** Festival» (the church's event, name unchecked: PRC Kids?) is a name for Names &
  words.
- **More idioms (2026-11-01 morning, log `2026-11-01_090734`, P2):** 09:22:18 «ne-am **trezit**
  cu un timp destul de îndelungat» (we found ourselves with) → "we **woke up** with";
  10:54:42 «nu este un lucru **de apucat**» (not something to grab at / take lightly) → "not
  something to be **rushed**"; 10:22:37 «care **s-au cheltuit**» (who gave themselves) → "who
  **spent money**"; 10:54:31 «datorită **nevegherii** noastre» (our lack of watchfulness) → "our
  **wandering**". Rows: a se trezi cu = to find oneself with; s-au cheltuit (people) = gave
  themselves; neveghere = lack of watchfulness.
- **Change:** (a) idioms row for "a … de N zile/ani" → "N days/years ago" / "for N days" by
  tense (perfect + de = ago); (b) a coverage check like the number/negation guard (idea 5):
  Romanian content words (adjectives/nouns) with no counterpart in the English trigger one
  re-translation; (c) an "am"/"a" before a past participle, when the subject is God/Jesus,
  fixed before translation.
- **Effort:** (a) tiny; (b) medium (needs a RO→EN word map or Gemma self-check, watch latency);
  (c) small but risky (false fixes when the speaker really speaks in the 1st person). Bench on
  the marked clips.
- **More evidence (2026-11-15 morning, logs `2026-11-15_081707` / `_094327` / `_102255`, P1): Gemma writes a sentence of its own over a garbled or cut piece, ~5 times, twice about God.** 09:35:22 «tot trupul i-a [cuprins] de cancer» (cancer had spread through her whole body) → "**God stopped the cancer** in her entire body" (the next pieces say God healed her later: the caption ran ahead and invented); 10:48:15 «[să-i dea] puteri **nemaipomenite** care vin de sus» (extraordinary strength from above) → "**let us remember those** who come from above"; 12:03:17 «**dor**, Dor. D.» (longing, the sermon's point) → "**crown, crown**" (2 Tim 4:8 was just named; Gemma took the verse's word); 11:48:12 «dacă nu-i f[all] e l[ove]» (English *fall in love*) → "if it is not **the will of God**"; 09:34:26 «Domnul s-a… [arătat]. Isa, i-a zis fii pe pace» (Judg 6:23) → "**Isaiah**, he said to **the sons of peace**". The coverage check (b) would flag the first two (content words with no source); the third needs the Scripture hint not to override the source word.
- **Done (narrow part) 2026-11-15 (branch `fix/2026-11-15-morning`):** God / Lord / Jesus / Christ in the English with no word for Him in the Romanian (and no domn-/laud-/slav-/binecuv- word) is retried without context with a note, like a negation. Flags 0.1 % of this morning's phrases (exactly "God stopped the cancer" and "the will of God") and 0-0.2 % of the 11-08 logs: < 0.01 s per phrase. **The full coverage check (b), planned, not built:** an English content word with no Bible-lexicon link or cognate in the source flags 20-41 % of phrases (3+ / 4+ such words), i.e. +0.15-0.3 s per phrase in retries, over the 0.1 s budget; it needs a better word lexicon than the Bible one first. The replay didn't reproduce either invented line on main, so the bench can't show the effect; tests cover it.

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

## 25. A Romanian word cut in two at a piece boundary is translated as two words — `partly done` (2026-11-15): the previous caption is redone when its last word was the start of this piece's first · 2026-11-15 evening `done` at the recogniser: Romanian words are committed by when they were heard, and a forced cut hears the audio past it (`live.timed_commit`); WER 6.42 / 4.41 / 4.79 → 5.25 / 3.70 / 3.70% on the three corrected sermons

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
  - 2026-11-01 11:10:05 / 11:10:10 «iar fratele **[first].** | **[surname]** se va ruga» → "and brother
    Mihai." | "will pray…": the surname at the start of the next piece was dropped, so the
    caption could mean the wrong person (a brother with the same first name was named a moment before). Don't close a
    piece after «fratele/sora + first name».
  - 2026-11-01 10:56:14 / 10:56:17 «nu s-ar întâmpla nimic. **ar mai muri.** | **un ticălos**, un
    om care merită osândă» (only one more wretch would die) → "nothing would happen. **if we
    were to die again.** | a wretch, a man who deserves…": the subject came in the next piece.
  - 2026-11-01 10:46:44 / 10:46:52 «…a fost **scopul.**» | «**ultim** pe care l-a avut» (scopul
    ultim, the ultimate purpose) → "…was the purpose." | "**the last one** that God had".
  - 2026-11-01 09:28:02 / 09:28:06 (shown): «el se bucură **neîncetat** de numele tău. Deci,
    dragii mei.» → "In your name they So, my dear ones." and the next piece heard the same words
    again as «**în cetare** de numele tău. Deci, dragii mei,» → "in the **silence** of Your name.
    So, my dear ones,": the piece was captioned twice, garbled both times. Marked.
  - 2026-11-01 09:09:42 "…ca să ne **bine.**" | "**binecuvinteze** pe noi…" (log
    `2026-11-01_090734`, paused): «binecuvinteze» cut in two again, the first half a real word
    («bine», well) that would translate as one; the next piece repeats it whole.
  - **2026-11-01 09:11:57 / 09:12:00 (captions page, P1): God made the subject of "doesn't
    know".** «…pentru că Dumnezeu. **nu știu cât** | evrei, israeliții de pe vremea aceea,
    cunoșteau» (I don't know how much the Hebrews of that time knew) → "because **God doesn't
    know how much**." | "Hebrews, the Israelites of that time, knew": the speaker's «nu știu
    cât» (I don't know how much) was cut from its subject, and the piece read God + "doesn't
    know". Also 09:09:01 «că nu sunt împlinit» (the preacher voicing God, ironically) → "that
    **they** are not fulfilled". Don't close a piece on «nu știu cât/ce/dacă» (it needs what
    follows); a «Dumnezeu» + 1st-person verb in one piece with no quote is a cut to check.
- **Change:** (a) when a piece ends on a word that the next piece's first word starts with
  (autor / autoînșelare), or on a non-word fragment, drop that last word before translating and
  let the next piece carry it (like idea 16's English repeat check and idea 20's `echo_word`, the
  other way round); (b) don't close a piece after a genitive noun that usually takes «lui +
  name» (omului, cartea, Cuvântul) when the next word comes within ~0.5 s; (c) glossary row
  «omul lui Dumnezeu» → "the man of God", «cartea omului lui Dumnezeu X» → "the book of X".
- **Effort:** (a) small (string check on the last/first word, both are in hand before the
  translation); (b) small, segmentation rule, watch delay; (c) tiny. Bench on the marked clips.
- **More evidence (2026-11-15 morning, logs `2026-11-15_081707` / `_094327` / `_102255`, P2): words cut at a piece boundary, ~10.** 10:49:44 «înainte de a **parca.** | **parcurge** câteva versete» → "before we **park**"; 09:59:46 «poate să se **cuplu.** | **cutremure**» → "can be **joined**. | shake"; 11:06:16 «această **nepărere.** | **neprihănire**» → "this **helplessness**"; 11:39:31 «viabilă **autor.** | **autentică**» → "a viable reality, **author**"; 11:09:12 «ordinat și **păstrat.** | **păstor**» → "ordained and **kept**"; 11:43:44 «să se **multe.** | **multiplice**» → "to be **many**"; 10:58:59 «hanul de **poposire.** | **popostire**» → "inn" twice; 11:53:38 «Harusus. | harul» → "Harusus." | "**the spring**"; 11:56:03 «muncă din asta **orcoolică.** | **alcoolică**» (workaholic) → "enters into a work of this kind of..." | "**alcoholic**"; 12:01:26 «**Dul** nu poate» (Duhul) → "**The world** cannot". The (a) rule (a last word that the next piece's first word starts with, or not in the lexicon, is dropped and carried) covers 8 of the 10.
- **Done (a) 2026-11-15 (branch `fix/2026-11-15-morning`):** when the next piece follows within 0.7 s and its first word continues the previous caption's last word (4+ shared letters and a longer word for a known word, 3+ for an unknown one), the previous piece without that word and this one are translated again as one, in place of the previous caption (`live.cut_word_redo`). The first rule (2+ letters) fired on real word pairs on the sermons («bogat sau sărac.» | «sărat», «Dumnezeu mare.» | «măriți»), so it was tightened: 9 of the first run's 24 redos remain, 5 clear fixes, 4 harmless repeats. **Bench** (11-15 clips, 61 marked errors, still wrong on main → branch: 40 → 10; luigi-mitoi chrF++ 62.62 → 62.44, BLEU 35.36 → 35.15, WER 4.82 → 4.82 %; Gemma 0.68/1.23 → 0.69/1.21 s, recogniser 0.29/0.49 → 0.29/0.44 s median/p90). Still wrong 3 → 1: "authentic", "multiply" now right; «cuplu.» | «cutremure» (a real word, 2 shared letters) still "joined"; «păstrat.» | «păstor» is no longer caught.
- **Where the live Romanian transcript's extra errors come from (2026-11-15 evening, branch `seam-timing`).** The three corrected sermons through the recogniser alone (`scripts/stt_replay.py`: the live engine without the translator, and the same recogniser offline on pause-cut segments of up to 20 s, church phrases boosted in both), each aligned to the corrected Romanian transcript; an error the live run has where the offline run is right is caused by the live cutting and stitching (`scripts/stt_attrib.py`). Starting code 405fef7:

  | | br-random-1 | nelu-suciu (first 8 min) | luigi-mitoi |
  |---|---:|---:|---:|
  | corrected words | 4,453 | 1,135 | 4,589 |
  | offline errors | 200 (4.49%) | 32 (2.82%) | 164 (3.57%) |
  | live errors, recogniser's own text | 275 (6.18%), S159 D45 I71 | 51 (4.49%), S28 D4 I19 | 197 (4.29%), S110 D46 I41 |
  | (e) scoring noise (phonetic key, figures) | 6 | 4 | 17 |
  | both have it (the recogniser's, or the reference's) | 153 | 25 | 123 |
  | **live only** | **116** | **22** | **57** |
  | (a) within 2 words of a forced cut (17–21% of the words) | 24, 18 of them extra words | 10 | 19 |
  | within 2 words of a pause boundary | 24 | 6 | 24, 14 of them missing words |
  | (b) inside a chunk | 68 | 6 | 14 |
  | offline only (the live context got it right) | 43 | 10 | 37 |
  | (c) the app's repairs, recogniser's text → repaired text | +4 (277 → 281) | −5 (60 → 55) | **+18** (198 → 216) |
  | (d) words in phrases not shown; phrases taken for English | 5; 0 | 1; 0 | 0; 0 |

  - (a) and the pause boundaries are the stitching by text: a stub and its word both kept («mâncat ‖ mântuit», «dispoziție ‖ dispoziția», «mun ‖ munte»), a word heard again after a pause kept as new («sărac | sărat», «culoar | culoare», «dor | doar»), and a phrase said twice lost because the text alignment took the second for the first («priviți-vă, priviți-vă rog», «dacă nu te pricepi la plumbing… dacă nu te pricepi la electrică»: 10 words gone).
  - (b) is mostly one word heard differently with a different stretch of audio around it; the offline run has as many the other way (43 on br-random-1), so only the excess (~25) is the live pipeline's.
  - (c) is the empty-phrase fallback (idea 10): when the Romanian recogniser gives nothing for a chunk because its words were already committed, Parakeet's text of the same audio is taken and the words are said twice (luigi-mitoi 24:59, 17 words; br-random-1 «nu o au», «scumpă să răți»). «Christmas» → «Hristos» and the name repairs are right where they fire.
  - (e) `bench.py ref` counts every phrase that starts up to 1 s after the corrected stretch ends, so nelu-suciu's score carries ~9 extra words from the first uncorrected phrase (4.85% in the app run; 4.49% for the recogniser's text cut at the stretch's end). The reference keeps some of the offline recogniser's own mistakes («fragi și surori», «eroia aceea», «intras pe poarta», «tere și s descalificați»), so a live run that hears these right is counted wrong: the offline number cannot be reached.
  - Half of all chunks end in a forced cut (235 of 471 on br-random-1). The voice detector calls the cut frame speech in 42% of them (≥ 0.9 in 25%); nelu-suciu 29%. 35% of the forced chunks are under 3 s (46 of 235 under 1 s): the pause tracker's 5.5 s speech clock and the 5.5 s buffer clock (`max_commit_s`) run apart, so a cut by one is followed by a cut by the other.
  - Auto mode took no phrase for English on any of the three (0 of 913 shown phrases).
- **Built (2026-11-15 evening, branch `seam-timing`, `live.timed_commit`, on by default):**
  - `NemoSTT.words()` gives each Romanian word's start and end from the CTC beam search's own frame path (80 ms frames; the same text as before on 200 random windows, no extra time). Word starts sit within ±0.1 s of the voice detector's onsets.
  - `ContextSTT` commits by time: the new words are those after the point in the audio where the committed text ended. Word times move between hearings (at the end of the audio the recogniser fires up to ~0.25 s early), so that point is then anchored on the last committed words, looked for within 3 words and 0.8 s of it.
  - At a forced cut the engine passes the audio it already holds past the cut (0.3–2.5 s) and how long the voice had been silent before it. A word that began before the cut (or before that silence) and ends inside the audio goes with this chunk, heard whole; one that ends within 0.16 s of the end of the audio waits (`live.commit_edge_s`).
  - A forced piece does not end on a word that leans on the next one (`segmenter.held_back`: «de», «că», «nu», «și»…, or the lone first word of a new sentence): it waits for the next chunk. The old code did not hear these at the edge; hearing them made 27% of forced pieces end "open" (7% before), which the pipeline holds, and the first word came later (within 8 s: 85.8 → 80.1%). With the rule 2% end open.
  - A clause the recogniser lost and finds with the next chunk's audio (3+ words in a row that the last chunk's audio did not give) is said late instead of never (once in 29 min on br-random-1: 6 words).
  - A forced chunk with no Romanian text while the recogniser has words in hand gives `clause.ro_wait`; the empty-phrase fallback does not take Parakeet's text for it.
  - Parakeet (English, language detection) has no word times here and is stitched by text as before. So is the Romanian recogniser if its times cannot be had (another NeMo version; three calls without times and it stops asking): this morning's seam redo (`live.cut_word_seam`) stays for that case only. With the times neither it nor this morning's spelling rule (`live.cut_word_redo`) fired once on the three sermons and the clips.
  - Reviewed by a second model with fresh eyes; its two real findings (a re-heard run taken for a lost clause, a stretched word said twice) are fixed, each with a test.
- **Bench (2026-11-15 evening; app replays x3, quality, gain 0, no names; 405fef7 without / with this morning's seam redo → 8625355; offline = the same recogniser on pause-cut segments, church phrases boosted, the older stored figure in brackets):**

  | Romanian transcript WER (all phrases) | offline | 405fef7, no redo | 405fef7, redo | **8625355** | extra words (redo → now) |
  |---|---:|---:|---:|---:|---:|
  | br-random-1 | 4.49% (4.22%) | 6.42% | 6.31% | **5.25%** | 82 → 39 |
  | nelu-suciu, first 8 min | 2.82% (2.56%) | 4.41% | 4.05% | **3.70%** | 14 → 8 |
  | luigi-mitoi | 3.57% | 4.79% | 4.71% | **3.70%** | 55 → 20 |

  - Share of the live-to-offline gap closed, from the no-redo run (the state BENCHMARK Phase 3e was measured in): br-random-1 60%, nelu-suciu 45% (42 words; half would be 41), luigi-mitoi 89%. From this morning's code with its redo: 58%, 28%, 89%.
  - luigi-mitoi English translation: chrF++ 62.5 → 63.1, BLEU 35.3 → 37.3.
  - Seam catalogue (`logs/bench/seams.json`, rebuilt from these runs: 18 cut words, 2 re-heard duplicates, 1 pair of real words; clips 12 min): right 4 of 21 without the redo, 15 with it, **19 with the times and 2 not reproduced** (the word is heard once).
  - Recogniser alone (`scripts/stt_replay.py`), 405fef7 → 8625355 (offline): br-random-1 6.18 → 5.25% (4.49%), nelu-suciu 4.49 → 3.70% (2.82%), luigi-mitoi 4.29 → 3.64% (3.57%). Live-only errors 116 → 81, 22 → 16, 57 → 34; offline-only 45, 10, 36. On luigi-mitoi, the one sermon whose reference was not made from the offline text, live now equals offline.
  - Latency, real time, first 12 min of luigi-mitoi (148 phrases, 145 the same in both runs), 405fef7 → 8625355: recogniser 0.39/0.44 → 0.42/0.45 s, Gemma 1.23/2.02 → 1.21/1.90 s (median/p90); first word median 6.71 → 6.72 s, p90 9.37 → 9.37 s; within 6 s 35.1 → 34.5% (52 → 51 phrases), within 8 s 85.8 → 84.5%. Each phrase comes 0.03 s later: the recogniser hears up to 2.5 s more audio per forced cut (+0.02 s).
  - `bench.py ref` now counts a phrase when its middle lies before the end of the corrected stretch (it took every phrase starting up to 1 s after it, whole: nelu-suciu carried ~9 extra words; 5.20% / 4.85% under the old rule are 4.41% / 4.05%).
- **What is left (br-random-1, recogniser alone: 234 errors against 200 offline).** No stitching: 1 stub and 1 repeat in 29 min. It is one word heard differently with a different stretch of audio around it («am/a păstrat», «dăm/luăm», «ar vrea/avea»), as often the other way (45 offline-only), plus the reference's own leftovers from the offline text («fragi și surori», «eroia», «intras», «fără copii, fără tineri» where the live «văd copii, văd tineri» is what is said).
- **Other sources checked (2026-11-15 evening):**
  - *Context text against context audio:* 28% of chunks run above 3 words/s, so the 18 words kept as context text cover less than the 6 s of audio. With the times it no longer matters for Romanian; Parakeet's stitching still uses it.
  - *Cut placement:* the forced cut lands on a frame the voice detector calls speech in 42% of cuts on br-random-1 (29% on nelu-suciu). With the audio past the cut heard it no longer cuts words. 0.8 s of audio after the cut instead of 0.3 s: same WER. One clock for forced cuts instead of two (35% of forced chunks are under 3 s because the pause tracker's speech clock and the buffer clock run apart): 235 → 178 forced cuts, WER 5.39 → 5.48%, the first word waits 4.61 → 4.96 s for the cut. Neither kept.
  - *A pause-ended chunk losing its last word:* not seen. What was seen is the reverse: the last word, right the first time, heard again as another word once the pause is cut out of the context («sărac | sărat», «culoar | culoare», «pleci | plece», «dor | doar») and kept as a new word by the text stitching; this morning's redo then dropped the right one. By time it is the old word.
  - *The c10a191 → 03e38eb regression (idea 27):* automatic gain is not it any more (br-random-1 with the gain fixed: 5.43% against 5.39%). The words added were the empty-phrase fallback's (Parakeet's text for a chunk the Romanian recogniser had already said: 17 words at luigi-mitoi 24:59) and the stitching itself; both are gone.
  - *Auto mode:* no phrase of the three sermons was taken for English.
  - *Names:* ten names entered as on a Sunday (`logs/bench/terms_corr_sermons.txt`): br-random-1 5.39 → 5.37%, nelu-suciu 3.70 → 3.61% (one or two words each).
  - *9 s of context instead of 6 s:* br-random-1 5.39 → 5.23% (7 words); the recogniser hears 3 s more per call. Not changed.
- **Next most valuable:** (1) the reference: a second pass over the br-random-1 and nelu-suciu corrections where live and offline disagree (~125 places, listed by `scripts/stt_attrib.py --list`), since the offline text's own mistakes are now a visible part of the remaining "gap"; (2) `context_s` 9 on all three with the real-time latency; (3) word times for Parakeet (parakeet-mlx gives them), so English gets the same commit and idea 16's overlap trim can go.
- **Ceiling check (2026-11-16, `scripts/stt_ceiling.py`; offline segments, 10,177 reference words, the live recogniser 396 errors = 3.89%).** What the errors left could be fixed by, before building anything:
  - *The right text is often there.* Best candidate of the recogniser's own beam-8 list (picked with the reference in hand, so a ceiling): 277 errors (−30%); of a beam-32 list: 207 (−48%); best of beam 8 or Whisper large-v3 per segment: 215 (−46%); best of every list and recogniser: 128 (−68%). Beam 32's own first choice: 396, no gain. The other decoder (TDT greedy) alone 570, Whisper alone 616, Parakeet 1,944.
  - *Plain rules do not find it.* Word votes with the live recogniser as backbone: 376 at best (beam 8 + TDT + Whisper, −5%); the list's candidate nearest to Whisper and/or TDT: 379–398. Whisper has 43–68% of the live recogniser's wrong words right, and 2–3 times as many wrong that it has right. A chooser needs to know the language (a stronger language model, Gemma scoring the candidates) or the sound (the TDT decoder scoring the CTC list): not measured yet. The right candidate is close: usually #2 or #3, median 1.07 score units under the top.
  - *By kind (396):* short words 185 (47%: missing 68, extra 48, one for another 69: «că», «o», «la», «a», «în», «cu», «să», «și»), word endings 55 («copii/copiii», «matur/maturi»), sounds alike 47, a different word 58, names 26, other missing/extra 18, diacritics 5, numbers 2. About 20 are the reference's spelling («Iisus», «prooroci», «-s» for «sunt», «n-» for «nu»), not hearing.
  - *The live transcript's wrong words* (final code, `l7-*`): the offline recogniser has 24–53% of them right, Whisper on whole segments 40–64%.
  - *Parakeet answered hundreds of `<unk>`* on 9 of 418 and 6+ of 556 segments (short ones too). Nothing in `src/live` strips it; none in the app replays' logs so far.
- **Fine-tune prepared, not run (2026-11-16, `src/finetune/`).** Only 1.27 h of Romanian sermon speech may be trained on (br-random-1, nelu-suciu, br-recalo); the default set is 0.96 h (0.54 h corrected, 0.42 h labelled by the recogniser where Whisper agrees within 10%), luigi-mitoi held out by recording and by content. NeMo's TDT loss is CUDA-only: `tdt_loss.py` is the same maths in PyTorch and trains on the Apple GPU (7.3 s per step, about 40 min for 10 epochs, 10.5 GB). `eval_checkpoint.py` scores a checkpoint with the live decoding and reproduces luigi-mitoi 3.57% (164) and the Bible 4.86%. With br-random-1 and nelu-suciu in training, only luigi-mitoi and the Bible stay clean measures. More audio is the lever: under an hour is below what published self-training used.
- **Fine-tune run `st1`: no gain on the held-out sermon (2026-11-16, `models/finetuned/jackrabbit-st1`).**
  The prepared settings on the 0.96 h set (10 epochs, 290 steps, top 4 encoder layers, LR 5e-5), batch 2 × 16
  instead of 4 × 8: about 2 GB instead of a 10.5 GB peak, 7.5 s a step, 37 min. Live decoding (CTC + 4-gram,
  beam 8, church phrases), offline segments, errors base → st1: **luigi-mitoi (never trained on) 164 → 168
  (3.57 → 3.66%)**; Bible set 193 → 179 (4.86 → 4.51%: nothing forgotten); the two sermons it trained on,
  br-random-1 200 → 192 (4.49 → 4.31%) and nelu-suciu 32 → 24 (2.82 → 2.11%), which are no longer clean
  measures. Dev set (60 segments, greedy): CTC 11.0 → 9.1%, TDT 10.1 → 9.3%, nearly all in the first two epochs.
  So the model moved, but with the language model on top the gain on an unseen preacher is nil, and even the
  training sermons kept 90% of their errors. What this does not answer: whether training on a regular preacher
  helps that preacher's next sermon (no clean sermon of a trained speaker exists to test on), and what several
  hours of audio would do. Not adopted; the live recogniser is unchanged.
- **Chooser test, part 1: judging the candidates as sound does not help (2026-11-16, `scripts/stt_choose.py`).**
  Each candidate of the beam lists got the probability the model's other decoder (TDT) and its CTC head without
  the language model give its text, and a weighted sum with the list's own score picked; weights tuned on two
  sermons, tested on the third. Beam-8 list: 396 errors → 393–396 held out (384 with the weights fitted to the
  same data); the other decoder makes about as many segments worse as better (25–27 each). Beam-32 list: 393–403
  held out. With the other decoder's and Whisper's own texts added as candidates: 396–412. Both judges hear
  through the same encoder as the recogniser, so they share its mishearings. Gemma as a judge (the candidate as
  a continuation of the transcript, and asked to pick) and Whisper as a judge (its decoder made to score each
  candidate) are written and tried on 12–30 segments only: the full runs were stopped twice because they filled
  the Mac's memory (two MLX models at once: 39 GB of swap; then Gemma alone, 12.5 GB peak, while the Mac was in
  use). On 12 segments Gemma alone picked worse than the recogniser (8 and 9 errors against 5). Not a result yet.

## 26. Name repair turns a real word into today's name: «înșeală» → «Ionela» — `done` (2026-10-25): a lower-case word with no cue needs sound ratio ≥ 0.85 and is never a name after nu/ne/te/se/să…; bench on all 14 logs with the saved names: 12 → 6 repairs, the 6 false ones gone (Ionela ×4, Barbu, Dragoș), the 6 real kept · **reopened `next` 2026-11-01**: correct names repaired into saved ones (a man's first name → a woman's saved name)

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
- **Back with saved names (2026-11-01 11:19:28-11:19:43, log `2026-11-01_090734`, shown,
  marked):** the recogniser heard the names RIGHT and the repair changed them to other people
  from `names_usage.json` (no names typed today): «fratele **[man's first name]** [surname]» → «**[woman's saved name]**»
  → "**sister** [woman's saved name] [surname]" (wrong person, wrong sex); a surname →
  another saved name; two more first names → saved nicknames.
  The capitalised first name after «fratele» counts as a cue, so the 0.85 sound ratio of the
  2026-10-25 fix doesn't apply. Change: never repair a word that is itself a common Romanian
  first name (a first-names list), never change «fratele X» into a
  female name, and only repair the *whole* «first last» pair against a saved full name.
- **Seen again after done (2026-11-01 morning, log `2026-11-01_090734`, P1): 9 wrong repairs
  in one service (from `names_fixed`), a place name among them.** 09:23:13 «în Oltenia, la
  **Târgu** Jiu» → «Tarcau» → "at **Tarcau** Jiu" (a town turned into a saved surname); 11:29:43
  «prin fratele **[man's first name]** [surname]» → the woman's saved name → "through **sister [saved name]** [surname]" (the
  second time today, after 11:19:28); 11:19:19 / 11:19:43 a first name → its nickname twice; 11:19:38
  a surname and a first name → saved names; 09:12:58 a family name respelled ×2 and 09:13:07
  another respelled (spellings unchecked, but the recogniser's were as likely right). Only
  one surname respelling (3×) was a real fix. Add to the change above: never repair a
  word in the Romanian lexicon or a place-name prefix (Târgu, Satu, Baia, Câmpulung…), and
  bench repairs on today's log: today 1 right kind vs 8 wrong.
- **Seen again after done (2026-11-08 morning, log `2026-11-08_090143`, P1): 14 repairs, 6 wrong,
  with today's names entered** (from `names_fixed`; the live notes blamed the recogniser for two of
  them, but the recogniser was right). 11:04:13 «Tată, **Fiule** și Duhul Sfânt» → «Tată, **<name>** și
  Duhul Sfânt» → "Father, **<name>** and the Holy Spirit" (the Trinity); 09:27:05 «literatură
  **creștină**» → «Cristina» → "**Cristina's** literature" (a lexicon word); 11:07:41 an engagement
  announcement, «fratele **<man's name>** … cu sora **<woman's name>**» → each repaired into a saved name of the
  other sex → "**brother <woman's name>** … **Sister <man's name>**"; 09:28:29 «fratele **Ioan**» → a woman's
  saved name; 10:27:31 «May» → «Mihai» (inside garbage). Right ones: a surname respelled ×3, a first
  name's capital ×2, a first name to the family's spelling ×3. **Change (all three are in the 11-01 entry,
  still not done):** never repair a word that is in the Romanian lexicon (fiule, creștină); never
  repair a common first name (Ioan, Michael, Nadia) into a different one; never cross sex (fratele →
  female name, sora → male name, or a man's name → a woman's).

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

## 28. Romanian recogniser mishears common sermon words — `partly done` (2026-11-09): evening words boosted + English loanwords kept; imanența / falimentul still misheard · 2026-11-15: «Christmas» → «Hristos» in Romanian; mărit să fie numele Lui, chipul lui Hristos, slava/slavei Domnului, pocăit/ă/i boosted

- **Evidence (2026-11-01 morning, Quality, log `2026-11-01_090734`; captions paused, so
  recognition only; marked in the recording):**
  - 09:09:31 «Și parcă nu e suficient **sângeri**, că doar au picat o treime d[in îngeri]»
    (meant «suficienți **îngeri**», enough angels: a third of the angels fell). The word
    boundary slid («suficienți îngeri» → «suficient sângeri») and the result means "bleed".
    09:08:53 the same word came out as «suficienți **înger**» (singular). Recogniser.
  - 09:09:34 «eu nu-l **sujesc** pe Dumnezeu» (slujesc, I serve): a non-word, a dropped «l».
  - **09:29:24, shown on the captions page: meaning reversed.** «și dragii mei, **nedejdea**, că
    rugăciunile noastre» (nădejdea, the hope) → "and my dear ones, **hopelessness**, that our
    prayers." A one-letter non-word, and Gemma read «ne-» as a negation. Marked. Worst kind:
    the opposite of what was said, in a sentence about hope.
    The near-miss repair only looks at today's names, so it doesn't fix this.
  - 09:34:46 (shown) «cu respect să vă **preocupați** locurile» (vă ocupați locurile, please
    take your seats; 09:16:50 heard it right) → "respect that you **care about the places**".
    A stock announcement worth a `CHURCH_PHRASES` row: «să vă ocupați locurile».
  - 10:00:27 (shown) «[Lăudat] **cântat** să fie numele lui Dumnezeu. **Pubiților**, și în
    această dimineață» → "let the name of God be sung. **To the people**, and on this morning."
    «Iubiților» (beloved, the usual address) lost its «I»; marked. Again 10:03:01 «cu lumea.
    **Pubiților!**» → "**To the public!**"; 10:43:47 the sermon opens with it → "to the public,
this morning": 4× today, systematic for this speaker. `CHURCH_PHRASES` row:
    «iubiților» / «preaiubiților».
  - 10:50:52 (shown) «să stăm **că mireți**» (smeriți, humble, most likely) → "to remain
    **watchful**": a non-word split in two, Gemma guessed. smerit/smeriți for the list.
  - 11:04:14 (shown, marked) «[umblarea noastră] **nepăsători**. Însă **înțelepți ne-ar
    socoti** Dumnezeu» (careless; but God would count us wise) → "**sinners**. However, it is
    wise for me to be, and this is also God's thought": «ne-ar socoti» heard «să-mi fi și
    aceasta nesocotește», then Gemma swapped «nepăsători» (careless) for "sinners".
  - 11:13:18 (shown, marked) «să mănânci Paștele cu **ucenicii** tăi» (Luke 22:15) heard
    «**șenicii**» → "to eat the Passover with **your children**"; 11:13:24 «te rog
    **cercetează** fiecare inimă» heard «**ce setează**» → "that every heart may be **set
    apart**". Both church words: ucenic/ucenicii, cercetează for the list.
  - 11:14:19 / 11:14:24 (shown, marked) «lasă peste noi, o, Doamne, **duh de pocăință**» (a spirit
    of repentance) heard «du de **păcăliți** / **păcăliță**» → "the fruit of **the sinners**" |
    "sinners.": pocăință (repentance) for the list, a core word in this church.
  - 11:18:56 / 11:19:04 (shown) «va duce **rodul viței**» heard as one word «**Rodoviței**» →
    "the wine" (a guess), then «**Rulviței**» → "will bring **the Rulviței**" (left as is).
    «rodul viței» (fruit of the vine) for `CHURCH_PHRASES`; it comes every communion Sunday.
  - 10:23:21-10:23:29 (shown, marked): English words inside Romanian announcements, «va avea
    loc un **fundraiser, bake sale**, prăjituri, dulciuri… **bake sale**-ul va avea loc în sala de
    gimnastică» → "**Fraser Bell**, cookies, sweets…", "**Basel** will take place in the
    gymnasium". The Romanian recogniser spells English loanwords by ear. Common ones in this
    church (fundraiser, bake sale, youth, worship…) could go in `CHURCH_PHRASES` / Names & words
    so the decoder can write them, and a glossary row keeps them in English.
    More, 10:25:46-10:26:28 (marked): «Samaritan's Purse, Operation Christmas Child,
    shoebox-urile» → «**Samar and Spars**», «Sammartin Spurs, Aroon Christos Child, acel Shub
    axes» (dropped from the English entirely), «**șubbax** / Subbax» → "this **shubbax**";
    «sunt în B3, în **hallway**» → «în Bet 3, în **Hollywoy**» → "in **Hollywood**". And the
    loan figure (idea 5): «…[suma] de euro» | «**dolari.**» — the currency came in the next
    piece, so the screen said euros, then "dollars.".
- **More evidence (2026-11-01 morning, same log, P1): what the English said for these.**
  09:09:31 «nu e suficient **sângeri**, că doar au picat o treime din cer» (enough angels: a third
  fell from heaven) was captioned "it is as if **His blood is not enough**, that only a third of
  **the blood** has fallen from heaven": invented doctrine, the worst line of the service. Also
  09:25:59 «eu am dus **alăturile** Bisericii pretutindeni» (likely «salutările», I carried the
  church's greetings everywhere) → "I have traveled **the sidelines** of the Church"; 09:22:26
  «**einerarul**» (itinerariul) → "**the honor** I have received"; 09:28:13 «și **selește**» (se
  veselește, rejoices) → "and **holy**"; 10:24:49 «rugăciunea cu **măslinul**» → "the prayer with
  **the olive**" (unclear; check the clip). For the boost list: salutările, itinerariul,
  se veselește.
- **Change:** (a) add the sermon vocabulary that comes out wrong (îngeri/îngerii, nădejde, slujesc/
  slujim/slujire, …) to `engine.CHURCH_PHRASES`, the same boost the stock church phrases get;
  (b) widen the near-miss repair to a short list of church words for non-word tokens only
  (sujesc → slujesc), never for real words (idea 26's lesson).
- **Effort:** (a) tiny, bench on the marked clip and the corrected sermons for WER; (b) small.
- **More evidence (2026-11-08 morning, log `2026-11-08_090143`, P2): 9 church words misheard,
  each a wrong English line** (plus two heard right and still wrong: 11:51:28 Matt 12:34 «din **prisosul**
  inimii» → "the **prison** of the heart", and 11:48:38 «iubiților» → "**lovers**" inside the Jer 6:19
  splice; rows for idea 9). 11:50:10 «cerul **slavei**» → "the heaven of **slaves**";
  11:57:56 «**Preaiubiților**» → «Preaibiților» → "**For the prophets**"; 11:55:18 «naufragiaseră» → «n-au fragiaseră» →
  "were **not afraid**"; 11:35:46 «**Domnul** spunea» → «omul» → "**the man** said"; 11:15:03 «corul» →
  «corpul» → "the **body** of the church"; 09:24:03 «Iehova» → «Șehova» → "**the Word of** God";
  09:28:05 «**mlădiță**» → «cam lădiță» → "but **a limited one**"; 09:11:05 «viță» → «vită» → "a **cow**"
  (and 09:11:07 "a fruitful cow" carried on); 11:42:48 «procrastinarea» → «prora» → "the **rudder**".
  For `CHURCH_PHRASES`: preaiubiților, iubiților, cerul slavei, prisosul inimii, corul bisericii,
  Iehova, mlădiță, viță roditoare, să vă ocupați locurile (09:07:28 «să ocupăm lucrurile» again).
- **More evidence (2026-11-08 evening, log `2026-11-08_173244`, Best, P2): church and sermon words misheard, each a wrong English line.** 18:56:25
  «**împărăția** lui Dumnezeu» → «împărțirea» → "signs for **the division of God**"; 19:54:52 «**imanența** lui» →
  «Eminența» → "**His Eminence**" (19:53:25 heard it right: "His immanence"); 19:57:20 «**falimentul**» → «filamentul» →
  "**the thread**"; 19:57:12 «robiți și **învinși**» → «înviți» → "shaped and taught"; 18:12:23 «câteva **ore**» → «ori» →
  "a few **times**"; 18:56:57 «să aducem cauzele» → «**Sam și** cauze» → "**Sam and causes**"; 19:33:29-19:55:47 the
  sermon's word «**sintagmă**» heard «simptomă / simă / sma / st / substanta» ~15 times → "symptom", "veil", "shadow",
  "hypocrisy", "substance", "reality" (see new idea 36). For `CHURCH_PHRASES`: împărăția lui Dumnezeu, imanența,
  falimentul, robiți și învinși, sintagmă.
- **More evidence (2026-11-08 evening, log `2026-11-08_173244`, Best, P1): English words inside Romanian, one reversed on screen.** 18:47:59-18:48:05 «avortul este
  considerat **Hellker** / **Helker**, este considerat Choice» (English *healthcare*) → "abortion is considered
  **legal**", then "**illegal**, it is considered a choice": the caption contradicts itself on abortion. Also 18:17:02
  «când îi aduceți la **Sandis**» (Sunday school) → "to Sandis"; 19:42:02 «să fii cu minte pe **bas**» (bus) → "in **the
  game**". **Change:** add healthcare, Sunday school, bus, outside to the English-loanword list (decoder boost +
  glossary keeps them in English, morning's done item); an unknown capitalised word next to «considerat» should go to
  the translator as unknown, not guessed. Effort: tiny.
- **Partly done 2026-11-09 (branch `fix/2026-11-08-evening`):** `CHURCH_PHRASES` + glossary: împărăția lui Dumnezeu, imanența, falimentul, robiți și învinși, sintagmă; healthcare, Sunday school, bus, outside kept in English. **Bench:** evening still wrong 21 → 20 («bas» → bus now uncertain instead of "the game"); imanența → "His Eminence" and falimentul → "the thread" still misheard (the boost isn't enough against a closer word; needs acoustic adaptation or a repair rule); WER luigi-mitoi 4.79 → 4.82 %, br-random-1 6.42 → 6.47 %, nelu-suciu 5.37 → 5.37 % (+1-2 words, noise); chrF++ 62.55 → 62.56; Gemma 0.64/1.05 s, recogniser 0.23/0.26 s.
- **More evidence (2026-11-15 morning, logs `2026-11-15_081707` / `_094327` / `_102255`, P1): the recogniser's misses this Sunday, each a wrong line (details in the live notes below).** A speaker's catchphrase «**mărit să fie numele Lui în veci**» heard «mărească, ei sunt numele în veci» / «Măria că ei sunt…» ×6 (09:33:08 "great, they are the names forever", 09:35:54 "**Mary**, for that is the name forever", 09:37:52 "Increase, for they are the old names"); the sermon's key word «**chip**» (Col 1:15, image) → "what **a face**", "what **chip**", "this **chip** of Christ", "**the face** of Christ" (11:33:00, 11:33:39, 11:34:18, 12:01:30); «**slavei**» → "**the slave** of the Lord Jesus Christ" ×2 (09:59:09, 10:00:51); «**Hristos**» → «Christmas» ×2 shown (09:10:19 "Christmas.", 11:29:00 "**Christmas is risen!**": the side effect of keeping English loanwords, 11-09); «**pocăit/pocăită**» → «păcălit» ×2 (10:55:45 "an old man who had been **deceived**", 11:02:22 "his wife was being **cheated on**"); 09:10:11 «aude» → "**dare**". English inside Romanian (guest pastor): 11:47:50 / 11:48:12 *fall in love* → "F. in **the lava**", "**the will of God**"; 11:49:31 *cheating* → "**citing**"; 09:51:11 *small sparks will kindle fire* → "Smalls Park, Will Kindle, fire." (shown as is); 09:52:17 *fail and destruction* → "Fal and Destruction". **Change:** «Christmas» is never kept in Romanian mode (→ «Hristos» unless «Crăciun» is near); `CHURCH_PHRASES`: mărit să fie numele Lui, chipul lui Hristos, slavei / slava Domnului, pocăit / pocăită / pocăiți (boost only; never rewrite «păcălit», idea 2's lesson); glossary chip (church) = image / likeness; English idioms a bilingual preacher uses (fall in love, cheating, fail) for the loanword list.
- **Done 2026-11-15 (branch `fix/2026-11-15-morning`):** «Christmas» in a Romanian piece is «Hristos» unless «Crăciun» is in it or the piece before ("Operation Christmas Child" kept); `CHURCH_PHRASES` + mărit să fie numele Lui, chipul lui Hristos, slava / slavei Domnului, pocăit / pocăită / pocăiți (boost only). **WER phrase by phrase** (the recogniser alone on the three corrected sermons, each phrase group added to the stock list): luigi-mitoi 5.25 %, br-random-1 6.87 %, nelu-suciu 6.78 % for the stock list and for every group and all of them (not one word changed; the replay's full-pipeline WER 4.82 / 6.49 / 5.37 % → 4.82 / 6.38 / 5.20 %). On this morning's audio the boost turns «păcăită / păcăliți» into «pocăită / pocăiți»; «mărit să fie numele Lui» needs weight 2 (one of three then right), which writes names into garbled audio, so it stays at 1; «slavei» and «chip» were heard right (their errors were translation, idea 9). **Bench** (11-15 clips, 61 marked errors, still wrong on main → branch: 40 → 10; luigi-mitoi chrF++ 62.62 → 62.44, BLEU 35.36 → 35.15, WER 4.82 → 4.82 %; Gemma 0.68/1.23 → 0.69/1.21 s, recogniser 0.29/0.49 → 0.29/0.44 s median/p90). Still wrong 3 → 2: "Christ is risen!" now right; the catchphrase still "old names".

## 29. The empty-phrase fallback let Portuguese through: «churrasco» in the communion prayer — `next`

- **Evidence (2026-11-01 11:14:30, log `2026-11-01_090734`, Best, shown on the captions page,
  marked):** during the prayer over the cup the Romanian recogniser returned nothing (`ro: ''`);
  the multilingual one (Parakeet) heard «**Tudo é sereia, Domne. Casa podemos fim para
  churrasco.**» (Portuguese), the source became «Casa podemos fim para churrasco.» and the
  caption was "**We can finish at the barbecue.**", in the middle of a communion prayer. Idea
  10's rule (accept the multilingual text in RO→EN unless it scores as English) let a third
  language through, and the confidence gate didn't stop it (the words are real, just not
  Romanian).
- **Change:** the fallback takes the multilingual text only when its words are mostly in the
  Romanian lexicon (the gate's `lexicon()["ro"]` share, the same test idea 17 uses for
  garbage), not merely "not English"; otherwise drop the piece (`empty_stt`). One short
  Romanian word (idea 10's «binecuvinteze.») still passes. Bench: idea 10's clip and this one.
- **Effort:** small (one condition in the fallback, two clips).

## 30. Quickest (NLLB) produces a sexual phrase the swear filter doesn't catch — `idea` — P1

- **Evidence (2026-10-30 rehearsal replay, log `2026-10-30_172815`, Quickest):** 17:28:42 «și pe
  tine o să te **pui**.» (a cut piece: "it will [rub off] on you too") → "and **you're gonna get
  laid**." The Best run of the same audio (log `2026-10-30_172253`, 17:23:22) said "it will
  also overwhelm you." Same family as idea 27's NLLB "Holy shit": NLLB on a cut Romanian
  fragment reaches for a colloquial English idiom. The swear list (commit 8cde0ea) is words,
  and "laid" is not one. Same run, harmless but silly: «vierul» → "The owl", «adevărata viză»
  → "the real visa", «rămâneți!» → "Stay, stay, stay, whoa, whoa!".
- **Change:** add multi-word phrases to the swear list (get laid, get some, screw…) and match
  them on the English caption; on a hit in Quickest, drop the line rather than mask a word.
- **Effort:** tiny (list + a test).

## 32. NLLB drops whole sentences from multi-sentence English pieces — `next`

- **Evidence:** idea 13, 2026-11-06, at least six times in one sermon, and they are often the
  words the point turns on: "**Pride.**" (20:41:30), "…**is obedience.**" (20:51:43), "**I speed up.**"
  (20:32:06), "**I'm using you. I'm showing my glory.**" (20:53:10), "**Sometimes it's our own
  foolishness.**" (20:30:28). `mt_truncated` was 0 for the whole service, and a check over the log
  for translations under half the source's length found none of them: most of the words survive,
  so neither flag sees it.
- **Change:** split a piece at sentence ends before NLLB and translate each sentence, joining the
  results. A length check alone will not catch this. Also strip um/uh first (idea 13).
- **Effort:** small. Time it on the machine bench (each sentence is its own NLLB call); replay
  the marked clips from `2026-11-06_195121`.
- **More evidence (2026-11-08 morning, log `2026-11-08_090143`, P1): 12 times in one English
  sermon, twice where it matters most.** 10:38:30 / 10:38:35 "I'm not talking about | **continuous
  abuse**. You need to seek help" → «nu vorbim despre asta.» | «Trebuie să cauți ajutor» (the abuse caveat
  gone: the sermon then reads as "forgive abuse"); 10:50:12 "**instead of** being open to the message" →
  «inima lor **era deschisă**» (reversed); 10:38:16 "that we may **continue to forgive**" → «să **ne ierte**»
  (that He forgive us); 09:48:02 "and distract them" → «**să-i distragem**» (we distract them, in a
  prayer); 10:49:06 "retribution on my | brothers" → «răzbunare **pe mine**»; 10:41:02 "I make | peace" →
  peace gone; 10:39:59 "I forgive him before | you ask" → «L-am iertat înainte»; 09:47:40 "Let us enter
  into this prayer and pray" → half dropped; single sentences dropped: 10:33:53 "wiped off.", 10:37:08
  "That's enough.", 10:53:52 "you got something coming", 10:37:21 "unforgiving servant?". Two causes: a
  sentence NLLB skips inside a piece, and a clause cut at a piece end that NLLB translates as complete.
  **Change:** split at sentence ends before NLLB, and carry the words after a piece's last sentence end
  into the next piece instead of translating a fragment.

## 33. `livectl monitor` shows only the Romanian while Hide is on — `idea`

- **Evidence (2026-11-01):** with `display.show_hidden` on, the translation is made and shown on the
  captions page, but the digest prints `paused: <source>` with no English, so monitoring missed
  the translations until the log was read by hand. Those before 09:27 were read only in review.
- **Change:** print the same RO / EN pair for `paused` phrases that have a translation, marked
  as hidden from ProPresenter. Effort: tiny.

## Review of the 2026-11-01 and 2026-11-06 services

- **Sunday 2026-11-01** (`2026-11-01_090734`, 965 phrases, Best, Hide on throughout): recogniser 0.25 s,
  translation median 0.73 s RO→EN and 0.66 s EN→RO, first word on screen about 6 s (p90 under 10 s).
  Speech mostly RO (793 phrases) with 169 EN. Name repair fired 11 times, most of them wrong (idea
  26), with no names entered that day. Worst captions: "We can finish at the barbecue" in a
  communion prayer (idea 29); «sister» for a brother (idea 26); "hopelessness" for hope (idea 28).
- **Friday 2026-11-06** (`2026-11-06_195231` to `_205911`, 524 phrases, almost all EN→RO, installed
  app 0.1.2, the Amen fix not in it): translation median 0.60 s, first word 5.8 s (p90 9.6 s), no
  backlog. Input good (speech about -20 dB). Fluent overall. The problems are NLLB's (idea 32,
  «în jurul valorii de», word choice), and they support trying Gemma on EN→RO again on the bench.
- **More from reading the rest of Sunday (not seen live):**
  - 09:09:31 the marked «sângeri» line in full: "And it is as if **His blood** is not enough, that only
    **a third of the blood** has fallen from heaven" (idea 28: about the angels who fell).
  - 09:23:13 the town «Târgu Jiu» was repaired into **a surname from the saved names**, the place
    gone (idea 26: a place after «la» / «în» is not a person).
  - 09:10:42 Proverbs 29:25 «frica de oameni este **o cursă**» (a snare) → "is **a race**"; 11:31:31
    «**îndurarea** ta» (Your mercy) → "Your **endurance**"; 09:07:59 «**uităm** fața» (we forget) → "we
    **look at** the face" (the next piece had "forget the name" right). Rows: cursă, îndurare.
  - 11:33:49 «să nu lipsească din niciun **cort**» → "any tent": faithful, «cort» meant literally here.
- **More from Friday (not seen live):** 20:20:42 "We are ninety-two thousand away" (from a fundraising
  goal) → «Suntem la 92.000 de **kilometri**»; 20:20:25 "caroling practices" heard "**Carolyn**"; 20:50:52 a
  man's "I" again «frustrată». Amounts need a currency or goal in context: flag for the guard.
- **Do first, by what it costs on screen:** idea 29 (a third language), idea 26 (real names
  changed), idea 32 (the point of the sentence dropped), then the verse repeats in idea 3.
- **Redaction note:** this repository is public. Service evidence here uses placeholders for people,
  prayer requests, money and local places; the real text stays in the local logs and clips.

## Live notes, Sunday 2026-11-08 (installed app 0.1.3, Best, log `2026-11-08_090143`)

Monitoring joined at 09:51; 09:06-09:51 was read from the log afterwards (no recording before 09:51:38).

- **«vită» for «viță» spreads through the context (idea 28 + idea 4), shown:** 09:11:05 Psalm 128
  retold, «nevasta lui este ca o **vită**» (cow; meant «viță», vine) → "whose wife is like **a cow**";
  09:11:07 the next piece heard «viță roditoare» right, yet came out "**a fruitful cow**": Gemma
  carried its own previous wrong word forward. Same Psalm read three minutes earlier was right
  (verse match). Change: «viță roditoare» / «ca o viță» as a glossary + decoder-boost row, and
  when the previous pair's translation is a recogniser slip the current piece corrects, let the
  current source win (prompt line: "the source wins over your earlier translation").
- **Word meanings (idea 9 rows):** 09:08:04 «familiile care sunt **rânduite**, care sunt la rând»
  (scheduled, next on the list) → "who are being **persecuted**"; 09:28:05 «suntem chemați la
  **rodire**» (bearing fruit) → "to **be born**", 09:28:17 «aceasta este o rodire» → "this is **a birth**";
  09:23:26 «**închinarea** înseamnă să cauți ajutor» → "**humility** means" (worship; the speaker may
  have said «a cere», but «închinarea» is not humility). Rows: rânduit, rodire = bearing fruit.
- **Recogniser (idea 28):** 09:07:28 «să **ocupăm lucrurile**» (locurile, take our seats) → "take care of
  things" (the stock phrase again); 09:24:03 «**Șehova** Dumnezeu» (Iehova) → "**the Word of** God";
  09:27:05 «literatură **Cristina**» (creștină, Christian literature) → "**Cristina's** literature"
  (a person invented); 09:27:31 «de la **polar, Roma**» (plural) → "Rome"; 09:26:42-48 Luke 17:20-21
  («împărăția lui Dumnezeu nu vine în așa fel ca să izbească privirile») garbled → "will not strike
  it ... they are in joy": a verse by its words that the matcher missed.
- **EN → RO (idea 13 / 32):** 09:48:02 "[the enemy wants to attack] and distract them" → «și **să-i
  distragem** atenția» (we distract them: the subject flipped to us, in a prayer); 09:47:40 "Let us
  enter into this prayer and pray for our youth" → only «Să ne rugăm pentru tineretul nostru» (half
  dropped, idea 32); 09:42:29 "walk in the ways" → «să mergem **pe jos**» (on foot); 09:43:17 "And I
  and I booked" → «Și eu și cu mine» (a stutter translated); 09:42:46 "The blue the blueprint" →
  «**Albastrul**, planul»; 09:43:52 "nodding my head" → «Ținând din cap», "But I didn't" → «Dar n-am
  făcut-o» (literal); 09:43:43 "having Google translate" → «de a avea Google traduce».
  **No diacritics:** 09:43:13 «si am spus», 09:44:00 «si am inteles doar o fractiune»: NLLB sometimes
  writes plain ASCII; postedit should restore «și» and common words (idea 13's fix-up list).
  Stutters ("I I", "the blue the blueprint", "for for me") should be removed before NLLB.

### Rest of Sunday 2026-11-08 (seen live from 09:51, marked in `2026-11-08_095138.wav`)

Placeholders: `<child>`, `<parent>`, `<name>` for people; `<park>`, `<country>` for places; amounts left out.

- **Bible verse shown twice (idea 3), the most frequent visible fault today, 7+ times:** 10:30:46 Eph 4:32
  («…cum v-a iertat așa cum Dumnezeu ne-a iertat»), 10:34:25 Luke 7:47, 10:43:03 Eph 4:30 («pecetluiți…»
  twice), 10:45:19 John 8:11 (with a stray `”)` on screen), 11:42:56 Prov 27:18 ("…shall eat its fruit. **He** And
  there is…"), 11:48:29-43 Jer 6:19 + Prov 14:22 spliced ("And the Lord knows this" twice), 11:52:48 Luke 24:17,
  11:53:52 Luke 24:21, 11:58:37 Phil 4:6 ("Do not be anxious about anything, In nothing be anxious"). In EN→RO
  the Cornilescu verse is appended to NLLB's own translation of the same words; in RO→EN the WEB verse is
  spliced into the speaker's paraphrase mid-sentence. **Change:** when a verse is captioned, drop the
  piece's own translation of the matched span (EN→RO: the whole piece when the verse covers ≥ 70 % of it), and
  never splice into the middle of a sentence. **Priority: first.**
- **Name repair turned a real word into a name (idea 26), shown:** 11:04:07 «Tată, **Fiule** și Duhul Sfânt»
  (recogniser right) → repaired to «Tată, **<name>** și Duhul Sfânt» → "Father, **<name>** and the Holy Spirit". The
  name had been spoken in the welcome an hour earlier (the repair learns names from the service itself).
  «Fiul/Fiule» must never be repaired; better, idea 26's rule: a word that is in the Romanian lexicon is never
  replaced by a name.
- **EN → RO loses the words a sentence turns on at piece boundaries (idea 32, plus no context for NLLB):**
  10:38:25 "I'm not talking about | continuous abuse. You need to seek help" → «…nu vorbim despre asta. Trebuie
  să cauți ajutor» (**the abuse caveat gone**); 10:39:54 "I forgive him before | you ask" → «L-am iertat înainte»;
  10:40:53 "I make | peace" → peace gone; 10:49:01 "retribution on my | brothers" → «răzbunare pe mine»;
  10:55:29 "God was | proud of him" → «eram mândră de el»; 10:36:41 "this great | uh death [debt]" → «A fost
  grozav. Moartea…». Single dropped sentences: 10:33:47 "wiped off.", 10:36:58 "That's enough.", 10:53:42 "you got
  something coming", 10:37:25 "keep track?". **Change:** give NLLB the previous piece's unfinished tail (carry
  the words after the last sentence end into the next piece instead of translating a fragment), and split
  pieces at sentence ends (idea 32). Both directions: 11:54:19 «[nu] ca să-i dezamăgească, ci ca să-i
  trezească» → "so that they might be disappointed" (the «nu» stayed in the previous piece).
- **Meaning reversed or wrong on screen (RO→EN, Gemma):** 11:53:36 «îngrijorarea… **a stins** cea mai frumoasă
  speranță» → "**became** the most beautiful hope" (the sermon's point); 10:50:05 "instead of being open" → «inima
  lor era deschisă»; 11:47:59 «va birui… gândul lui Hristos» → "will conquer the mind of Christ"; 11:37:06 worry
  "plays with us" → "that is how **He** plays with us" (idea 11: capitalise only when the referent is God);
  11:40:30 «**Avea** nu slujire, ci multă slujire» → "Avea did not have a ministry" (a verb read as a name:
  idea 6, a capitalised word that is a common Romanian verb is not a name); 10:59:23 a rhetorical question
  shown as a statement ("the most important thing is for your child to become the president of America").
- **Recogniser (idea 28):** 11:35:40 «[D]omnul spunea» → «omul» → "When **the man** said"; 11:50:00 «cerul
  **slavei**» → "the heaven of **slaves**"; 11:51:16 «din **prisosul** inimii» (Matt 12:34) → "the **prison** of the
  heart"; 11:57:52 «**Preaiubiților**» → «Preaibiților» → "**For the prophets**" (and 11:48:34 «iubiților» merged
  into Jer 6:19 as "lovers"); 11:55:13 «naufragiaseră» → «n-au fragiaseră» → "were **not afraid**"; 11:42:38
  «procra[stinarea]» → «prora» → "the rudder"; 11:14:58 «corul» → «corpul» → "the body of the church"; 10:03:06
  «jertfa buzelor» → «abuzurilor». Stock words for the decoder boost (`engine.CHURCH_PHRASES`): preaiubiților,
  iubiților, slavei, prisosul inimii, corul, să vă ocupați locurile.
- **Numbers and dates (idea 5):** 11:06:26 «13-27 noiembrie» → "November **1327**"; 11:07:51 «noiembrie 21… 21» →
  "November **2021**"; 10:28:11 "between a hundred and hundred and fifty" → «între 100 și 100 și 50»; 11:51:31 «o
  sută și o mie de prieteni» → "100,000 friends"; 10:28:50 "fifty years on the path" → «am 50 de ani». A day-month
  range and a repeated day need their own rule before digits are glued.
- **Garbage shown (idea 17):** 10:19:07 song tail «[binec]uvintez acest copil» → "I am **winning** this child";
  10:26:56 "Post media, painting." (music); 10:29:18, 10:42:31, 10:52:04 a Romanian aside or false start in the
  English sermon («Găinile, Fages, Brad, Aurelio Ghems…», «Gaz, Grace Mercy…», «Următorul, Din World…»);
  10:58:00 "Oh, Singham. Mega Gy, Menor, Okingham," (the child's names, passed because the rest of the piece is
  clean Romanian: gate per clause, not per piece). One was caught (10:49:19 `mt_echo`).
- **Language switch (idea 12):** 10:27:07 the English sermon was heard by the Romanian recogniser for ~45 s
  (hidden at the time); 10:01:41 a Romanian reading was taken as English for a minute (hidden).
- **English words inside Romanian announcements:** "Samaritan's Purse … Operation Christmas Child shoeboxes"
  → "Ser Spars. Stens Spst, Aon Christmas Child, The Shubaks Ghes"; "hallway" → "Hollywood"; "loan" → "Lon";
  "first baby" → "Forst Baby"; "fundraiser" heard three ways. Add them to the saved names / glossary.
- **Glossary rows (idea 9):** fanfara = the brass band (twice "fanfare"); rodire = bearing fruit; rânduit =
  scheduled; viță roditoare = fruitful vine; alegeri = elections (here; came out "choices" twice); drept răspuns =
  in reply; lasă de dorit = leaves much to be desired; a stins = put out. EN→RO: ministry = slujire (not
  «minister»); vessel = vas (not «navă»); testimonies = mărturii; hanging on the cross = atârnat pe cruce; trial
  (hardship) = încercare (not «proces»); fortune = avere (not «noroc»); Job = Iov, Peter = Petru, Joseph = Iosif
  (NLLB left them in English several times).
- **NLLB small faults:** missing diacritics («si», «ierti», «vorbesti», «lipsiti»); «iertă» for «iartă»; um/uh kept;
  stutters translated; idioms literal ("see eye to eye", "on the hook", "take a toll" → «ne omoară pe toți»).
- **Child blessing names:** the family's names came out garbled several ways (entering them under Names &
  words before the service would have fixed most). Also «sora <name>» ran together into one word.
- **Tooling:** `livectl summary` now prints a `pp_health` event every 5 s (0.1.3): filter it out like idea 33.

### Review of Sunday 2026-11-08

- **Log `2026-11-08_090143`** (installed app 0.1.3, Best throughout, 09:01-12:02): 1480 phrases, 1094 captioned (630 RO,
  464 EN), 340 held by Hide. Translation median 0.9 s (p90 1.5), recogniser 0.3 s, first word on screen 6.2 s
  (p90 9.5; RO 6.5, EN 5.8). Input median −21 dB, auto gain −4..+15. A few "behind" alerts (9-11 s) were single
  slow phrases with no queue. Nothing crashed. The operator used Hide heavily (songs, readings, the blessing).
- **What to fix first, by how much it costs on screen:** (1) idea 3, verses doubled or spliced, 7+ times;
  (2) idea 26, never repair a lexicon word into a name ("Father, <name> and the Holy Spirit"); (3) EN→RO
  piece boundaries (carry the unfinished tail; idea 32), the abuse caveat lost; (4) dates and ranges (idea 5);
  (5) per-clause garbage gate (idea 17).
- **Done the same afternoon (not yet benched or seen in a service):** idea 26: a dictionary word or its case
  form («Fiule», «creștină») and the cue words are never repaired into a name, a heard first name only
  respelled (no Ioan → Ioana), a first name after «fratele» / «sora» keeps its sex (surnames in -a still
  repaired); idea 3: the speaker's words next to a captioned verse are dropped when 40 % of them are in the
  verse (no "anxious about anything, In nothing be anxious"); idea 5: days glued next to a month
  («noiembrie 1327» → «noiembrie 13-27», «noiembrie 2021» → «noiembrie 21» when the other recogniser heard 21);
  idea 32: NLLB translates an English piece sentence by sentence (one batch), without um/uh and stutters;
  idea 34: NLLB's subtitle dashes and invented replies stripped, a man's adjectives kept masculine, «si» → «și»;
  idea 9 / 28: the glossary rows and decoder-boost phrases above, the English announcement words as names.
  **Still open:** carrying a piece's unfinished tail into the next (idea 32), the per-clause garbage gate
  (idea 17), "a hundred and fifty" before NLLB, the source-wins prompt line (vită/viță).

## 34. NLLB invents subtitle dialogue on short English pieces: "why?" → «- De ce nu?» — `idea` — P1

- **Evidence (logs `2026-11-06_201604`, `2026-11-06_205911`, `2026-11-08_090143`, EN→RO, Best = NLLB, shown):**
  NLLB answers a short piece the way a film subtitle would, with a leading dash and words nobody said.
  2026-11-06 20:43:29 "why?" → «**- De ce nu?**» (why **not**?: reversed, in the sermon); 20:59:48 "…I saw
  some new faces here. <name>." → «…<name>. **Ce faci?**» (how are you?); 20:59:51 "Nice to meet you and raise
  your hand. All right." → «…**Bine, să mergem.**» (let's go); 20:59:40 "I don't know. Um" → «**- Nu știu. - Nu
  știu.**»; 21:00:06 «**- Poftim.**»; 20:55:05 «**- Tu ești?**». 2026-11-08 10:37:16 "that?" → «**- Asta? - Da.**»
  (an invented "Yes"); 10:34:41 «**- Puțin?**»; 10:35:20 «**- E imposibil.**». The dash and the extra
  sentence are the tell: the source has one short clause, the output has two speakers.
- **Change:** in `NLLBTranslator` post-edit: strip a leading "- " and a "- … - …" two-speaker shape;
  when the output has more sentences than the source, or a sentence with no counterpart (Da, Ce faci,
  să mergem), retranslate the piece with the previous piece's tail (idea 32) or drop the extra sentence;
  don't send a 1-2 word English piece ("why?", "that?") to NLLB alone: hold it for the next piece (the
  EN→RO `hold_min_words`). Test: the pairs above as cases.
- **Effort:** small (post-edit + hold + tests); bench EN→RO on the Friday clips.

## Live notes, Sunday evening 2026-11-08 (`logs/services/2026-11-08_173244.jsonl`, clone run, no recording)

Captions mostly on Hide during readings and songs; notes from the captioned stretches.

- **Reversed meanings (top problem tonight):**
  - 18:44:05 «ne place, nu-i așa?» → "it's hard, isn't it?" (previous line's "too difficult" leaked in).
  - 18:47:54-59 «avortul este considerat Hellker / Helker» (English *healthcare*) → "legal", then "illegal":
    the caption contradicts itself. English word inside Romanian (see «Sandis» = Sunday School, «outside»).
  - 18:54:57-55:01 «prețul. / n-are valoare» split at the boundary → "the price. / have value" (negation lost).
  - 18:55:01 «n-a putut nici măcar să-l jelească» → "couldn't even help her" (mourn).
- **Idioms / glossary rows:** «între gratii» → "among the graces" (behind bars); «ca și părinți / ca și tați»
  → "just as parents" ×3 (= *as* parents: role, not comparison); «mi-am pironit ochii» → "squinted" (fixed my
  eyes); «și gata» → "to prepare" (and that's it); «Nu-i așa» → "It is so" (isn't it?); «am rămas așa» → "I was
  left like that" (it stuck with me); «să nu le mai luăm și pe celelalte» (not to mention the rest); «un vas de
  lucru» → "a vessel of work"; «cauze» → "causes" (prayer requests); «fanfara va intona un imn» heard as «ffara…
  im» → "ffara will sing" (brass band will play); «Iată ce spune Isaia» → "Behold, what Isaiah says" (keep
  "Behold" for the verse only); «omule, nu te duce» → "man does not take you".
- **Person / subject:** «lăsam microfonul, plecam la mama» → "we would leave… the mother's bench" (I, my mom);
  «ca să-l / cunoști pe Hristos» split → "that I may know Christ"; «Vorbisem poporului» → "We spoke"; «Îl iubesc»
  (the enemy) → "I love Him" (capital He again).
- **Context echo:** 18:56:11 output repeated the previous caption "a living sign before You and before" and
  dropped «poporului tău, poate chiar în biserica noastră, în comunitatea noastră».
- **Verse matcher (idea 3):** Ezekiel 24:18 "and in the evening my wife died" shown twice; stutter «picioare nuț»
  got the verse fragment "feet yet."; Isaiah 8:18 missed on the first line (stutter «copiii. mei» → "the children
  we") and caught one line late as "me are for signs…"; «capitolul 24. / cu 3» left "with 3" alone.
- **Recogniser:** «împărăția» heard «împărțirea» → "division of God"; «câteva ore» → «câteva ori» ("a few
  times"); book names «Solonici» (Tesaloniceni), «Sam Ashoti» (Psalmul …); «să aducem cauzele» → «Sam și cauze».
- **Boundaries:** «Dumnezeu! / să ne binecuvinteze» → "God!" + "may He bless us"; «pe ei, bunul Dumnezeu /
  să-i binecuvinteze» → "those, good God,"; «după care sora. / Grace» → "Sister / Sister Grace".
- **Stiff English:** "to be we a sign", «tot mai mult» → "the more we seek", «tot mai puțini» → "still fewer".
- **Timing:** first word 4.8-6.4 s, one 14.5 s on a single long phrase; mt 0.5-1.1 s; speech -19..-26 dB;
  "captions behind ~10 s (queue 0)" alerts only while on Hide.

### Rest of Sunday evening 2026-11-08 (19:00-20:09; recording from 19:13:55, `logs/recordings/2026-11-08_191355.wav`, 31 marks)

- **English sermon, EN→RO (NLLB):** "fall" → «și să cadă» (toamna); "You guys sounded fantastic" → «Voi sună
  fantastic»; "practices" → «practicile» (repetițiile); "the way I used to live" → «modul în care am folosit
  pentru a trăi»; "your old manner" → «vechile maniere»; "by the book" → «de carte»; "it's fully not Jesus" →
  «nu este cu totul Isus»; "Didn't say" → «N-am spus»; "social influencer" → «influența mea socială»; "if we are
  called" → «pentru că»; "we are to be changed" → «Vom fi»; "do not be foolish" → «Nu fiți proști!»; "arise from
  the dead" → «învierea din morți»; "on your own matter" → «din cauza ta»; missing diacritics again. Recogniser:
  "fragrant offering" → "vagrant" → «ofrandă vagabond»; "Praise the Lord" → "Prashisa Lord". Boundary: "God… /
  take control of our lives" → «să ne controlam viețile» (we control).
- **Code-switching speaker (idea 12):** Romanian inside English mode passed through as garbage: «FSN
  capitolopatru, versetus shaptashpe» (Efeseni 4:17), «Sudhid… Sadiqam Tot Simpichware»; back to Romanian took
  ~1 min at 19:17 (hidden). Romanian reference inside English → run the RO recogniser on low-score EN chunks.
- **Cornilescu matcher on English reading:** Eph 4:17 shown twice (NLLB + Cornilescu); 4:22-23 recogniser
  "do not be renewed" → «să nu fiți reînnoiți în trupul vostru» (reversed + invented); Eph 5:2 "pentru noi"
  doubled; Eph 5:14-17 not matched.
- **Pastor's sermon (Best, RO→EN), the key phrase lost every time:** «puțin credincioșilor / puțin credinciosule»
  (Mt 8:26, Mt 6:30) → "the unfaithful", "slightly faithful", "few faithful ones", "less faithful", "little by
  little faithful", "you few who are faithful", "few of the faithful", "do not be a little faithful anymore"
  (11 times, never right). «sintagmă» heard «simptomă / simă / sma / st» → "symptom", "veil", "shadow",
  "substance", "hypocrisy" (~15 times).
- **Reversed meanings (sermon):** «nu de puține ori vedem» → "we don't see many people"; «nu mai recunoaștem»
  split → "we again recognize"; «o viață trăită… ne face să uităm» → "let us forget"; «căci trăim» → "because
  we are dying"; Job 2:10 «nu se cade… să le primim» → "it is not easy"; «ne potrivim chipului veacului» →
  "conform to His likeness"; «uitați ce zice» → "forget what it says" (and «uităm» → "we see / we look" ×3);
  «dacă mai există printre noi» (gestures) → "if He still exists among us"; «Biserica are viitor» → "is the
  future" ×2.
- **Person / reference:** the pastor's self-mocking monologue (I) → "He won't take", "God has given you", "you
  can spend"; «te trezește / îl trezești» → "you wake up"; «Păi știe Domnul» → "I know, the Lord"; «Dumnezeu să
  fie cum vrea el» (the children) → "let God be"; «copiii cărora le spui» → "children whom He"; "Your right
  hand", "You have changed nothing" (capital for a person); «dacă vreți să cădem de acord cu voi» → "agree with
  us"; Abel's testimony → "Cain, who gave a testimony".
- **Glossary rows:** «rânduit» → "scheduled" ×3 (ordained/appointed; morning gave "persecuted"); «imanența» heard
  «Eminența» → "His Eminence"; «robii» → "victims" (slaves); «robiți și învinși» → "shaped and taught";
  «falimentul» → "the thread"; «stare» → "truth"; «să ne revenim» → "see each other again"; «omul lui Dumnezeu»
  → "man, God"; «trimitere» (commission) → "authority"; «tot felul de» → "the same kind of"; «își punea amprenta»
  → "told its imprint"; «oameni, neoameni» → "among the people of the world"; «citim 5 versete» → "verse 5";
  «spunea de fratele X» → "about brother X" (by); «are clopotul lui» → "is His bell"; «până la liman» →
  "until the shore"; English «pe bas» (bus) → "in the game".
- **Verse matcher (idea 3), evening total 65 verse lines:** doubled Ps 121:3 ("to be moved. He who keeps you"
  ×2), Ps 46:10 (paraphrase + verse, "as the psalmist says" ×2), Heb 11:2-6 ("elders obtained approval" ×2,
  "still speaks" ×2, 11:6 in full ×2), Jn 20:27 ("said to Thomas" ×2), Heb 11:1 mixed with Gemma ("un unwavering
  trust…"); Mt 6:30 cut at "you" so the tail «puțin credincioșilor» went to Gemma ("few of the faithful"); Mk
  16:15 with stray "to them,"; Ps 30:7, Job 2:10, Mt 8:26, Prov 24:10 not matched; «24 cu 10» → "24 with 10"
  (reference «X cu Y» = X:Y not converted). Clean: Is 41:10-14, Ps 121:7-8, Ps 46:10-11, Mt 16:18 (WEB
  "assembly" → say "church").
- **Garbage shown (idea 17):** "Appius in the mind", "rom.", "feet yet.", lone «să-l.» → "call out.", «n-amar 4
  nambăr» → "not 4. number".
- **ProPresenter:** unreachable from 19:15:01 (port 1025 closed, host up), back 19:15:37, gone again 19:15:42;
  the app kept captioning and retrying. `pp_health` floods the log every 5 s (tooling note above).

### Review of Sunday evening 2026-11-08

- **Numbers:** 1054 phrases 17:35-20:09; 774 captioned (653 RO / 121 EN), 242 held by Hide; Best only; audio
  4.1/6.7 s, stt 0.2/0.3 s, mt 0.7/1.1 s, first word 5.7/9.5 s (one 14.5 s), latency 1.5/3.2 s; speech -21 dB,
  peak max -1 dB; auto gain -4..+10 dB; "captions behind ~10 s (queue 0)" alerts only while hidden; no crash.
- **Priorities (evening adds weight to the morning list):**
  1. **Reversed meanings at phrase boundaries** (idea 32 + Best boundaries): negations split from their verb
     («nu mai / recunoaștem», «prețul. / n-are valoare») and idioms read literally («nu de puține ori», «nu se
     cade», «uitați ce zice»). Hold a clause that ends on «nu / nu mai / n-» for the next piece; add a prompt rule
     with these idioms; bench the marked clips.
  2. **Repeated sermon phrases learned live:** «puțin credincioșilor» (11×) and «sintagmă» (15×) wrong all
     evening. Fixed glossary rows for the first; for the second, a live "repeated unknown word" detector that
     suggests a term to the operator (or learns it like idea 26's names).
  3. **Verse matcher doubling and tails** (idea 3): one verse = one caption; don't send the verse's last words
     to the translator after a match; map «X cu Y» to X:Y.
  4. **Person and capitals:** first-person monologue → "you/He"; capital He/You for people. Prompt rule:
     keep the speaker's person; capitalise He/You only for God.
  5. **Code-switching:** Romanian inside English mode (references, "let's stand") → run both recognisers on
     low-score chunks, or drop low-score English.
- **Clips:** recording from 19:13:55 only; 17:35-19:13 (Romanian exhortation with most reversed meanings)
  exists only in the log.

## 35. Meaning reversed at Romanian piece boundaries and in negative idioms (Best, Gemma) — `done` (2026-11-09): hold on «nu», idioms, negation per sentence; bench: 5 of its 7 marked evening errors right, was 2 · 2026-11-15: open pieces held (bare transitive verb, «nu este + noun», one-word last sentence); tag-question negation

- **Evidence (2026-11-08 evening, log `2026-11-08_173244`, Best, shown; the 17:35-19:13 part has no recording, 19:13:55 on is in
  `logs/recordings/2026-11-08_191355.wav`):**
  - 18:55:02 / 18:55:11 «…ca semn pentru cei din jurul tău. **prețul.**» | «**n-are valoare.** Soția a murit» → "the
    price." | "**have value**." (the price has no value: negation lost at the cut).
  - 19:50:59 / 19:51:04 «…în ceața aceea că **nu.**» | «**mai recunoaștem**» → "where we do not" | "**we again
    recognize**" (we no longer recognise).
  - 19:55:29 «și **nu de puțini** o vedem oameni când le merge bine» (not rarely = often) → "**we don't see many**
    people"; 19:49:14 Job 2:10 «**nu se cade**… să le primim și pe acestea» (shall we not receive) → "**it is not easy**";
    19:53:12 «căci **trăim**» → "because **we are dying**"; 19:57:26 «și **uitați** ce zice» (look what it says) →
    "and **forget** what it says", 19:51:15 / 19:51:22 «uităm» (we forget) → "we **look at**" (uitați/uităm swapped both
    ways); 18:44:10 «ne place, nu-i așa?» → "**it's hard**, isn't it?" (the previous line's "too difficult" leaked in);
    18:55:11 «n-a putut nici măcar să-l **jelească**» → "couldn't even **help** her" (mourn); 19:53:01 «ne face să uităm» →
    "**let us** forget".
  - Same family in the morning: 11:54:19 «[nu] ca să-i dezamăgească, ci ca să-i trezească» → "so that they might be
    disappointed" (the «nu» in the previous piece).
- **Change:** (a) segmentation: never end a Romanian piece on «nu», «nu mai», «n-», «nici» or on a bare noun followed
  by a negated verb; hold it for the next piece (like the preposition rule in idea 20); (b) prompt rule for Gemma with
  the traps: «nu de puține ori» = often, «nu se cade» = it is not fitting / shall we not, «uitați-vă / uitați ce zice»
  = look, «uităm» = we forget; (c) the negation guard (idea 5) also compares across the previous pair when the source
  starts with «n-» / «nu». Bench on the corrected sermons and the 19:13+ clip.
- **Effort:** small (a, b), medium (c).
- **Done 2026-11-09 (branch `fix/2026-11-08-evening`):** (a) a Romanian piece ending on «nu / nici / n-» or «nu mai» is held for the next piece; (b) idioms nu de puține ori, nu se cade, uitați-vă / uitați ce zice, uităm, trăim, nu mai; (c) the negation guard compares sentence by sentence when source and translation have the same number of sentences (with (a), «nu» no longer sits across two pieces, so the cross-pair check wasn't needed). **Bench:** evening still wrong 23 → 21 (Job 2:10 «nu se cade», «uităm», «uitați ce zice» now right; «ne face să uităm» → "let us forget" still wrong, «trăim» uncertain); luigi-mitoi chrF++ 62.54 → 62.55, BLEU 35.12 → 35.12, WER 4.79; Gemma 0.64/1.04 → 0.64/1.03 s (evening 0.63/1.04 → 0.64/1.06), recogniser 0.23/0.26 → 0.23/0.25 s.
- **Seen again after done (2026-11-15 morning, logs `2026-11-15_081707` / `_094327` / `_102255`, P1): meaning reversed when the negation or the object arrives in the next piece, ~9 times.** 09:56:47 / 09:56:50 «singurul mijlocitor. **Maria.** | **nu Maria**, nu toți sfinții» → "He is the mediator, the only mediator. **Mary.**" (Mary on screen as the mediator, the «nu» in the next piece); 09:57:40 / 09:57:52 «lumea îl urăște pe Hristos pentru că **iubește.** | **păcatul**» → "because it **loves Him**." (the object «păcatul» next); 11:44:45 / 11:44:56 «Nimicul în împărăție **nu este forma.** | **eșecului**» → "Nothing in the kingdom **is** the form."; 09:16:30 / 09:16:33 «așa ne învață. | stăpânitorul lumii acesteia» → "that is what **He** teaches us" (the devil as God); 09:16:39 «**nu face** bine, nu face [ce] e drept» (the tempter: don't do good) → "**it is not good, it is not right**"; 09:06:19 / 09:06:23 «lăudat. | să fie Domnul» → "may the Lord be His"; 09:09:49 / 09:09:53 «să. | fim aici» → "**finish** here"; 09:27:24 «[Eram] poate plecați în veșnicie nepregătiți» (we might have gone into eternity unprepared) → "**He can take you away** forever". And a lost «n-»: 09:37:00 «[Ghedeon] **n-a** învins cu 22.000 de persoane, nu?» (he did *not* win with 22,000) → "**I defeated them** with 22,000 people, didn't I?" (the tag "didn't I" satisfied the guard). **Change:** 35(a) holds only a *trailing* «nu»; also hold a piece that ends on a bare transitive verb (iubește, urăște, învață, spune) or «nu este + noun» when the next piece starts within ~0.7 s, or give Gemma the next piece's first 3-4 words as look-ahead before committing; the guard counts «n-» in the source against a tag question in the English as no negation.
- **Done 2026-11-15 (branch `fix/2026-11-15-morning`):** a Romanian piece that ends on a bare transitive verb (iubește, urăște, învață, spune…), on «nu este + noun» or on a one-word last sentence («… mijlocitor. Maria.») waits for the next piece when it starts within 1.0 s (0.99 s seen), merging up to hold_max_s + 6 s (`live.hold_open_end`); the guard (idea 5) counts «n-» answered only by an English tag question as a lost negation (a sharper note, then NLLB) and flags a negative statement turned into a question («N-ai vrea…» → "Wouldn't you like"). **Bench** (11-15 clips, 61 marked errors, still wrong on main → branch: 40 → 10; luigi-mitoi chrF++ 62.62 → 62.44, BLEU 35.36 → 35.15, WER 4.82 → 4.82 %; Gemma 0.68/1.23 → 0.69/1.21 s, recogniser 0.29/0.49 → 0.29/0.44 s median/p90). This idea's marked errors still wrong 4 → 2: "didn't I?" now "I did not defeat 22,000 people, right?", «Nu este extraordinar» "Isn't it wonderful?", «N-ai vrea» "You would not want"; «iubește. | păcatul» and «nu este forma. | eșecului» are now merged but Gemma keeps the recogniser's full stop at the join ("loves Him. sin."): next step, drop that stop when merging. Cost: 1.5 % of pieces held, ~5 s more for those (median), 0.08 s averaged over all pieces (from the live log).

## 36. A sermon's key phrase is wrong every time it is said: «puțin credincioșilor», «sintagmă» — (a, b) `done` (2026-11-09), (c) `idea`

- **Evidence (2026-11-08 evening, log `2026-11-08_173244`, Best, shown, the pastor's sermon on Matt 8:26):** «puțin credincioșilor / puțin credinciosule» (you of
  little faith) came out 11 times and never right: 19:33:29 "the **unfaithful**", 19:33:36 "**slightly** faithful",
  19:35:12 "**few** faithful ones", 19:37:46 "**less** faithful", 19:50:59 "**little by little** faithful", 19:56:49
  "**few of the faithful**", 19:57:34 "**you few who are faithful**", 20:00:19 "do not be **a little faithful** anymore".
  Matt 6:30 was matched but cut at "you", so the tail went to Gemma. The second key word «sintagmă» (phrase, saying) was
  heard «simptomă / simă / sma / st» ~15 times → "symptom", "veil", "shadow", "hypocrisy", "substance" (idea 28).
- **Change:** (a) glossary row «puțin credincios / puțin credinciosule / puțin credincioșilor» = "of little faith / you
  of little faith"; when a verse match ends mid-phrase, extend it to the verse's last words; (b) «sintagmă» to
  `CHURCH_PHRASES`; (c) later: count unknown or repaired words during a service and, when one repeats ≥ 3 times, show it
  to the operator as a suggested Names & words entry.
- **Effort:** (a, b) tiny; (c) medium.
- **Done (a, b) 2026-11-09 (branch `fix/2026-11-08-evening`):** glossary rows puțin credincios / credinciosule / credincioșilor / credincioși / credinciosului = of little faith; a verse match that stops 1-3 words before the verse's end is completed (idea 3); sintagmă in `CHURCH_PHRASES` (idea 28). (c) open. **Bench:** evening still wrong 20 → 17 (three «puțin credincioșilor» lines now "you of little faith"; 19:50:59 uncertain); luigi-mitoi unchanged (chrF++ 62.56, BLEU 35.11); Gemma 0.64/1.05 → 0.64/1.04 s, recogniser 0.23/0.26 s.

## Live notes, Sunday 2026-11-15 (installed app 0.1.3, Best, log `2026-11-15_081707`)

The service started at 09:04. The first recording (`2026-11-15_074054.wav`) stopped at 08:33 while the band rehearsed, so
09:04-09:22 was read from the log afterwards and has **no audio**; recording again from 09:22:21 (`2026-11-15_092221.wav`).
Placeholders: `<name>` for people.

- **«Christmas» from the Romanian recogniser (idea 28, side effect of keeping English loanwords):** 09:10:19 «ne cheamă pe
  fiecare. **Christmas.** Fiul lui Dumnezeu» → "calls each one of us. **Christmas.**" (meant «Hristos»); the rehearsal
  songs had «doar **Christmas**» / «doar Cristos» too (08:18:42). Change: never keep "Christmas" as a loanword in Romanian
  mode (or map it to «Hristos» unless «Crăciun» context); a Christmas sermon in December would also want this checked.
- **Bible verse doubled / spliced (idea 3), three times in 10 minutes:** 09:12:39 «Păziți ce este drept. și faceți ce este
  bine.» → "**the Word of God speaks.** The Lord says: “Maintain justice and do what is right, for my" (the previous
  caption's "the Word of God speaks" repeated, verse cut on "for my"); 09:14:23 → "is near **and my righteousness** will
  soon be revealed" (previous caption already ended "and my righteousness"); 09:15:53 «care rămâne statornic în el. Și
  Dumnezeu să ne ajute…» → "who holds it fast; **who keeps** And may God help…" (verse tail spliced onto the preacher's
  own words: Isa 56:2 quoted only up to «statornic în el»).
- **God's capital on the devil (idea 11), meaning reversed:** 09:16:30 «să fim egoiști, așa ne învață / stăpânitorul lumii
  acesteia» → "to be selfish, that is what **He** teaches us" — the subject («the ruler of this world») came in the next
  piece, so Gemma assumed God. 09:06:51 «să **îl** însoțim» (the brother praying) → "to accompany **Him**". Change: no
  capital He when the subject isn't named yet; hold a piece ending on «ne învață / ne spune» without a subject (idea 35a).
- **Imperatives / person changed:** 09:12:23 «faceți!» → "**Come!**" (echo of the previous "Come, come…"); 09:16:39
  «nu face bine, nu face e drept» (the whisper: don't do good) → "**it is not good, it is not right**"; 09:16:16
  «dă-ne, Doamne» → "give **me**, Lord"; 09:17:07 «fă așa, așa, așa» dropped; 09:05:27 «N-am făcut nimic» (we) → "**I** have
  done nothing".
- **Boundary cuts (idea 25 / 35):** 09:06:23 «lăudat. / să fie Domnul Lui» → "**may the Lord be His**"; 09:09:53 «să. / fim
  în dimineața aceasta aici» → "**finish** here this morning".
- **Word meanings / recogniser:** 09:10:11 «aude și ascultă» → "**dare** and listen"; 09:11:27 «într-o **amorțeală**»
  (numbness) → "in a state of **death**"; 09:10:51 «C e mare ne-a făcut Hristos» (Ce chemare mare, misheard) → "It is great
  that Christ has made us"; 09:12:50 «fiecare în dreptul lui» (each one personally) → "each in **his own right**" (literal,
  twice); 09:21 «celui **prea înalt**» (the Most High) → "the One who is **too high**" (idea 9 row: Cel Prea Înalt = the
  Most High).
- **Rehearsal (08:17-08:31, paused):** 1-2 word song pieces took 9-10 s each («pe. veci.» 9.2 s → "neighbor», «Hristos,
  speră.» 10.0 s) while longer lines took ~1.7 s, raising "captions behind" alerts every few minutes. Not seen (Hide),
  but the cause is worth finding (a hold that waits for more words and times out?).

### Rest of Sunday 2026-11-15 (09:22-12:10, marked in `2026-11-15_092221.wav`)

Three speakers before the sermon, a guest preacher's 30-minute word (10:39-11:09) and a guest pastor's sermon (11:28-12:07,
Col 1:13-15). Placeholders: `<name>`, `<surname>`, `<state>` for people and places.

- **Meaning reversed (idea 35), the worst kind, ~20 times:** 09:56:39 «El este… singurul mijlocitor. Maria. / nu Maria, nu
  toți sfinții» → "He is the only mediator. **Mary.**" (the «nu» came after the word, in the next piece); 09:57:31 «lumea îl
  urăște pe Hristos pentru că iubește. / păcatul» → "because it **loves Him**." (object in the next piece); 11:44:40
  «Nimicul în împărăție nu este forma. / eșecului» → "Nothing in the kingdom **is** the form."; 12:05:13 «N-ai vrea să faci
  parte din Laodiceea» → "**Wouldn't you like** to be part of the odiceea"; 11:56:21 «ferească Domnul de predicatori care…»
  (God save us from) → "May the Lord **protect** the preacher…"; 11:42:22 «Nu este extraordinar?» → "It is **not**
  extraordinary."; 11:29:23 «ne mai înviorăm» → "we get **discouraged** again"; 11:40:31 «s-au săturat oamenii de vopsele»
  (fed up with) → "have **fed** people with paint"; 10:01:20 «prietenii… vă zic contrariul» → "**I** tell you the opposite";
  09:36:52 «n-a învins cu 22.000» → "I defeated them with 22,000"; 09:35:13 «tot trupul i-a [cuprins] de cancer» → "**God
  stopped** the cancer"; 09:31:55 (song) «și-a întins mâna» → "He did **not** stretch out His hand"; 11:59:28
  «Răstignește-l!» → "**repent**"; 12:06:49 «s-a dus vremea cultelor importanței» → "the time of **worship** has passed".
  New shape: the negation or the object arrives **after** the word in the next piece («X. / nu X», «iubește. / păcatul»,
  «nu este forma. / eșecului»), so 35a's hold on a trailing «nu» doesn't catch it. Change: hold a piece that ends on a bare
  transitive verb or a predicate noun and starts the next with «nu»/an object (or let Gemma see the next piece's first
  3-4 words before committing, the lookahead idea); a rhetorical «Nu este…?» stays a question.
- **«domnul / domnule» (sir, Mr.) → "Lord", capital You/He for people (idea 11), 15+ times in one story:** 11:00:26
  "the Lord", 11:00:41 / 11:04:14 "Lord B", 11:04:56 «Sunați-l pe domnul din America» → "Hear **the Lord** from America",
  11:01:52 «Domnule, te-ai ținut de cuvânt» → "Lord, You are a man of Your word", 10:58:27-11:03:37 a drunk man talking
  to the preacher gets "You" throughout; 09:16:30 the devil gets "He teaches us"; 09:26:23, 11:07:17, 11:38:24, 11:29:15
  "Him/He" for hands, a neighbour, a model, an area of life. Change: «domnule» / «domnul + name» / «domnul din…» is
  "sir / Mr." (rule + idiom row); capital only when the referent is God (11, still open from the 11-08 evening).
- **Bible verses doubled / spliced / not matched (idea 3), 12+:** doubled 09:54:08 (John 7:37), 09:55:43 John 10:9 ("I am
  the door, I am the door"), 10:41:47 Eph 1:3, 11:51:50 Jer 31:3, 11:54:55 John 1:18, 11:59:49 Rev 2:4; spliced 09:32:19
  Isa 56:1 ("…for my"), 09:54:38 John 6:35 ("to them, “I am…"); not matched 09:34:12 Judg 6:23 ("Isaiah… sons of peace"),
  11:36:13 John 12:21, 11:49:30 «Efeseni 5» → "FS 5", 11:51:04 Rom 2:4 → "Romans 4:2, 4", 12:03:27 2 Tim 4:8. «N cu M»
  still literal when the numbers sit in another piece than the book or the recogniser puts a full stop inside: 09:33:23
  «Geneza. / 26 cu 24» → "26 with 24", 10:41:25 «Coloseni 1 cu. 13 și 14» → "1 with. 13 and 14", 11:54:55 «cu 18» →
  "with 18". The doubling seems to come from Gemma already having translated the verse from the source when the matcher
  then also shows it: a matched verse should replace, not follow, Gemma's caption of the same words.
- **Wrong language switch (idea 12), 2 shown:** 09:33:34 Gen 26:24 reading «…» heard as English "She shall kut the
  children." → «**Ea va tăia copiii.**» on screen; 09:37:10 «Ghedeon a coborât…» → "Get one coborila up cu tre de woman."
  → «Ia un coborila în sus cu femeia.». Both a single piece with a Bible name in fast Romanian; both switched back at once.
  Change: a lone switch piece that the Romanian recogniser reads as fluent Romanian (≥ 60 % lexicon) stays Romanian; never
  caption EN→RO from a piece whose English has ≥ 2 non-English words («kut», «coborila», «cu», «tre»).
- **Recurring words heard wrong every time (idea 36 / 28):** «mărit să fie numele (Lui) în veci», a speaker's catchphrase,
  ×6 never right ("great, they are the names forever", "**Mary**, for that is the name forever", "Increase, for they are
  the old names"); «chip» (image, likeness, Col 1:15 — the sermon's key word) ×6: "this **chip** of Christ", "what chip",
  "a face", "that face"; «slavă / slavei» (glory) → "**slave**" ×2 (09:58:59, 10:00:45 "the light of the slave of the Lord
  Jesus"); «Christmas» for «Hristos» ×3 (09:10:19, 11:28:49 "**Christmas is risen!**"); «pocăită / pocăiți» (born-again)
  heard «păcălită» → "his wife was being **cheated on**" (11:02:16); «răpire» (rapture) → "kidnapping"; «cursă» (snare,
  Prov 29:25) → "race"; «întoarce» (bring back) → "turn over" ×2; «rânduit» → "as He has ordained" (09:27:42, still).
  Rows: chip = image/likeness; slavă = glory; răpire = rapture; Christmas → Hristos in RO mode; pocăit boost; mărit să fie.
- **Names (idea 6 / 26):** people's names translated into Bible names: «fratele Ilie» → "brother **Elijah**", «Iosif
  <surname>» → "**Joseph** <surname>" ×2, a child «Moise, Ilie» → "Moses, Elijah", «Iulian» → "Julian" once (kept
  elsewhere); invented names: «pe tata» → "Doru", «a transformat apa» → "Pamphil", «Cheon» for Gideon; a guest family heard
  two ways. A first name after «fratele / sora / pe» is a person: keep it as said.
- **Words cut at piece boundaries (idea 25), 12+:** parca/parcurge → "before we **park**", cuplu/cutremure → "can be
  joined", nepărere/neprihănire → "**helplessness**", autor/autentică → "author", păstrat/păstor → "kept", multe/multiplice →
  "to be many", minut/minunat, poposire/popostire ("inn" twice), Harusus/harul ("the spring"), «Du-/hul» → "the world",
  orcoolică/alcoolică (workaholic) → "alcoholic". Change (25): when a piece ends on a word that isn't in the lexicon or is
  a prefix of the next piece's first word, merge them before translating.
- **English inside Romanian (12 / 28):** the guest pastor mixed English often: "fall in love" → "F. in **the lava**" and
  "if it is not **the will of God**" (invented), "Fail and Destruction" → "Fal", "the Father's attention" → "they seek the
  **truth**", "No matter what" → "9, Whatever happens", "reels" → "real friends", "cheating" → "citing", "shoe boxes" →
  "Sub Axis". Gemma recovered "Divine providence", "Heavenly Connections", "Samaritan's Purse Operation Christmas Child".
- **Echoes shown twice (idea 20):** 2-3 word echoes pass `echo_word` (1 word): "her limbs." ×2, "heart disease" ×2,
  "thieves," ×2, "all", "the church.", "cities." (ora șase). Extend `echo_word` to the last 1-3 words.
- **Recurring scraps shown (idea 17):** "Lorenbl.", "col.", "the sorcerer.", "Oh, lion", "Harusus.", "D.", "9,", "cities.".
- **Timing:** first word median 5-7 s (up to 13.9 s in a song), translation 0.8-1.2 s; "captions behind ~9-10 s" alerts
  came from 1-2 word song pieces during the rehearsal (8-10 s each), not from load.
