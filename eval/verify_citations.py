"""Citation grounding verifier. Re-queries the SAME LogStore the agent's tools
used and checks that every citation the agent made (a) points at a real
message (right layer/session/timestamp) and (b) actually says what the agent
claims it says. This is the hallucination check.

"Says what the agent claims" is checked semantically, not by exact string
match: the agent is allowed to quote a field value in readable form (e.g.
"-107.6 dBm" for {"rsrp_dbm": -107.6}) rather than verbatim JSON syntax. A
citation is grounded if it's a verbatim substring of the raw line, OR
references the real msg_type, OR any string field value appears in it, OR any
number in it matches a real numeric field value within rounding tolerance.
"""
import re

TS_TOLERANCE = 0.01  # seconds; covers float round-trip through JSON, not "close enough"
NUM_TOLERANCE = 0.05  # log values are rounded to 1 decimal; this covers that rounding
_NUM_RE = re.compile(r"-?\d+\.?\d*")


def _flatten_leaf_values(value, out: list):
    if isinstance(value, dict):
        for v in value.values():
            _flatten_leaf_values(v, out)
    elif isinstance(value, list):
        for v in value:
            _flatten_leaf_values(v, out)
    else:
        out.append(value)


def _semantic_match(quoted: str, quoted_norm: str, quoted_nums: list[float], msg: dict) -> bool:
    leaves = [msg["msg_type"], msg["direction"], msg["cell_id"]]
    _flatten_leaf_values(msg["fields"], leaves)
    for v in leaves:
        if isinstance(v, str) and v and v.lower() in quoted_norm:
            return True
    if quoted_nums:
        for v in leaves:
            if isinstance(v, (int, float)) and any(abs(qn - v) <= NUM_TOLERANCE for qn in quoted_nums):
                return True
    return False


def verify_citation(log_store, session_id: str, citation: dict) -> dict:
    layer = citation.get("layer")
    ts = citation.get("ts")
    quoted = (citation.get("quoted_text") or "").strip()

    if layer not in ("RRC", "NAS", "PHY") or ts is None:
        return {"ok": False, "reason": "citation missing a valid layer/ts"}

    matches = log_store.query(layer, session_id, ts - TS_TOLERANCE, ts + TS_TOLERANCE)
    if not matches:
        return {"ok": False, "reason": f"no {layer} message exists for {session_id} near ts={ts}"}

    quoted_norm = quoted.lower()
    quoted_nums = [float(x) for x in _NUM_RE.findall(quoted)]

    for msg in matches:
        if quoted_norm and quoted_norm in msg["raw_line"].lower():
            return {"ok": True, "reason": "verbatim substring match", "matched_ts": msg["ts"]}
        if _semantic_match(quoted, quoted_norm, quoted_nums, msg):
            return {"ok": True, "reason": "matches real field value(s) in the message", "matched_ts": msg["ts"]}

    return {
        "ok": False,
        "reason": "a message exists at that layer/ts but quoted text doesn't match its content",
        "actual_candidates": [m["raw_line"] for m in matches],
    }


def verify_investigation(log_store, session_id: str, citations: list[dict]) -> dict:
    if not citations:
        return {"any_fabricated": True, "reason": "no citations provided", "per_citation": []}
    results = [verify_citation(log_store, session_id, c) for c in citations]
    any_bad = any(not r["ok"] for r in results)
    return {"any_fabricated": any_bad, "per_citation": results}
