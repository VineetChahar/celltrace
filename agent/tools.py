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
