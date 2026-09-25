"""Runs every labeled incident through the agent and scores it. This is a real
end-to-end eval: each incident is a fresh investigation against the real
LogStore, using the real local Ollama model, no shortcuts.

Run: python -m eval.run_eval
"""
import json
import time
from pathlib import Path

from agent.investigator import investigate
from agent.tools import InvestigationTools, build_log_store
from eval.report import main as print_report
from eval.verify_citations import verify_investigation

DATA_DIR = Path("data")
OUT_DIR = Path("eval_results")


def _load_done(transcripts_path: Path) -> tuple[list[dict], set[str]]:
    """Resumability: if a previous run already scored some incidents (saved
    incrementally, one JSON line per incident as it finished), pick up where
    it left off instead of re-running everything from scratch."""
    if not transcripts_path.exists():
        return [], set()
    results, done_ids = [], set()
    for line in transcripts_path.open():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        inc, trace, verification = rec["incident"], rec["trace"], rec["verification"]
        final = trace.get("final_answer") or {}
        results.append({
            "incident_id": inc["incident_id"],
            "true_fault_type": inc["fault_type"],
            "predicted_fault_type": final.get("fault_type"),
            "correct": final.get("fault_type") == inc["fault_type"],
            "n_tool_calls": trace.get("n_tool_calls", 0),
            "any_fabricated_citation": verification["any_fabricated"],
            "n_citations": len(final.get("citations", [])),
            "elapsed_s": rec.get("elapsed_s", 0.0),
            "timed_out": trace.get("timed_out", False),
        })
        done_ids.add(inc["incident_id"])
    return results, done_ids


def main():
    OUT_DIR.mkdir(exist_ok=True)
    store = build_log_store(
        str(DATA_DIR / "logs" / "rrc.jsonl"),
        str(DATA_DIR / "logs" / "nas.jsonl"),
        str(DATA_DIR / "logs" / "phy.jsonl"),
    )
    incidents = [json.loads(l) for l in open(DATA_DIR / "incidents.jsonl")]

    transcripts_path = OUT_DIR / "transcripts.jsonl"
    results, done_ids = _load_done(transcripts_path)
    remaining = [inc for inc in incidents if inc["incident_id"] not in done_ids]
    if done_ids:
        print(f"resuming: {len(done_ids)} incidents already scored, {len(remaining)} remaining")

    with open(transcripts_path, "a") as transcript_f:
        for i, inc in enumerate(remaining):
            t0 = time.time()
            tools = InvestigationTools(store, inc["session_id"])
            trace = investigate(inc["session_id"], inc["reported_ts"], inc["symptom"], tools)
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
                "n_citations": len(citations),
                "elapsed_s": elapsed,
                "timed_out": trace.get("timed_out", False),
            }
            results.append(record)
            transcript_f.write(json.dumps(
                {"incident": inc, "trace": trace, "verification": verification, "elapsed_s": elapsed},
                default=str) + "\n")
            transcript_f.flush()
            status = "OK   " if record["correct"] else "WRONG"
            print(f"[{len(done_ids)+i+1:>3}/{len(incidents)}] {inc['incident_id']} {inc['fault_type']:28s} -> "
                  f"{str(record['predicted_fault_type']):28s} {status} tools={record['n_tool_calls']} "
                  f"fab={record['any_fabricated_citation']} ({elapsed:.1f}s)")

    (OUT_DIR / "results.json").write_text(json.dumps(results, indent=2))
    print_report()


if __name__ == "__main__":
    main()
