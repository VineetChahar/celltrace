# CellTrace — Design

## Network-stack decision (stated plainly, per spec requirement)

This build uses the **hand-written protocol-accurate synthesizer fallback**, not a live
UERANSIM+Open5GS deployment. Reasoning was made explicit with the user before starting:
Open5GS's ~10-microservice 5GC plus UERANSIM's UE/gNB simulator require a TUN device and
`NET_ADMIN` capability inside Docker, are historically flaky on macOS Docker Desktop, and
risk burning an entire session on container networking before any of the C++ parsing,
agent, or eval work (the actual point of this project) gets built. The synthesizer is
cross-checked field-for-field against the publicly downloadable 3GPP specs:
**38.331** (5G NR RRC) and **24.501** (5G NAS). **No log in this repository comes from a
live network. Every timestamp, message, and signal-quality value is synthetic.** This is
stated again in the README.

## Shared contract (pins all four components together)

### Log format

Three JSON-Lines files under `data/logs/`, one per protocol layer, globally sorted by
timestamp and interleaved across all simulated UEs/sessions (a real network log is one
merged stream, not one file per call — the parser's job is to window-query out of that
stream):

- `rrc.jsonl` — RRC-layer messages (gNB<->UE)
- `nas.jsonl` — NAS-layer messages (AMF<->UE, carried inside RRC but logged separately at
  the NAS layer per the spec's "log every layer separately" requirement)
- `phy.jsonl` — PHY-layer signal-quality samples, one row per UE per ~200ms tick

Every line, regardless of layer, has this envelope:

```json
{
  "ts": 1234.567,              // simulation-clock seconds, float, monotonic across the whole run
  "session_id": "sess_00042",  // one per UE attach-to-detach lifetime
  "ue_pseudo": "ue_9f3a2b7c",  // HMAC-pseudonymized subscriber id — see Anonymization
  "layer": "RRC" | "NAS" | "PHY",
  "msg_type": "RRCSetupRequest" | "RegistrationRequest" | "measurement" | ...,
  "direction": "UE->gNB" | "gNB->UE" | "UE->AMF" | "AMF->UE" | "N/A",
  "cell_id": "cell_03",
  "fields": { ... msg_type-specific, named per 38.331/24.501 IE names ... }
}
```

`fields` content is defined per `msg_type` in `synthesizer/messages_5g.py` — every field
name is the real 3GPP information-element name (e.g. `rrc-TransactionIdentifier`,
`establishmentCause`, `5gsRegistrationType`, `ngKSI`) so the C++ parser and the LLM agent
are both reading spec-accurate structures, not made-up JSON keys.

PHY `fields` = `{"rsrp_dbm": float, "rsrq_db": float, "sinr_db": float}`.

### Ground truth (never shown to the agent)

`data/incidents.jsonl` — one row per labeled failure incident, held out from everything
the agent/parser serve at query time:

```json
{
  "incident_id": "INC_0031",
  "fault_type": "HO_MISSED_MEASUREMENT",
  "ue_pseudo": "ue_9f3a2b7c",
  "session_id": "sess_00042",
  "reported_ts": 1240.100,
  "symptom": "connection dropped during handover",
  "window_start": 1220.0,
  "window_end": 1250.0
}
```

The eval harness reads this file to score the agent; the agent and its tools only ever
see `data/logs/*.jsonl` through the C++ bridge.

### Fault types (10), each a real protocol-level breakage

| fault_type | Layer(s) | What actually breaks |
|---|---|---|
| `AUTH_BAD_KEY` | NAS | UE's Authentication Response RES* doesn't match the network's XRES* (simulated bad shared key) -> AMF sends Authentication Reject |
| `REG_TIMEOUT_DROPPED_NAS` | NAS | RegistrationRequest sent, the RegistrationAccept is dropped in transit (never logged on the AMF->UE side) -> UE's T3510 timer expires, retries then aborts |
| `RRC_CONN_FAIL_CONGESTION` | RRC | RRCSetupRequest answered with RRCReject, `cause=congestion`, instead of RRCSetup |
| `HO_MISSED_MEASUREMENT` | RRC+PHY | RSRP crosses the A3-event handover threshold but the MeasurementReport is never sent (UE-side reporting failure) -> serving cell RRCReconfiguration (handover command) never issued -> radio link failure |
| `HANDOVER_RECONFIG_TIMEOUT` | RRC | RRCReconfiguration (with `mobilityControlInfo` to a target cell) is sent, but RRCReconfigurationComplete never arrives from the target cell -> handover failure, UE falls back |
| `PHY_SIGNAL_RLF` | PHY+RRC | RSRP/SINR degrade below the out-of-sync threshold over several consecutive samples with no compensating handover in time -> radio link failure -> RRC re-establishment attempt |
| `SECURITY_MODE_FAILURE` | NAS | SecurityModeCommand sent post-handover with a stale key context -> UE returns SecurityModeReject (integrity check failure) |
| `SERVICE_REQUEST_NO_CONTEXT` | NAS | ServiceRequest arrives at an AMF that has already dropped the UE's context (context timeout) -> ServiceReject, `cause=no-context` |
| `PAGING_TIMEOUT` | RRC | Network pages an idle UE; no RRCSetupRequest arrives before the paging timer expires (UE unreachable / DRX misconfiguration) |
| `DEREGISTRATION_IMPLICIT` | NAS | UE misses its periodic registration update -> network-initiated implicit deregistration after the mobile-reachable timer expires |

Each fault type is instantiated 6-10 times across different UEs/sessions/cells with
timing jitter -> 60-100 total labeled incidents (see `synthesizer/faults.py` for the
per-instance parameter randomization). Background "clean" sessions (no injected fault)
are also generated so the merged log stream isn't just back-to-back failures.

### Anonymization

Each simulated UE has an internal synthetic SUPI-like identifier (`imsi_sim_*`, e.g.
`"imsi_sim_001740"`) that **never touches disk or the LLM prompt**. Before any log line
is written or handed to the agent, it's replaced by `ue_pseudo = "ue_" + HMAC-SHA256(key,
imsi_sim)[:8]` — the same real ID always maps to the same pseudonym (so cross-layer /
cross-session correlation for a given UE still works), but the mapping is one-way and the
HMAC key lives only in `synthesizer/anonymize.py` at generation time, not in any output
file. `cell_id`/`session_id` are not considered sensitive and are left as-is. This is the
only identifier class present in these logs (no IMEI, no location beyond cell_id, no
payload/user-plane data — this is control-plane signaling only), so that's the extent of
what needed anonymizing.

### C++ <-> Python bridge

pybind11 native extension (`celltrace_parser`), not a socket. The C++ side owns a
mutex-protected ring buffer that a producer thread fills by tailing the three `.jsonl`
files (simulating a live stream); parsed, typed events are appended to
per-layer indexed stores. Python calls into the same process:

```python
import celltrace_parser as ctp
store = ctp.LogStore()
store.ingest_files("data/logs/rrc.jsonl", "data/logs/nas.jsonl", "data/logs/phy.jsonl")
rrc_msgs = store.query(layer="RRC", session_id="sess_00042", t_start=1220.0, t_end=1250.0)
```

`query()` returns already-structured Python dicts built from the parsed C++ structs — no
second JSON parse happens in Python. Throughput is benchmarked in
`parser/benchmarks/bench_throughput.cpp` (messages/sec sustained through the ring buffer
under concurrent producer load) and reported in the README.

### Agent tools

1. `get_rrc_window(session_id, t_start, t_end)`
2. `get_nas_window(session_id, t_start, t_end)`
3. `get_phy_window(session_id, t_start, t_end)`
4. `rag_lookup(query_text, k=3)` — brute-force cosine search over the 30-entry
   hand-written failure-pattern corpus, embedded with `all-MiniLM-L6-v2`

All four are backed by the same `LogStore` instance built once at agent startup.

### Citation grounding

The agent's final answer must include verbatim-quoted log lines with `layer` + `ts`. The
eval harness's citation verifier re-queries the exact same `LogStore` for that
`(layer, ts, session_id)` and does an exact-field diff against what the agent claimed —
this is the same `LogStore`/parser the agent used, not a re-implementation, so a
"hallucination" here means the agent's own tool output was misquoted, not a parser
discrepancy.
