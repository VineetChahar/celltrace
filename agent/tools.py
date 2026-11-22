"""Tool definitions the investigator agent can call. Each of the three log-window
tools is bound to one investigation's session_id (the agent is handed a specific
incident, like an on-call engineer handed a ticket -- it isn't guessing session
ids). All three read from the same LogStore instance the C++ parser built, so
every fact the agent sees traces back to a real parsed log line.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "parser" / "build"))
import celltrace_parser as ctp  # noqa: E402

from synthesizer.faults import FAULT_TYPES  # noqa: E402
from rag.retriever import get_retriever  # noqa: E402


def build_log_store(rrc_path: str, nas_path: str, phy_path: str) -> ctp.LogStore:
    store = ctp.LogStore()
    stats = store.ingest_files(rrc_path, nas_path, phy_path)
    if stats.parse_errors > 0:
        print(f"[log_store] warning: {stats.parse_errors} malformed lines skipped")
    return store


class InvestigationTools:
    def __init__(self, log_store: ctp.LogStore, session_id: str):
        self.log_store = log_store
        self.session_id = session_id
        self.call_log: list[dict] = []

    def _record(self, name: str, args: dict, result_summary: str):
        self.call_log.append({"tool": name, "args": args, "result_summary": result_summary})

    def get_rrc_window(self, t_start: float, t_end: float) -> list[dict]:
        rows = self.log_store.query("RRC", self.session_id, t_start, t_end)
        self._record("get_rrc_window", {"t_start": t_start, "t_end": t_end}, f"{len(rows)} RRC messages")
        return rows

    def get_nas_window(self, t_start: float, t_end: float) -> list[dict]:
        rows = self.log_store.query("NAS", self.session_id, t_start, t_end)
        self._record("get_nas_window", {"t_start": t_start, "t_end": t_end}, f"{len(rows)} NAS messages")
        return rows

    def get_phy_window(self, t_start: float, t_end: float) -> list[dict]:
        rows = self.log_store.query("PHY", self.session_id, t_start, t_end)
        self._record("get_phy_window", {"t_start": t_start, "t_end": t_end}, f"{len(rows)} PHY samples")
        return rows

    # --- absence / timing tools (agent v2) -----------------------------------
    #
    # The v1 tools only list what DID happen. Three fault mechanisms in
    # DESIGN.md are defined by what did NOT happen (an expected answer never
    # logged) or by how long a silence lasted relative to a protocol timer, and
    # the README's 3B-vs-7B result showed a bigger model can't infer that from
    # a list of events. These tools compute the absence / the gap explicitly.
    #
    # Every message they mention comes back as an evidence object carrying a
    # `line_id` ("<LAYER>@<ts>") plus the same layer/ts/msg_type/fields the
    # window tools return, so a citation built from it goes through the
    # unchanged citation verifier like any other.

    def _session_rows(self, layers=("RRC", "NAS", "PHY")) -> list[dict]:
        rows = []
        for layer in layers:
            rows.extend(self.log_store.query(layer, self.session_id, 0.0, _FAR_FUTURE))
        rows.sort(key=lambda r: (r["ts"], r["layer"]))
        return rows

    def find_missing_response(self, trigger_msg: str, expected_msg: str, window_s: float,
                              ue_id: str | None = None) -> dict:
        """For each occurrence of `trigger_msg` in this session, does `expected_msg`
        appear within `window_s` seconds after it (any layer)? `ue_id` is accepted
        for interface compatibility and ignored: like the window tools, this is
        bound to the incident's session."""
        window_s = float(window_s)
        rows = self._session_rows()
        if not rows:
            result = {"error": "no log lines for this session"}
            self._record("find_missing_response", {"trigger_msg": trigger_msg, "expected_msg": expected_msg,
                                                   "window_s": window_s}, "no rows")
            return result
        last_ts = rows[-1]["ts"]
        triggers = [r for r in rows if _same_msg(r["msg_type"], trigger_msg)]
        checks = []
        for trig in triggers[:MAX_OCCURRENCES]:
            t0 = trig["ts"]
            resp = next((r for r in rows if t0 < r["ts"] <= t0 + window_s and _same_msg(r["msg_type"], expected_msg)), None)
            checks.append({
                "trigger": _evidence(trig),
                "expected_found": resp is not None,
                "response": _evidence(resp) if resp else None,
                "response_delay_s": round(resp["ts"] - t0, 3) if resp else None,
                # an absence only means something if the log actually covers the window
                "window_fully_logged": last_ts >= t0 + window_s,
                "logged_after_trigger_s": round(last_ts - t0, 3),
            })
        result = {
            "trigger_msg": trigger_msg,
            "expected_msg": expected_msg,
            "window_s": window_s,
            "n_trigger_occurrences": len(triggers),
            "checks": checks,
            "any_missing": any(not c["expected_found"] for c in checks),
            "last_logged_event": _evidence(rows[-1]),
        }
        if not triggers:
            result["note"] = f"no {trigger_msg} message in this session's logs"
        self._record("find_missing_response",
                     {"trigger_msg": trigger_msg, "expected_msg": expected_msg, "window_s": window_s},
                     f"{len(triggers)} triggers, any_missing={result['any_missing']}")
        return result

    def gap_vs_timer(self, event_a: str, event_b: str, timer_name: str, ue_id: str | None = None) -> dict:
        """Gap from each `event_a` to the next `event_b` (if event_a == event_b,
        the next occurrence of the same message, i.e. a retry), compared with the
        nominal value of `timer_name` from TIMER_TABLE."""
        key = _timer_key(timer_name)
        if key is None:
            return {"error": f"unknown timer {timer_name!r}", "known_timers": sorted(TIMER_TABLE)}
        timer = TIMER_TABLE[key]
        rows = self._session_rows()
        last_ts = rows[-1]["ts"] if rows else None
        a_rows = [r for r in rows if _same_msg(r["msg_type"], event_a)]
        pairs = []
        for a in a_rows[:MAX_OCCURRENCES]:
            nominal_s, nominal_source = _nominal_seconds(key, a)
            b = next((r for r in rows if r["ts"] > a["ts"] and _same_msg(r["msg_type"], event_b)), None)
            pair = {"event_a": _evidence(a), "nominal_s": nominal_s, "nominal_source": nominal_source}
            if b is not None:
                gap = b["ts"] - a["ts"]
                pair.update({
                    "event_b": _evidence(b),
                    "gap_s": round(gap, 3),
                    "gap_matches_timer_expiry": abs(gap - nominal_s) <= _tolerance(nominal_s),
                    "gap_vs_nominal_ratio": round(gap / nominal_s, 3),
                })
            else:
                observed = last_ts - a["ts"]
                pair.update({
                    "event_b": None,
                    "event_b_never_logged_after_event_a": True,
                    "logged_after_event_a_s": round(observed, 3),
                    # no event_b at all, and the log runs past the timer: the timer would have expired
                    "timer_would_have_expired_unanswered": observed >= nominal_s,
                })
            pairs.append(pair)
        result = {
            "event_a": event_a, "event_b": event_b, "timer": key,
            "timer_description": timer["description"],
            "match_tolerance": "|gap - nominal| <= max(0.1 s, 20% of nominal)",
            "n_event_a_occurrences": len(a_rows),
            "pairs": pairs,
        }
        if not a_rows:
            result["note"] = f"no {event_a} message in this session's logs"
        self._record("gap_vs_timer", {"event_a": event_a, "event_b": event_b, "timer_name": timer_name},
                     f"{len(pairs)} pairs")
        return result

    def last_event_before(self, t: float, layer: str = "ANY", ue_id: str | None = None) -> dict:
        """The last message (RRC/NAS) or measurement (PHY) strictly before time t
        on one layer, or across all layers with layer="ANY"."""
        t = float(t)
        layer_u = (layer or "ANY").upper()
        if layer_u not in ("RRC", "NAS", "PHY", "ANY"):
            return {"error": f"layer must be RRC, NAS, PHY or ANY, got {layer!r}"}
        layers = ("RRC", "NAS", "PHY") if layer_u == "ANY" else (layer_u,)
        before = [r for r in self._session_rows(layers) if r["ts"] < t]
        if not before:
            result = {"t": t, "layer": layer_u, "event": None, "note": f"no {layer_u} event before t={t}"}
        else:
            ev = before[-1]
            result = {"t": t, "layer": layer_u, "event": _evidence(ev), "seconds_before_t": round(t - ev["ts"], 3)}
        self._record("last_event_before", {"t": t, "layer": layer_u},
                     "none" if result["event"] is None else result["event"]["line_id"])
        return result

    def rag_lookup(self, query: str, k: int = 3) -> list[dict]:
        results = get_retriever().search(query, k=k)
        self._record("rag_lookup", {"query": query, "k": k}, f"{len(results)} corpus entries")
        return [{"id": r["id"], "title": r["title"], "text": r["text"]} for r in results]


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_rrc_window",
            "description": "Fetch RRC-layer (radio resource control) messages for this session in a time window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "t_start": {"type": "number", "description": "window start, seconds"},
                    "t_end": {"type": "number", "description": "window end, seconds"},
                },
                "required": ["t_start", "t_end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_nas_window",
            "description": "Fetch NAS-layer (non-access stratum: registration/authentication/security/service) messages for this session in a time window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "t_start": {"type": "number", "description": "window start, seconds"},
                    "t_end": {"type": "number", "description": "window end, seconds"},
                },
                "required": ["t_start", "t_end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_phy_window",
            "description": "Fetch PHY-layer signal-quality samples (rsrp_dbm, rsrq_db, sinr_db) for this session in a time window.",
            "parameters": {
                "type": "object",
                "properties": {
                    "t_start": {"type": "number", "description": "window start, seconds"},
                    "t_end": {"type": "number", "description": "window end, seconds"},
                },
                "required": ["t_start", "t_end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rag_lookup",
            "description": "Search known failure-pattern documentation (postmortem/runbook style) for entries relevant to a symptom or observation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "free-text description of the symptom or observed pattern"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_root_cause",
            "description": "Submit your final root-cause determination. Call this only once, when you have enough evidence to name a specific fault type and cite the log lines that support it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "fault_type": {"type": "string", "enum": FAULT_TYPES + ["OTHER"]},
                    "explanation": {"type": "string", "description": "one-to-three sentence explanation of the root cause"},
                    "citations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "layer": {"type": "string", "enum": ["RRC", "NAS", "PHY"]},
                                "ts": {"type": "number"},
                                "quoted_text": {"type": "string", "description": "verbatim msg_type and/or field value copied from the tool output at this ts"},
                            },
                            "required": ["layer", "ts", "quoted_text"],
                        },
                    },
                },
                "required": ["fault_type", "explanation", "citations"],
            },
        },
    },
]


