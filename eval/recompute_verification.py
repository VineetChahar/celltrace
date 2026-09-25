"""Re-verifies every saved transcript's citations against the real LogStore
using the current verify_citations logic, without re-running the (expensive)
LLM investigations. Rewrites transcripts.jsonl, results.json, and the report.
Run this after any change to eval/verify_citations.py.
"""
import json
from pathlib import Path

from agent.tools import build_log_store
from eval.report import main as print_report
from eval.verify_citations import verify_investigation

DATA_DIR = Path("data")
OUT_DIR = Path("eval_results")


def main():
    store = build_log_store(
        str(DATA_DIR / "logs" / "rrc.jsonl"),
        str(DATA_DIR / "logs" / "nas.jsonl"),
        str(DATA_DIR / "logs" / "phy.jsonl"),
    )
    records = [json.loads(l) for l in (OUT_DIR / "transcripts.jsonl").open()]

    results = []
    with open(OUT_DIR / "transcripts.jsonl", "w") as f:
        for rec in records:
            inc, trace = rec["incident"], rec["trace"]
            final = trace.get("final_answer") or {}
            citations = final.get("citations", [])
            verification = verify_investigation(store, inc["session_id"], citations)
            rec["verification"] = verification
            f.write(json.dumps(rec, default=str) + "\n")

            results.append({
                "incident_id": inc["incident_id"],
                "true_fault_type": inc["fault_type"],
                "predicted_fault_type": final.get("fault_type"),
                "correct": final.get("fault_type") == inc["fault_type"],
                "n_tool_calls": trace.get("n_tool_calls", 0),
                "any_fabricated_citation": verification["any_fabricated"],
                "n_citations": len(citations),
                "elapsed_s": rec.get("elapsed_s", 0.0),
                "timed_out": trace.get("timed_out", False),
            })

    (OUT_DIR / "results.json").write_text(json.dumps(results, indent=2))
    print_report()


if __name__ == "__main__":
    main()
