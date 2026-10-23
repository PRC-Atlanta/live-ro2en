# Review packet

5 clips, 12.3 min. Reference text: **google-chirp_3**; highlighted where it disagrees with **jackrabbit-ctc-lm-b8**.

1. Open `index.html` in a browser (works offline). Listen, fix only the yellow words, click **Save corrections**.
2. Put the downloaded `corrections.json` in this folder.
3. Rescore every model against the corrected text:
   `.venv/bin/python src/review_packet.py rescore review_packet/corrections.json`

No browser? Edit `clips/<clip>.txt` instead: each `[[reference || other]]` holds a disagreement;
replace the whole `[[...]]` with the correct words, then run
`.venv/bin/python src/review_packet.py rescore review_packet/clips`.
