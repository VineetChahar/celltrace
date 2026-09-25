"""Agentic root-cause investigator. Given a reported symptom, plans and executes
a sequence of tool calls (deciding what to look at next based on what it's
already seen) until it can name a root cause and cite the log lines that
support it. The full sequence of tool calls, arguments, and any reasoning text
the model emits along the way is captured in the returned trace for audit.
"""
import json
import time

import ollama

from agent.tools import TOOL_SCHEMAS, InvestigationTools

MODEL = "qwen2.5:3b-instruct"
MAX_STEPS = 10
SUBMIT_ONLY_TOOL = [t for t in TOOL_SCHEMAS if t["function"]["name"] == "submit_root_cause"]
# Caps worst-case generation length per call -- without this a degenerate
# repetition loop in the small quantized model can run for many minutes on a
# single call instead of the few hundred tokens a reasoning+tool-call turn
# actually needs.
GEN_OPTIONS = {"num_predict": 400}

SYSTEM_PROMPT = """\
You are a cellular network root-cause investigator. You are handed a reported
incident (a session id, a rough timestamp, and a one-line symptom) for a real
5G network session. You must determine which specific fault type caused it.

You have four tools: get_rrc_window, get_nas_window, get_phy_window (each
returns real log messages for THIS session in a time window you choose), and
rag_lookup (searches known failure-pattern documentation for a query you
write). Start with a window of a few seconds before to a few seconds after
the reported timestamp, then WIDEN or narrow it, and move to a DIFFERENT
layer, based on what you actually see -- e.g. if RRC shows a reconfiguration
was sent but nothing confirms it completed, check PHY for signal degradation
at that time before concluding anything. Don't call all tools in a fixed
order out of habit; let the evidence decide what you check next.

When you have enough evidence, call submit_root_cause exactly once with:
- fault_type: your best-matching fault type (or OTHER if none fit)
- explanation: 1-3 sentences
- citations: a list of {layer, ts, quoted_text}, where quoted_text MUST be
  copied verbatim (msg_type and/or a field value) from what a tool actually
  returned to you at that exact ts. Do not invent or paraphrase a citation --
  an investigation with a fabricated citation is worse than one with fewer
  citations.

Call at least one evidence-gathering tool before submit_root_cause. Do not
guess without looking at the logs first.
"""


def _tool_call_args(tc) -> dict:
    args = tc.function.arguments
    if isinstance(args, str):
        try:
            return json.loads(args)
        except json.JSONDecodeError:
            return {}
    return dict(args)


def investigate(session_id: str, reported_ts: float, symptom: str, tools: InvestigationTools,
                 model: str = MODEL, max_steps: int = MAX_STEPS) -> dict:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Incident report:\nsession_id: {session_id}\n"
            f"reported_ts: {reported_ts}\nsymptom: \"{symptom}\"\n\n"
            f"A reasonable first window to check is roughly {reported_ts - 15:.1f} to {reported_ts + 5:.1f}."
        )},
    ]
    trace = {"session_id": session_id, "reported_ts": reported_ts, "symptom": symptom, "steps": []}
    final_answer = None
    fn_table = {
        "get_rrc_window": tools.get_rrc_window,
        "get_nas_window": tools.get_nas_window,
        "get_phy_window": tools.get_phy_window,
        "rag_lookup": tools.rag_lookup,
    }

    for step in range(max_steps):
        t0 = time.time()
        resp = ollama.chat(model=model, messages=messages, tools=TOOL_SCHEMAS, options=GEN_OPTIONS)
        latency = time.time() - t0
        msg = resp["message"]
        reasoning = (msg.get("content") or "").strip()
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            trace["steps"].append({"step": step, "reasoning": reasoning, "tool_calls": [], "latency_s": latency})
            messages.append({"role": "assistant", "content": reasoning})
            messages.append({"role": "user", "content": (
                "Continue the investigation with a tool call, or call submit_root_cause "
                "with your final answer now."
            )})
            continue

        messages.append({"role": "assistant", "content": reasoning, "tool_calls": tool_calls})
        step_record = {"step": step, "reasoning": reasoning, "tool_calls": [], "latency_s": latency}

        for tc in tool_calls:
            name = tc.function.name
            args = _tool_call_args(tc)
            step_record["tool_calls"].append({"name": name, "args": args})

            if name == "submit_root_cause":
                final_answer = args
                messages.append({"role": "tool", "content": "recorded"})
                trace["steps"].append(step_record)
                trace["final_answer"] = final_answer
                trace["n_tool_calls"] = sum(
                    1 for s in trace["steps"] for tc in s["tool_calls"] if tc["name"] != "submit_root_cause"
                )
                return trace

            fn = fn_table.get(name)
            if fn is None:
                result = {"error": f"unknown tool {name}"}
            else:
                try:
                    result = fn(**args)
                except TypeError as e:
                    result = {"error": str(e)}
            messages.append({"role": "tool", "content": json.dumps(result)})

        trace["steps"].append(step_record)

    # Step budget exhausted without a submission: force one last call, restricted
    # to only the submit tool, so the agent can't wander off into more tool use
    # and instead has to commit to its best current assessment -- matching what
    # a real on-call engineer has to do when told "give me your best guess now".
    messages.append({"role": "user", "content": (
        "You are out of investigation time. Call submit_root_cause now with your "
        "best assessment based on everything you've already seen."
    )})
    resp = ollama.chat(model=model, messages=messages, tools=SUBMIT_ONLY_TOOL, options=GEN_OPTIONS)
    tool_calls = resp["message"].get("tool_calls") or []
    if tool_calls and tool_calls[0].function.name == "submit_root_cause":
        final_answer = _tool_call_args(tool_calls[0])
        trace["steps"].append({"step": max_steps, "reasoning": "(forced final call)",
                                "tool_calls": [{"name": "submit_root_cause", "args": final_answer}],
                                "latency_s": 0.0})

    trace["final_answer"] = final_answer or {
        "fault_type": "OTHER", "explanation": "agent did not submit a final answer even when forced",
        "citations": [],
    }
    trace["n_tool_calls"] = sum(
        1 for s in trace["steps"] for tc in s["tool_calls"] if tc["name"] != "submit_root_cause"
    )
    trace["timed_out"] = final_answer is None
    return trace