# --- helpers + timer table for the absence / timing tools ----------------------

_FAR_FUTURE = 1e12
MAX_OCCURRENCES = 5  # keeps a tool result small enough for a 3B model's context

# Nominal timer values, taken from the specs' timer definitions -- not from the
# labeled incidents. Where the spec leaves a value to network configuration, the
# entry says so and states the value used here.
TIMER_TABLE = {
    "T300": {
        "seconds": 1.0,
        "description": "RRC connection setup guard (TS 38.331): UE starts it on RRCSetupRequest, stops on "
                       "RRCSetup/RRCReject. Configurable (ms100..ms2000); this table uses 1 s.",
    },
    "t304": {
        "seconds": 1.0,
        "description": "Handover execution guard (TS 38.331): UE starts it on an RRCReconfiguration that "
                       "moves it to a target cell, stops on successful access there; on expiry the UE "
                       "starts re-establishment. Configured per handover: the t304_ms value logged in "
                       "that RRCReconfiguration is used when present, else 1 s.",
    },
    "T3510": {
        "seconds": 15.0,
        "description": "Registration guard (TS 24.501): UE starts it on RegistrationRequest, stops on "
                       "RegistrationAccept/RegistrationReject; on expiry the UE retries the registration. 15 s.",
    },
    "T3513": {
        "seconds": 2.0,
        "description": "Paging guard (TS 24.501): network starts it when it pages the UE, stops when the UE "
                       "responds with a new connection (RRCSetupRequest / ServiceRequest). The spec leaves "
                       "the value network-dependent; this table uses 2 s.",
    },
    "T3560": {
        "seconds": 6.0,
        "description": "Network-side guard for AuthenticationRequest and SecurityModeCommand (TS 24.501): "
                       "stops on the UE's response. 6 s.",
    },
}


