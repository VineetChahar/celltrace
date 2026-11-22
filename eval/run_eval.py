"""Runs every labeled incident through the agent and scores it. This is a real
end-to-end eval: each incident is a fresh investigation against the real
LogStore, using the real local Ollama model, no shortcuts.

Run: python -m eval.run_eval [--data-dir data] [--out-dir eval_results/v2_tools/original]
                             [--agent v1|v2] [--model qwen2.5:3b-instruct]
                             [--fault-types A,B,C]

`--agent v1` is the agent exactly as it was before the absence/timing tools
were added (4 evidence tools, original system prompt). `--agent v2` adds
find_missing_response / gap_vs_timer / last_event_before. Everything else
(model, generation options, final-answer schema, step budget, citation
verifier) is identical between the two, so a before/after difference is the
tool set + its prompt text, not a settings change.

Defaults are what `make eval` runs: the v2 agent on the original 86-incident
dataset.
"""
import argparse
import json
import subprocess
import time
from pathlib import Path

from agent.investigator import GEN_OPTIONS, MAX_STEPS, MODEL, investigate
from agent.tools import InvestigationTools, build_log_store
from eval.report import main as print_report
from eval.verify_citations import verify_investigation


def _record(inc: dict, trace: dict, verification: dict, elapsed: float) -> dict:
    final = trace.get("final_answer") or {}
    return {
        "incident_id": inc["incident_id"],
        "true_fault_type": inc["fault_type"],
        "predicted_fault_type": final.get("fault_type"),
        "correct": final.get("fault_type") == inc["fault_type"],
        "n_tool_calls": trace.get("n_tool_calls", 0),
        "any_fabricated_citation": verification["any_fabricated"],
        "n_citations": len(final.get("citations", [])),
        "insufficient_evidence": bool(final.get("insufficient_evidence", False)),
        "elapsed_s": elapsed,
        "timed_out": trace.get("timed_out", False),
    }


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
        results.append(_record(rec["incident"], rec["trace"], rec["verification"], rec.get("elapsed_s", 0.0)))
        done_ids.add(rec["incident"]["incident_id"])
    return results, done_ids


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return "unknown"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    ap.add_argument("--out-dir", type=Path, default=Path("eval_results/v2_tools/original"))
    ap.add_argument("--agent", choices=["v1", "v2"], default="v2")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--fault-types", default="", help="comma-separated subset of fault types (default: all)")
    args = ap.parse_args(argv)

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    store = build_log_store(
        str(args.data_dir / "logs" / "rrc.jsonl"),
        str(args.data_dir / "logs" / "nas.jsonl"),
        str(args.data_dir / "logs" / "phy.jsonl"),
    )
    incidents = [json.loads(l) for l in open(args.data_dir / "incidents.jsonl")]
    if args.fault_types:
        wanted = set(args.fault_types.split(","))
        incidents = [i for i in incidents if i["fault_type"] in wanted]

    config = {
        "agent": args.agent, "model": args.model, "data_dir": str(args.data_dir),
        "fault_types": args.fault_types or "all", "n_incidents": len(incidents),
        "gen_options": GEN_OPTIONS, "max_steps": MAX_STEPS, "git_sha_at_start": _git_sha(),
    }
    (out_dir / "run_config.json").write_text(json.dumps(config, indent=2))
    print(f"config: {json.dumps(config)}")

    transcripts_path = out_dir / "transcripts.jsonl"
    results, done_ids = _load_done(transcripts_path)
    remaining = [inc for inc in incidents if inc["incident_id"] not in done_ids]
    if done_ids:
        print(f"resuming: {len(done_ids)} incidents already scored, {len(remaining)} remaining")

    with open(transcripts_path, "a") as transcript_f:
        for i, inc in enumerate(remaining):
            t0 = time.time()
            tools = InvestigationTools(store, inc["session_id"])
            trace = investigate(inc["session_id"], inc["reported_ts"], inc["symptom"], tools,
                                model=args.model, toolset=args.agent)
            elapsed = time.time() - t0

            citations = (trace.get("final_answer") or {}).get("citations", [])
            verification = verify_investigation(store, inc["session_id"], citations)
            record = _record(inc, trace, verification, elapsed)
            results.append(record)
            transcript_f.write(json.dumps(
                {"incident": inc, "trace": trace, "verification": verification, "elapsed_s": elapsed},
                default=str) + "\n")
            transcript_f.flush()
            status = "OK   " if record["correct"] else "WRONG"
            print(f"[{len(done_ids)+i+1:>3}/{len(incidents)}] {inc['incident_id']} {inc['fault_type']:28s} -> "
                  f"{str(record['predicted_fault_type']):28s} {status} tools={record['n_tool_calls']} "
                  f"fab={record['any_fabricated_citation']} ({elapsed:.1f}s)", flush=True)

    (out_dir / "results.json").write_text(json.dumps(results, indent=2))
    print_report(out_dir)


if __name__ == "__main__":
    main()
