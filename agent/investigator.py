"""Agentic root-cause investigator. Given a reported symptom, plans and executes
a sequence of tool calls (deciding what to look at next based on what it's
already seen) until it can name a root cause and cite the log lines that
support it. The full sequence of tool calls, arguments, and any reasoning text
the model emits along the way is captured in the returned trace for audit.

Diagnose-then-fix pass (see LEARNING.md "Part 1" write-up): the original
version asked the model to conclude via a native `submit_root_cause` tool
call. Reading failed transcripts showed the model usually reasoned fine and
even wrote out a complete, correct-looking answer -- as plain prose, not as
an actual invoked tool call -- especially once nudged or forced. That's a
format/completion gap, not a reasoning gap, so evidence-gathering still uses
native tool-calling (that part worked), but the FINAL answer is produced by a
dedicated call using Ollama's JSON-schema-constrained structured output
(`format=`), which reliably returns a parseable object instead of hoping the
model emits a well-formed tool call under pressure.
"""
import json
import time

import ollama

from agent.tools import TOOL_SCHEMAS, InvestigationTools
from synthesizer.faults import FAULT_TYPES

MODEL = "qwen2.5:3b-instruct"
MAX_STEPS = 10
# Only the four evidence tools are offered as native tool calls now -- see
# module docstring for why submit_root_cause was removed from this list.
EVIDENCE_TOOL_SCHEMAS = [t for t in TOOL_SCHEMAS if t["function"]["name"] != "submit_root_cause"]
# Caps worst-case generation length per call -- without this a degenerate
# repetition loop in the small quantized model can run for many minutes on a
# single call instead of the few hundred tokens a reasoning+tool-call turn
# actually needs.
GEN_OPTIONS = {"num_predict": 400}

FINAL_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "fault_type": {"type": "string", "enum": FAULT_TYPES + ["OTHER"]},
        "explanation": {"type": "string"},
        "insufficient_evidence": {
            "type": "boolean",
            "description": "true if you genuinely don't have enough evidence to name a specific fault type",
        },
        "citations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "layer": {"type": "string", "enum": ["RRC", "NAS", "PHY"]},
                    "ts": {"type": "number"},
                    "quoted_text": {"type": "string"},
                },
                "required": ["layer", "ts", "quoted_text"],
            },
        },
    },
    "required": ["fault_type", "explanation", "insufficient_evidence", "citations"],
}

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

Investigate using the tools until you have enough evidence, then say so in
plain text (you'll be asked to formalize your answer once you stop calling
tools). Only cite a log line you actually saw in a tool's result -- never
invent a timestamp or a field value.
"""

FINAL_PROMPT = """\
Based on everything above, give your final structured answer now.

Set insufficient_evidence to true if you genuinely don't have enough evidence
to name a specific fault type with confidence -- that is a valid, honest
answer and is better than guessing. Every citation's quoted_text and ts must
come from a real tool result shown above; if you have no solid citation,
leave citations empty rather than inventing one.
"""


def _tool_call_args(tc) -> dict:
    args = tc.function.arguments
    if isinstance(args, str):
        try:
            return json.loads(args)
        except json.JSONDecodeError:
            return {}
    return dict(args)


def _structured_final_answer(model: str, messages: list) -> dict:
    call_messages = messages + [{"role": "user", "content": FINAL_PROMPT}]
    resp = ollama.chat(model=model, messages=call_messages, format=FINAL_ANSWER_SCHEMA, options=GEN_OPTIONS)
    content = resp["message"].get("content") or ""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        parsed = {
            "fault_type": "OTHER",
            "explanation": f"structured output failed to parse: {content[:200]!r}",
            "insufficient_evidence": True,
            "citations": [],
        }
    parsed.setdefault("citations", [])
    parsed.setdefault("insufficient_evidence", False)
    return parsed


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
    fn_table = {
        "get_rrc_window": tools.get_rrc_window,
        "get_nas_window": tools.get_nas_window,
        "get_phy_window": tools.get_phy_window,
        "rag_lookup": tools.rag_lookup,
    }

    n_evidence_calls = 0
    used_early_nudge = False

    for step in range(max_steps):
        t0 = time.time()
        resp = ollama.chat(model=model, messages=messages, tools=EVIDENCE_TOOL_SCHEMAS, options=GEN_OPTIONS)
        latency = time.time() - t0
        msg = resp["message"]
        reasoning = (msg.get("content") or "").strip()
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            trace["steps"].append({"step": step, "reasoning": reasoning, "tool_calls": [], "latency_s": latency})
            messages.append({"role": "assistant", "content": reasoning})
            if n_evidence_calls == 0 and not used_early_nudge:
                # Hasn't looked at any logs yet -- nudge once rather than let it
                # conclude (or declare insufficient evidence) without looking.
                used_early_nudge = True
                messages.append({"role": "user", "content": "Look at the logs with a tool before concluding anything."})
                continue
            break  # model produced prose instead of a tool call: ready to conclude (or stuck) -- formalize it now

        messages.append({"role": "assistant", "content": reasoning, "tool_calls": tool_calls})
        step_record = {"step": step, "reasoning": reasoning, "tool_calls": [], "latency_s": latency}

        for tc in tool_calls:
            name = tc.function.name
            args = _tool_call_args(tc)
            step_record["tool_calls"].append({"name": name, "args": args})
            n_evidence_calls += 1

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

    final_answer = _structured_final_answer(model, messages)
    trace["final_answer"] = final_answer
    trace["n_tool_calls"] = n_evidence_calls
    return trace