def _same_msg(actual: str, wanted) -> bool:
    return isinstance(wanted, str) and actual.lower() == wanted.strip().lower()


def _timer_key(name) -> str | None:
    if not isinstance(name, str):
        return None
    for key in TIMER_TABLE:
        if key.lower() == name.strip().lower():
            return key
    return None


def _nominal_seconds(key: str, event_a: dict) -> tuple[float, str]:
    if key == "t304":
        mci = (event_a.get("fields") or {}).get("mobilityControlInfo") or {}
        if isinstance(mci, dict) and isinstance(mci.get("t304_ms"), (int, float)):
            return mci["t304_ms"] / 1000.0, f"t304_ms logged in {event_a['layer']}@{event_a['ts']}"
    return TIMER_TABLE[key]["seconds"], "timer table"


def _tolerance(nominal_s: float) -> float:
    return max(0.1, 0.2 * nominal_s)


def _evidence(row: dict) -> dict:
    return {
        "line_id": f"{row['layer']}@{row['ts']}",
        "layer": row["layer"],
        "ts": row["ts"],
        "msg_type": row["msg_type"],
        "direction": row["direction"],
        "fields": row["fields"],
    }


def _timer_list_text() -> str:
    return "; ".join(f"{k} ({v['seconds']:g} s)" for k, v in TIMER_TABLE.items())


