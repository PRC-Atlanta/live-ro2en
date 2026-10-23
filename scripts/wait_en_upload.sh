#!/bin/bash
# Wait for an English recording (Audio/EN*) to finish uploading, cut a 10-min excerpt from 20 % in,
# then run the offline EN→RO A/B on it.
cd "$(dirname "$0")/.."
while true; do
  EN=(Audio/EN*); F=""; [ -e "${EN[0]}" ] && F=${EN[0]}
  if [ -n "$F" ]; then
    S1=$(stat -f %z "$F"); sleep 30; S2=$(stat -f %z "$F")
    [ "$S1" = "$S2" ] && [ "$S1" -gt 1000000 ] && break
  else
    sleep 30
  fi
done
echo "found $F ($(stat -f %z "$F") bytes)"
D=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$F")
START=$(python3 -c "print(max(0, float('$D') * 0.2))")
mkdir -p data/samples
OUT=data/samples/$(basename "${F%.*}")_10min.wav
ffmpeg -nostdin -loglevel error -y -ss "$START" -t 600 -i "$F" -ac 1 -ar 16000 "$OUT"
echo "excerpt $OUT from ${START}s of ${D}s"
scripts/en_ab.sh "$OUT"
echo WAIT-EN-DONE
