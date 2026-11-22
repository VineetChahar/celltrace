"""Before/after tables for the absence/timing-tools change.

Reads the run directories written by eval.run_eval and prints (and saves to
eval_results/comparison.md + comparison.json) every number as k/n:

  v1_rerun/original   old agent, original 86      v2_tools/original   new agent, original 86
  v1_rerun/heldout    old agent, held-out set     v2_tools/heldout    new agent, held-out set
  v2_tools/7b_three_types   new agent on qwen2.5:7b-instruct, the 27 incidents of the
                            three previously-0% fault types (original set)

plus the historical committed run (eval_results/results.json, the published
25.6%) and the historical 7B run (results_part3.json) for reference.

Old vs new are run on the same incidents, so the accuracy difference is tested
with an exact McNemar test on the discordant pairs (incidents one agent got
right and the other got wrong).

Run: python -m eval.compare
"""
import json
import re
from collections import Counter, defaultdict
from math import comb
from pathlib import Path

from synthesizer.faults import FAULT_TYPES

R = Path("eval_results")
TARGET = ["HO_MISSED_MEASUREMENT", "PAGING_TIMEOUT", "REG_TIMEOUT_DROPPED_NAS"]
NEW_TOOLS = ["find_missing_response", "gap_vs_timer", "last_event_before"]


def load_run(d: Path) -> dict | None:
    t = d / "transcripts.jsonl"
    if not t.exists():
        return None
    recs = {}
    for line in t.open():
        if line.strip():
            rec = json.loads(line)
            recs[rec["incident"]["incident_id"]] = rec
    return recs


_FT_PREFIX = re.compile(r'"fault_type"\s*:\s*"([A-Z_]+)"')


def truncated_fault_type(final: dict) -> str | None:
    """For a final answer whose JSON was cut off by the num_predict cap, the
    fault_type the model had already written (it's the first key). Diagnostic
    only -- the scored answer for these is still the OTHER fallback."""
    expl = (final or {}).get("explanation") or ""
    if "structured output failed to parse" not in expl:
        return None
    m = _FT_PREFIX.search(expl.replace('\\"', '"'))
    return m.group(1) if m else None


def kn(k: int, n: int) -> str:
    return f"{k}/{n} ({k / n:.1%})" if n else "0/0"


def stats(recs: dict, only_types=None) -> dict:
    rows = [r for r in recs.values() if not only_types or r["incident"]["fault_type"] in only_types]
    n = len(rows)
    correct = sum((r["trace"].get("final_answer") or {}).get("fault_type") == r["incident"]["fault_type"] for r in rows)
    fab = sum(r["verification"]["any_fabricated"] for r in rows)
    insuff = sum(bool((r["trace"].get("final_answer") or {}).get("insufficient_evidence")) for r in rows)
    parse_fail = sum("structured output failed to parse" in ((r["trace"].get("final_answer") or {}).get("explanation") or "")
                     for r in rows)
    trunc_correct = sum(truncated_fault_type(r["trace"].get("final_answer")) == r["incident"]["fault_type"] for r in rows)
    calls = [r["trace"].get("n_tool_calls", 0) for r in rows]
    tool_counter = Counter()
    used_new = 0
    for r in rows:
        names = [c["name"] for s in r["trace"]["steps"] for c in s["tool_calls"]]
        tool_counter.update(names)
        used_new += any(nm in NEW_TOOLS for nm in names)
    per_type = defaultdict(lambda: [0, 0])
    for r in rows:
        ft = r["incident"]["fault_type"]
        per_type[ft][1] += 1
        per_type[ft][0] += (r["trace"].get("final_answer") or {}).get("fault_type") == ft
    return {
        "n": n, "correct": correct, "fabricated": fab, "insufficient_evidence": insuff,
        "final_answer_parse_failures": parse_fail,
        "truncated_but_fault_type_was_right": trunc_correct,
        "avg_tool_calls": sum(calls) / n if n else 0.0, "total_tool_calls": sum(calls),
        "investigations_using_a_new_tool": used_new, "tool_call_counts": dict(tool_counter),
        "per_type": {ft: tuple(v) for ft, v in sorted(per_type.items())},
    }


def mcnemar(old: dict, new: dict) -> dict:
    ids = sorted(set(old) & set(new))
    ok = lambda r: (r["trace"].get("final_answer") or {}).get("fault_type") == r["incident"]["fault_type"]
    b = sum(ok(old[i]) and not ok(new[i]) for i in ids)  # old right, new wrong
    c = sum(not ok(old[i]) and ok(new[i]) for i in ids)  # old wrong, new right
    m = b + c
    p = min(1.0, 2 * sum(comb(m, k) for k in range(0, min(b, c) + 1)) / 2 ** m) if m else 1.0
    return {"n_paired": len(ids), "old_right_new_wrong": b, "old_wrong_new_right": c, "exact_two_sided_p": p}