ABSENCE_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "find_missing_response",
            "description": (
                "Absence check. For every occurrence of trigger_msg in this session, reports whether "
                "expected_msg was logged within window_s seconds after it (any layer), with line_id/ts of "
                "the trigger and of the response if found, and whether the log even covers the whole "
                "window. Standard request -> answer pairs: RRCSetupRequest -> RRCSetup; "
                "RRCReconfiguration -> RRCReconfigurationComplete; Paging -> RRCSetupRequest; "
                "RegistrationRequest -> RegistrationAccept; AuthenticationRequest -> AuthenticationResponse; "
                "SecurityModeCommand -> SecurityModeComplete; ServiceRequest -> ServiceAccept."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "trigger_msg": {"type": "string", "description": "msg_type that should have been answered, e.g. RRCSetupRequest"},
                    "expected_msg": {"type": "string", "description": "msg_type of the expected answer"},
                    "window_s": {"type": "number", "description": "how many seconds after the trigger to look"},
                },
                "required": ["trigger_msg", "expected_msg", "window_s"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gap_vs_timer",
            "description": (
                "Timing check. Measures the gap from each event_a to the next event_b in this session (if "
                "event_a and event_b are the same msg_type, the gap to its next repeat, i.e. a retry) and "
                "compares it with a 3GPP timer's nominal value, reporting whether the gap matches that "
                "timer expiring. If event_b never follows event_a, reports how long the log continued and "
                "whether the timer would have expired unanswered. Timers: " + _timer_list_text() + "."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_a": {"type": "string", "description": "msg_type that starts the interval"},
                    "event_b": {"type": "string", "description": "msg_type that ends the interval"},
                    "timer_name": {"type": "string", "enum": list(TIMER_TABLE)},
                },
                "required": ["event_a", "event_b", "timer_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "last_event_before",
            "description": (
                "Returns the last logged message (RRC/NAS) or signal measurement (PHY) strictly before time "
                "t on one layer, or across all layers with layer=ANY, with its line_id/ts and how long "
                "before t it happened. Use it to see what a layer last did before a failure, or how long "
                "it had been silent."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "t": {"type": "number", "description": "time, seconds"},
                    "layer": {"type": "string", "enum": ["RRC", "NAS", "PHY", "ANY"]},
                },
                "required": ["t", "layer"],
            },
        },
    },
]
