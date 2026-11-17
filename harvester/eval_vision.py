"""Measure image reading (build ticket 08) on development images: ticket stamps and request text, scanned and
screenshot domains, standing orders. Evaluation only (reads harvester/data; the 5 most recent runs are held out).

    uv run harvester/eval_vision.py --runs 6          # first N development runs
    uv run harvester/eval_vision.py --runs 6 --offline

Ground truth:
- tickets: the task's `request` column is the open ticket's text. The open image is the one whose transcript contains
  most of its words; the others are closed. Scored: stamp status per image, whether reading in path order until the first OPEN
  stamp finds the right ticket, and the similarity of the transcribed request to the real one.
- scans / screenshots: every expected answer domain must appear verbatim in the transcripts of the images the request
  names (a recall of spellings; the answer itself needs the audit log as well). Gap checks are left out: their answer
  is the domains the pages do not show.
- standing orders: transcripts are written to runs/vision_eval_standing_orders.md for a manual check.
Transcripts are cached in runs/vision_eval_cache.json by blob hash (the harness itself caches per run only).
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "harvester"))

from arena import llm  # noqa: E402
from eval_intake import load_tasks  # noqa: E402
from harness import vision as V  # noqa: E402
from harness.machine import Deadlines  # noqa: E402
from harness.pacing import Pacer  # noqa: E402

DATA = ROOT / "harvester" / "data"
CACHE = ROOT / "runs" / "vision_eval_cache.json"
SO_OUT = ROOT / "runs" / "vision_eval_standing_orders.md"


def blob(sha: str) -> bytes:
    return (DATA / "blobs" / sha).read_bytes()


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().casefold()


def sim(body: str, request: str) -> float:
    """Word recall: the share of the request's words that the transcript contains (ticket headers don't dilute it)."""
    have = collections.Counter(re.findall(r"[\w@.:-]+", norm(body)))
    want = collections.Counter(re.findall(r"[\w@.:-]+", norm(request)))
    return sum(min(n, have[w]) for w, n in want.items()) / max(1, sum(want.values()))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--runs", type=int, default=6)
    ap.add_argument("--offline", action="store_true")
    a = ap.parse_args()
    rows = load_tasks(a.runs)
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}

    jobs: dict[str, str] = {}  # sha -> path
    ticket_tasks, scan_tasks, orders = [], [], {}
    for r in rows:
        files = r["paths"]
        photos = sorted(p for p in files if p.startswith("/tickets/") and V.is_image(p))
        texts = [p for p in files if p.startswith("/tickets/") and not V.is_image(p)]
        if "/tickets/" in r["instruction"] and photos:
            open_text = any(re.search(r"status:\s*open", blob(files[p]).decode("utf-8", "replace"), re.I)
                            for p in texts)
            if not open_text:
                ticket_tasks.append((r, photos))
                for p in photos:
                    jobs.setdefault(files[p], p)
        named = [p for p in dict.fromkeys(V.IMAGE_PATH.findall(r["request"])) if p in files]
        gap = re.search(r"\bgap\b|missing|not (?:listed|on)", r["request"], re.I)  # answer = domains NOT shown
        if named and not gap and r["expected_outcome"] == "OK" and isinstance(r["expected_answer"], list):
            scan_tasks.append((r, named))
            for p in named:
                jobs.setdefault(files[p], p)
        if "/standing_order.jpg" in files:
            orders.setdefault(r["run_id"], files["/standing_order.jpg"])
    for sha in orders.values():
        jobs.setdefault(sha, "/standing_order.jpg")

    todo = {h: p for h, p in jobs.items() if h not in cache}
    print(f"{len(rows)} tasks: {len(ticket_tasks)} photo-ticket tasks, {len(scan_tasks)} image-list tasks, "
          f"{len(orders)} standing orders; {len(jobs)} distinct images, {len(todo)} to read")
    tracker = llm.CostTracker()
    if todo and not a.offline:
        pacer = Pacer(tracker, Deadlines(hard_s=7200, soft_s=7200))
        t0 = time.time()

        def read(item):
            h, p = item
            one = llm.CostTracker()
            msg = pacer.call("gemma", lambda t: llm.chat(
                [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": V.data_url(p, blob(h))}},
                                              {"type": "text", "text": V.PROMPT}]}],
                max_tokens=V.MAX_TOKENS, temperature=0.0, tracker=one, retries=0, timeout=t))
            tracker.add("gemma", {"cost": one.total})
            return h, {"text": (msg.get("content") or "").strip(), "cost": one.total,
                       "prompt_tokens": (msg.get("_usage") or {}).get("prompt_tokens")}
        with ThreadPoolExecutor(6) as ex:
            for h, v in ex.map(read, todo.items()):
                cache[h] = v
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache))
        print(f"read {len(todo)} images: ${tracker.total:.4f}, {time.time() - t0:.0f}s")
    T = {h: v["text"] for h, v in cache.items()}
    cost = [v["cost"] for v in cache.values() if "cost" in v]

    # --- tickets ---------------------------------------------------------------------------------------------------
    st = collections.Counter()
    found = calls = 0
    fidelity = []
    misses = []
    for r, photos in ticket_tasks:
        files = r["paths"]
        tx = {p: T.get(files[p], "") for p in photos}
        truth = max(photos, key=lambda p: sim(V.body(tx[p]), r["request"]))
        for p in photos:
            want = "open" if p == truth else "closed"
            got = V.stamp_status(tx[p])
            st[(want, got)] += 1
        pick = None
        for p in photos:  # what intake does: path order until the first OPEN stamp
            calls += 1
            if V.stamp_status(tx[p]) == "open":
                pick = p
                break
        found += pick == truth
        if pick == truth:
            fidelity.append(sim(V.body(tx[pick]), r["request"]))
        elif len(misses) < 5:
            misses.append((r["run_id"], r["task_id"], truth, pick, {p: V.marks(tx[p])[:80] for p in photos}))

    # --- scans / screenshots ----------------------------------------------------------------------------------------
    rec_hit = rec_n = tasks_full = 0
    scan_miss = []
    for r, named in scan_tasks:
        text = "\n".join(T.get(r["paths"][p], "") for p in named).casefold()
        exp = [str(x).casefold() for x in r["expected_answer"]]
        hits = [d for d in exp if d in text]
        rec_hit += len(hits)
        rec_n += len(exp)
        tasks_full += len(hits) == len(exp)
        if len(hits) < len(exp) and len(scan_miss) < 8:
            scan_miss.append((r["task_id"], sorted(set(exp) - set(hits))))

    SO_OUT.write_text("\n\n".join(f"## {run} ({sha[:12]})\n\n{T.get(sha, '')}" for run, sha in orders.items()))

    n_t = len(ticket_tasks)
    print("\n## Tickets (photo)")
    print(f"- open ticket found: {found}/{n_t} = {found / max(1, n_t):.3f}; vision calls per task {calls / max(1, n_t):.2f}")
    print(f"- stamp status per image (want, got): {dict(st)}")
    if fidelity:
        print(f"- request text similarity (found tasks): mean {sum(fidelity) / len(fidelity):.3f}, "
              f"≥ 0.95: {sum(f >= 0.95 for f in fidelity)}/{len(fidelity)}")
    for m in misses:
        print("  miss:", m)
    print("\n## Image lists (scans, screenshots)")
    print(f"- expected domains found verbatim: {rec_hit}/{rec_n} = {rec_hit / max(1, rec_n):.3f}; "
          f"tasks with all found: {tasks_full}/{len(scan_tasks)}")
    for m in scan_miss:
        print("  miss:", m)
    if cost:
        pt = [v.get("prompt_tokens") for v in cache.values() if v.get("prompt_tokens")]
        print(f"\n## Cost: ${sum(cost) / len(cost):.6f} per image, prompt tokens median {sorted(pt)[len(pt) // 2]}")
    print(f"standing orders: {SO_OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