def table(title: str, cols: list[tuple[str, dict]]) -> list[str]:
    out = [f"### {title}", "", "| metric | " + " | ".join(c for c, _ in cols) + " |",
           "|---|" + "---:|" * len(cols)]
    def row(label, f):
        out.append(f"| {label} | " + " | ".join(f(s) for _, s in cols) + " |")
    row("root-cause accuracy", lambda s: kn(s["correct"], s["n"]))
    row("citation hallucination (>=1 bad/missing citation)", lambda s: kn(s["fabricated"], s["n"]))
    row("insufficient_evidence=true", lambda s: kn(s["insufficient_evidence"], s["n"]))
    row("final JSON cut off / unparseable", lambda s: kn(s["final_answer_parse_failures"], s["n"]))
    row("...of which the cut-off JSON already named the right fault (diagnostic, not scored)",
        lambda s: f"{s['truncated_but_fault_type_was_right']}/{s['final_answer_parse_failures']}")
    row("avg tool calls / investigation", lambda s: f"{s['avg_tool_calls']:.2f}")
    row("investigations that called a new tool", lambda s: kn(s["investigations_using_a_new_tool"], s["n"]))
    out.append("")
    types = sorted({ft for _, s in cols for ft in s["per_type"]}, key=FAULT_TYPES.index)
    out += ["| fault type | " + " | ".join(c for c, _ in cols) + " |", "|---|" + "---:|" * len(cols)]
    for ft in types:
        cells = []
        for _, s in cols:
            k, n = s["per_type"].get(ft, (0, 0))
            cells.append(kn(k, n))
        out.append(f"| {ft} | " + " | ".join(cells) + " |")
    out.append("")
    return out


def historical_committed() -> dict | None:
    """The published run (eval_results/results.json + transcripts.jsonl)."""
    recs = load_run(R)
    return recs


def main():
    runs = {
        "v1_original": load_run(R / "v1_rerun" / "original"),
        "v2_original": load_run(R / "v2_tools" / "original"),
        "v1_heldout": load_run(R / "v1_rerun" / "heldout"),
        "v2_heldout": load_run(R / "v2_tools" / "heldout"),
        "v2_7b_three": load_run(R / "v2_tools" / "7b_three_types"),
        "hist_committed": historical_committed(),
        "hist_7b_part3": None,
    }
    p3 = R / "transcripts_part3.jsonl"
    if p3.exists():
        runs["hist_7b_part3"] = {json.loads(l)["incident"]["incident_id"]: json.loads(l) for l in p3.open() if l.strip()}

    md = ["# Absence/timing tools: before vs after", "",
          "Generated by `python -m eval.compare` from the transcripts in `eval_results/`. "
          "Model `qwen2.5:3b-instruct` unless stated, identical settings for old and new "
          "(`num_predict=400`, 10-step budget, same final-answer schema, same citation verifier).", ""]
    summary = {}

    for ds, label in (("original", "original 86 incidents (seed 42)"), ("heldout", "held-out set (seed 20261122)")):
        old, new = runs[f"v1_{ds}"], runs[f"v2_{ds}"]
        if not old or not new:
            md.append(f"_{ds}: run(s) missing, skipped_\n")
            continue
        so, sn = stats(old), stats(new)
        cols = [("old agent (v1)", so), ("new agent (v2)", sn)]
        if ds == "original" and runs["hist_committed"]:
            sh = stats(runs["hist_committed"])
            cols = [("published run (v1, Sep)", sh)] + cols
            summary["published_original"] = sh
        md += table(f"{label}: all fault types", cols)
        mc = mcnemar(old, new)
        md.append(f"Paired on {mc['n_paired']} incidents: old right / new wrong = {mc['old_right_new_wrong']}, "
                  f"old wrong / new right = {mc['old_wrong_new_right']}, exact McNemar p = {mc['exact_two_sided_p']:.3g}.")
        md.append("")
        to, tn = stats(old, TARGET), stats(new, TARGET)
        md += table(f"{label}: only the three previously-0% fault types", [("old agent (v1)", to), ("new agent (v2)", tn)])
        mct = mcnemar({k: v for k, v in old.items() if v["incident"]["fault_type"] in TARGET},
                      {k: v for k, v in new.items() if v["incident"]["fault_type"] in TARGET})
        md.append(f"Paired on {mct['n_paired']}: old right / new wrong = {mct['old_right_new_wrong']}, "
                  f"old wrong / new right = {mct['old_wrong_new_right']}, exact McNemar p = {mct['exact_two_sided_p']:.3g}.")
        md.append("")
        md.append(f"New-tool call counts ({ds}, v2): " +
                  ", ".join(f"{t}={sn['tool_call_counts'].get(t, 0)}" for t in NEW_TOOLS) + "")
        md.append("")
        summary[ds] = {"v1": so, "v2": sn, "mcnemar_all": mc, "v1_three": to, "v2_three": tn, "mcnemar_three": mct}

    if runs["v2_7b_three"]:
        s7 = stats(runs["v2_7b_three"])
        cols = []
        if runs["hist_7b_part3"]:
            cols.append(("7B, old tools (Sep, part 3)", stats(runs["hist_7b_part3"])))
        if runs["v2_original"]:
            cols.append(("3B, new tools", stats(runs["v2_original"], TARGET)))
        cols.append(("7B, new tools", s7))
        md += table("qwen2.5:7b-instruct with the new tools, the 27 previously-0% incidents (original set)", cols)
        summary["7b_three_types"] = s7

    text = "\n".join(md)
    (R / "comparison.md").write_text(text + "\n")
    (R / "comparison.json").write_text(json.dumps(summary, indent=2, default=list))
    print(text)


if __name__ == "__main__":
    main()
