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
- **More evidence (2026-10-24 11:42, one prayer):** Puha → Puca, PA, Pva, PUV.
- **Today:** Names & words (terms) feed the glossary, spellings and the *English* recognizer
  (Whistle keywords). The Romanian recognizer (Jackrabbit CTC + 4-gram LM, beam 8) gets nothing.
- **Change:** boost today's names in the CTC beam search (word boosting / hotwords in the LM
  decoder), and add fuzzy spelling fixes for near-misses (Pha → Puha) before translation.
- **Effort:** medium (decoder support needs checking in NeMo's beam decoder).

## 3. Bible references: show the published verse — `idea`

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

## 4. Give Gemma its own previous translations as context — `idea`

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

## 5. Numbers, negations and facts: a guard — `next`

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
- **RO→EN, translator side (11:58):** "cei paisprezece copii" → "their **fifteen** children"
  (fourteen; the next chunk "celor 14 nepoți" → "14" was right). Number *words* are the risk
  in both directions; a guard comparing number words/digits would have caught this one.
- **Negation invented (12:22):** "Și a pus hotarul acesta | ca locuință, între cele două ne
  mișcăm" (between the two we move) → "that we do **not** move between the two as a dwelling
  place". A "not" with no "nu" in the source reverses the meaning; the same guard can count
  negations (nu/n-/nici ↔ not/no/never).
- **(13:29):** "dar privită de sus, nu-i așa?" (but seen from above, isn't it [different]?) →
  "but seen from above, **it isn't**, is it?": the tag question "nu-i așa" read as a negation.
- **Change:** compare digits / number words in source and translation; on a mismatch,
  re-translate once or log it for review.
- **Effort:** small.

## 6. Words that are also names — `idea`

- **Evidence:** "Dragoș" (name, also "dear") → "Sweetie"; "Casa Dânșiilor" (their house,
  capitalised by the recogniser) → "the House of Dancers"; "Hayomin".
- **More (2026-10-24):** "Zaharia" → "Zechariah" and "Bala" → "Paul" (people's names turned into
  Bible names); in EN→RO, "Jonathan" → "Ionatan", "Joseph" → "Iosif" for the family's children.
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

## 9. Church terms the translator gets wrong: add them to the glossary — `next`

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

## 10. Empty-phrase fallback misses short Romanian words — `idea`

- **Evidence (2026-10-24 11:39):** the Romanian recogniser returned nothing, the multilingual
  one heard "binecuvinteze." and it was still dropped: the fallback needs a Romanian-looking
  score > 0, and one word without diacritics or common words scores 0.
- **Change:** in RO→EN (locked, or auto with Romanian current) accept the multilingual text
  unless it scores as English (< 0).
- **Effort:** tiny.

## 11. Prayer language: idioms and the pronoun for God — `idea`

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

## 12. Language detection: check the Romanian recogniser before switching — `next`

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

## 13. English → Romanian: grammar, wrong-language words, idioms — `next`

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

## 14. Romanian names from the English recogniser: map back to Romanian spelling — `idea`

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

## 15. Lists of names are dropped as "untranslated" — `idea`

- **Evidence (11:47-11:48, the obituary's lists of siblings and children):** "Despra, Carmen,
  Dan, David, Silvia, Dir." and "Isaac, Emily, Michelle, Michael," were not shown
  (`mt_echo`): the output equals the input, which is right for names, so the echo filter
  threw them away. Those lists are what the family wants read out.
- **Again in Romanian (11:53):** "Despina, Carmen, Dan, David, Cecilia." and "Teodor,
  Daniela, Daniel, Claudiu." dropped the same way.
- **Change:** in `is_echo` handling, pass a phrase through when it is mostly capitalised
  words / names (or in the terms list) instead of dropping it.
- **Effort:** tiny.

## 16. Words repeated across chunk boundaries (English) — `idea`

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

## 17. Don't caption garbled recognition: a confidence gate — `next`

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

## 18. Songs: don't caption garbled lyrics — `idea`

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

## 19. Automatic gain: level each speaker to the ideal volume — `next` (asked for by the operator)

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

## 20. Cut phrases by words, not seconds, for slow speakers — `idea`

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

## 21. Smoother caption flow: reveal words at speaking pace — `idea` (asked for by the operator)

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

## 22. Gemma's notes to the reader: cut them, keep the translation — `next`

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
