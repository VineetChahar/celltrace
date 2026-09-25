"""Part 3 of the diagnose-then-fix pass: does a bigger local model close the
cross-layer synthesis gap? Re-points the SAME agent logic at
qwen2.5:7b-instruct instead of qwen2.5:3b-instruct, for only the fault types
still at 0% accuracy after the Part 2 fix (HO_MISSED_MEASUREMENT,
PAGING_TIMEOUT, REG_TIMEOUT_DROPPED_NAS). No agent-logic changes.

Run: python -m eval.run_part3
"""
import json
import time
from pathlib import Path

from agent.investigator import investigate
from agent.tools import InvestigationTools, build_log_store
from eval.verify_citations import verify_investigation

DATA_DIR = Path("data")
OUT_DIR = Path("eval_results")
MODEL = "qwen2.5:7b-instruct"
TARGET_FAULT_TYPES = ["HO_MISSED_MEASUREMENT", "PAGING_TIMEOUT", "REG_TIMEOUT_DROPPED_NAS"]


def main():
    store = build_log_store(
        str(DATA_DIR / "logs" / "rrc.jsonl"),
        str(DATA_DIR / "logs" / "nas.jsonl"),
        str(DATA_DIR / "logs" / "phy.jsonl"),
    )
    incidents = [json.loads(l) for l in open(DATA_DIR / "incidents.jsonl")]
    subset = [i for i in incidents if i["fault_type"] in TARGET_FAULT_TYPES]
    print(f"running {len(subset)} incidents on {MODEL}: {TARGET_FAULT_TYPES}")

    results = []
    with open(OUT_DIR / "transcripts_part3.jsonl", "w") as transcript_f:
        for i, inc in enumerate(subset):
            t0 = time.time()
            tools = InvestigationTools(store, inc["session_id"])
            trace = investigate(inc["session_id"], inc["reported_ts"], inc["symptom"], tools, model=MODEL)
            elapsed = time.time() - t0

            final = trace.get("final_answer") or {}
            citations = final.get("citations", [])
            verification = verify_investigation(store, inc["session_id"], citations)

            record = {
                "incident_id": inc["incident_id"],
                "true_fault_type": inc["fault_type"],
                "predicted_fault_type": final.get("fault_type"),
                "correct": final.get("fault_type") == inc["fault_type"],
                "n_tool_calls": trace.get("n_tool_calls", 0),
                "any_fabricated_citation": verification["any_fabricated"],
                "insufficient_evidence": final.get("insufficient_evidence", False),
                "elapsed_s": elapsed,
            }
            results.append(record)
            transcript_f.write(json.dumps(
                {"incident": inc, "trace": trace, "verification": verification, "elapsed_s": elapsed},
                default=str) + "\n")
            transcript_f.flush()
            status = "OK   " if record["correct"] else "WRONG"
            print(f"[{i+1:>2}/{len(subset)}] {inc['incident_id']} {inc['fault_type']:26s} -> "
                  f"{str(record['predicted_fault_type']):26s} {status} tools={record['n_tool_calls']} "
                  f"fab={record['any_fabricated_citation']} ({elapsed:.1f}s)")

    (OUT_DIR / "results_part3.json").write_text(json.dumps(results, indent=2))

    by_type = {}
    for r in results:
        d = by_type.setdefault(r["true_fault_type"], {"n": 0, "correct": 0})
        d["n"] += 1
        d["correct"] += int(r["correct"])
    print(f"\n=== Part 3 ({MODEL}) accuracy on the 0%-accuracy subset ===")
    for ft, d in sorted(by_type.items()):
        print(f"{ft:30s} {d['n']:3d}  {d['correct']:3d}  {d['correct']/d['n']:.1%}")
    overall = sum(r["correct"] for r in results) / len(results)
    hallu = sum(r["any_fabricated_citation"] for r in results) / len(results)
    print(f"overall: {overall:.1%}  hallucination: {hallu:.1%}")


if __name__ == "__main__":
    main()
