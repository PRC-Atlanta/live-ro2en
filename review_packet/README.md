# Review packet

5 clips, 12.3 min. Reference text: **google-chirp_3**; highlighted where it disagrees with **jackrabbit-ctc-lm-b8**.

1. Open `index.html` in a browser (works offline). Listen, fix only the yellow words, click **Save corrections**.
2. Put the downloaded `corrections.json` in this folder.
3. Rescore every model against the corrected text:
   `.venv/bin/python src/review_packet.py rescore review_packet/corrections.json`

No browser? Edit `clips/<clip>.txt` instead: each `[[reference || other]]` holds a disagreement;
replace the whole `[[...]]` with the correct words, then run
`.venv/bin/python src/review_packet.py rescore review_packet/clips`.

## Spot check (blind A/B of the words nobody could edit)

`spots/index.html` lists the 46 places where Google and Jackrabbit agreed (so they were not
highlighted above) but Whisper heard something else. Each has a Play button that starts ~1.5 s
before the spot, and the two versions in random order. Pick A, B or type the right words; leave
unsure ones blank. Save, put `spot_answers.json` in `spots/`, then:
`.venv/bin/python src/spot_check.py rescore review_packet/spots/spot_answers.json`
(writes `corrections_spotchecked.json` and `results/review_scores_spotchecked.csv`).
