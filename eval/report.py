"""Turns eval_results/results.json into the honest-numbers tables for the
README, plus a tool-call-efficiency chart. Run standalone after run_eval.py.
"""
import json
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

OUT_DIR = Path("eval_results")

# Palette: a fixed categorical order for the two outcome states (never cycled),
# using color for the job it does (status: correct vs incorrect), not decoration.
COLOR_CORRECT = "#2E7D32"    # good
COLOR_INCORRECT = "#C62828"  # critical
COLOR_NEUTRAL = "#37474F"


def accuracy_by_fault_type(results: list[dict]) -> dict:
    by_type = defaultdict(lambda: {"n": 0, "correct": 0})
    for r in results:
        d = by_type[r["true_fault_type"]]
        d["n"] += 1
        d["correct"] += int(r["correct"])
    return {
        ft: {"n": d["n"], "correct": d["correct"], "accuracy": d["correct"] / d["n"]}
        for ft, d in sorted(by_type.items())
    }


def overall_accuracy(results: list[dict]) -> float:
    return sum(r["correct"] for r in results) / len(results)


def hallucination_rate(results: list[dict]) -> float:
    return sum(r["any_fabricated_citation"] for r in results) / len(results)


def tool_use_efficiency(results: list[dict]) -> dict:
    n_calls = [r["n_tool_calls"] for r in results]
    avg_calls = sum(n_calls) / len(n_calls)
    median_calls = sorted(n_calls)[len(n_calls) // 2]
    # Split at the median rather than a fixed threshold: every investigation
    # here uses >=2 tool calls (the system prompt requires at least one before
    # submitting), so a fixed "<=1 call" bucket would always be empty.
    low = [r for r in results if r["n_tool_calls"] <= median_calls]
    high = [r for r in results if r["n_tool_calls"] > median_calls]
    return {
        "avg_tool_calls": avg_calls,
        "median_tool_calls": median_calls,
        "n_investigations_at_or_below_median": len(low),
        "n_investigations_above_median": len(high),
        "accuracy_at_or_below_median_calls": overall_accuracy(low) if low else None,
        "accuracy_above_median_calls": overall_accuracy(high) if high else None,
    }


def make_chart(results: list[dict], out_path: Path):
    by_calls = defaultdict(lambda: {"n": 0, "correct": 0})
    for r in results:
        d = by_calls[r["n_tool_calls"]]
        d["n"] += 1
        d["correct"] += int(r["correct"])
    xs = sorted(by_calls.keys())
    accs = [by_calls[x]["correct"] / by_calls[x]["n"] for x in xs]
    ns = [by_calls[x]["n"] for x in xs]

    plt.rcParams.update({"font.size": 11, "axes.edgecolor": "#B0BEC5", "axes.labelcolor": "#37474F"})
    fig, ax = plt.subplots(figsize=(7, 4.5))
    bars = ax.bar(xs, accs, color=COLOR_NEUTRAL, width=0.6)
    for x, acc, n, bar in zip(xs, accs, ns, bars):
        ax.text(x, acc + 0.02, f"n={n}", ha="center", va="bottom", fontsize=9, color="#37474F")
        bar.set_color(COLOR_CORRECT if acc >= 0.5 else COLOR_INCORRECT)
    ax.set_xlabel("tool calls in investigation")
    ax.set_ylabel("root-cause accuracy")
    ax.set_ylim(0, 1.15)
    ax.set_xticks(xs)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_title("Accuracy vs. investigation length", loc="left", fontsize=12, color="#263238")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main():
    results = json.loads((OUT_DIR / "results.json").read_text())

    print(f"\n=== Root-cause accuracy: {overall_accuracy(results):.1%} overall ({len(results)} incidents) ===")
    print(f"{'fault_type':32s} {'n':>4s} {'correct':>8s} {'accuracy':>10s}")
    for ft, d in accuracy_by_fault_type(results).items():
        print(f"{ft:32s} {d['n']:4d} {d['correct']:8d} {d['accuracy']:9.1%}")

    print(f"\n=== Citation grounding ===")
    print(f"hallucination rate (>=1 fabricated/misquoted citation): {hallucination_rate(results):.1%}")

    print(f"\n=== Tool-use efficiency ===")
    eff = tool_use_efficiency(results)
    for k, v in eff.items():
        print(f"{k}: {v}")

    make_chart(results, OUT_DIR / "tool_calls_vs_accuracy.png")
    print(f"\nChart saved to {OUT_DIR / 'tool_calls_vs_accuracy.png'}")

    summary = {
        "overall_accuracy": overall_accuracy(results),
        "accuracy_by_fault_type": accuracy_by_fault_type(results),
        "hallucination_rate": hallucination_rate(results),
        "tool_use_efficiency": eff,
        "n_incidents": len(results),
    }
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
