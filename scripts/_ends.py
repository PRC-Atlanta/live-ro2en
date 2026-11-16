import json, sys
sys.path.insert(0, "src"); sys.path.insert(0, "src/live")
from live.pipeline import dangling, open_end
for name in sys.argv[1:]:
    recs = [json.loads(l) for l in open(f"logs/bench/stt/{name}.jsonl")]
    recs = [r for r in recs if (r.get("ro") or "").strip()]
    f = [r for r in recs if not r["trace"]["final"]]
    p = [r for r in recs if r["trace"]["final"]]
    d = lambda rs: sum(dangling(r["ro"]) or open_end(r["ro"]) for r in rs)
    empty = sum(1 for l in open(f"logs/bench/stt/{name}.jsonl") if not (json.loads(l).get("ro") or "").strip())
    print(f"{name:20s} clauses with text {len(recs)} (+{empty} empty) | forced {len(f)}, ending open {d(f)} ({d(f) / max(len(f), 1) * 100:.0f}%) | pause {len(p)}, ending open {d(p)} ({d(p) / max(len(p), 1) * 100:.0f}%)")
