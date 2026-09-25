# CellTrace

An agentic root-cause investigator for cellular (5G) network signaling failures.
A local LLM agent is handed a reported symptom (session id, timestamp, one-line
description), queries RRC/NAS/PHY log layers as tools -- deciding which layer to
check next based on what it's already seen -- and produces a root-cause claim
with verbatim-cited log lines. A harness then checks whether the root cause was
actually right and whether every citation is real.

**Everything in this repo runs locally.** No paid APIs, no cloud accounts, no
logins, no real subscriber data anywhere.

## Network-stack decision

This build uses a **hand-written, protocol-accurate log synthesizer**, not a
live UERANSIM+Open5GS deployment. **No log in this repository comes from a live
network -- every timestamp, message, and signal-quality value is synthetic.**

This was a deliberate call, made explicit before writing any code: Open5GS's
~10-microservice 5G core plus UERANSIM's UE/gNB simulator need a TUN device and
`NET_ADMIN` inside Docker, are historically flaky on macOS Docker Desktop, and
risked burning an entire build session on container networking before any of
the actual point of this project -- the C++ parser, the agent, the eval harness
-- got built. The synthesizer is cross-checked message-by-message against the
public 3GPP specs **38.331** (5G NR RRC) and **24.501** (5G NAS): every field
name in every message is the real information-element name from those specs.
Full schema and fault-type-to-mechanism mapping is in [DESIGN.md](DESIGN.md).

## Fault types

10 distinct, protocol-level fault types, each injected 6-10 times across
different simulated UEs/cells/sessions with timing jitter, for **86 total
labeled failure incidents** (plus 30 fault-free sessions generated alongside
them so the log stream isn't just back-to-back failures):

| fault_type | layer(s) | mechanism |
|---|---|---|
| `AUTH_BAD_KEY` | NAS | bad shared key -> AuthenticationReject (MAC-failure) |
| `REG_TIMEOUT_DROPPED_NAS` | NAS | RegistrationAccept dropped in transit -> T3510 timeout, retry |
| `RRC_CONN_FAIL_CONGESTION` | RRC | RRCSetupRequest -> RRCReject (congestion) |
| `HO_MISSED_MEASUREMENT` | RRC+PHY | RSRP crosses handover threshold, MeasurementReport never sent |
| `HANDOVER_RECONFIG_TIMEOUT` | RRC | handover command sent, RRCReconfigurationComplete never arrives (t304 expiry) |
| `PHY_SIGNAL_RLF` | PHY+RRC | RSRP/SINR degrade past out-of-sync thresholds, no compensating handover |
| `SECURITY_MODE_FAILURE` | NAS | stale key context post-handover -> SecurityModeReject |
| `SERVICE_REQUEST_NO_CONTEXT` | NAS | AMF already dropped UE context -> ServiceReject (no-context) |
| `PAGING_TIMEOUT` | RRC | Paging sent, no RRCSetupRequest response before timer expiry |
| `DEREGISTRATION_IMPLICIT` | NAS | missed periodic registration -> AMF-internal implicit deregistration |

See [DESIGN.md](DESIGN.md) for the full mechanism description and the shared
log-format contract every component below reads and writes.

## Architecture

```
synthesizer/  -> data/logs/{rrc,nas,phy}.jsonl + data/incidents.jsonl (ground truth)
                          |
                          v
parser/ (C++)     ring buffer -> parse -> LogStore   (pybind11: celltrace_parser)
                          |
                          v
agent/ (Python)   Ollama tool-calling loop over LogStore + rag/ corpus
                          |
                          v
eval/             root-cause accuracy, citation-grounding verifier, tool-use stats
```

## C++ parser

Not a wrapper around a Python log reader -- the actual parsing and structuring
happens in C++, exposed to Python as a pybind11 native extension
(`celltrace_parser`), so Python only ever consumes already-structured dicts.

- **`json.hpp`/`json.cpp`**: a small hand-written recursive-descent JSON parser
  (no third-party JSON library). Builds a real `json::Value` tree (move-only,
  `unique_ptr`-indirected Object/Array so the recursive type is safe to define),
  not a string Python would have to re-parse.
- **`ring_buffer.hpp`**: a lock-free SPSC ring buffer (atomics, cache-line-padded
  head/tail to avoid false sharing), capacity rounded to a power of two.
- **`log_store.cpp`**: three independent producer/consumer pipelines (one per
  log file) run concurrently -- a producer thread reads lines into the ring
  buffer, a consumer thread parses them out the other side -- then all three
  per-layer stores are sorted by timestamp once ingestion completes, so
  out-of-order arrival (real logs aren't always delivered in order) doesn't
  break windowed queries.
- **Unit tests** (Catch2, 35 test cases / 200,101 assertions): valid parsing,
  malformed JSON (truncated objects/arrays/strings, bad literals, unescaped
  control characters, trailing garbage), missing/wrong-typed envelope fields,
  a concurrent 200k-item producer/consumer ordering stress test, and a
  file-based integration test through the real threaded ingestion pipeline
  against a fixture file with malformed lines mixed into valid ones. **Clean
  under AddressSanitizer + UndefinedBehaviorSanitizer.**

**Throughput** (synthetic benchmark, `parser/benchmarks/bench_throughput.cpp`,
Release build, Apple M-series):

| total messages | wall time (s) | messages/sec |
|---:|---:|---:|
| 150,000 | 0.093 | 1,619,132 |
| 600,000 | 0.354 | 1,692,921 |
| 1,500,000 | 0.949 | 1,581,398 |

On the actual generated dataset for this project (16,489 real log lines across
all three layers) ingestion is dominated by thread-startup and file-I/O
overhead rather than parse cost, since the dataset is small: ~55K msgs/sec
end-to-end from Python via the pybind11 bridge. The number above is the
sustained parse-pipeline throughput at a scale where startup cost stops
mattering.

## RAG layer

25 hand-authored, postmortem/runbook-style entries in
[`rag/corpus.json`](rag/corpus.json) (2 per fault type + 5 general triage/cause-code
entries), embedded locally with `sentence-transformers/all-MiniLM-L6-v2` (no
API calls, no login). Retrieval is brute-force cosine similarity over a 25x384
matrix (`rag/retriever.py`) -- at this corpus size a FAISS index would be pure
overhead.

## Agent

`agent/investigator.py` runs an Ollama tool-calling loop (`qwen2.5:3b-instruct`,
local) with four tools (`get_rrc_window`, `get_nas_window`, `get_phy_window`,
`rag_lookup`) plus a required `submit_root_cause` tool that structures the
final answer (fault type, explanation, and a list of `{layer, ts, quoted_text}`
citations) so it's never free text with no traceable evidence. The agent picks
its own window sizes and which layer to check next -- it isn't forced through
a fixed tool order. If it exhausts its step budget without submitting, one
final call restricted to only the submit tool forces a best-guess answer
rather than silently timing out.

### Example transcript

A correctly-solved, real, non-cherry-picked-for-difficulty investigation
(`INC_0008`, full transcript in `eval_results/transcripts.jsonl`):

> **Incident**: session `sess_00008`, reported_ts=3058.6, symptom: *"registration
> never completed, UE stuck retrying"*

1. **Tool call**: `get_rrc_window(3043.6, 3063.6)` -> empty result.
2. **Reasoning**: *"It seems that no RRC messages were found in the provided
   window. Let's expand the window slightly."*
3. **Tool call**: `get_rrc_window(3040.6, 3065.6)` -> RRCSetupRequest/Setup/
   SetupComplete found.
4. **Reasoning**: *"...Given the symptom 'registration never completed, UE
   stuck retrying,' it seems the UE might be stuck in a retry loop after the
   RRCSetupComplete message. Let's check the NAS layer."*
5. **Tool call**: `get_nas_window(3040.6, 3065.6)` -> shows authentication and
   security mode succeeding, then **two** RegistrationRequests with no
   RegistrationAccept between them.
6. **Reasoning**: *"...the UE is retrying the registration without completing
   successfully... Let's use rag_lookup to search for relevant failure
   patterns."*
7. **Tool call**: `rag_lookup("UE stuck retrying registration, NAS layer")` ->
   retrieves the `REG_TIMEOUT_DROPPED_NAS-1` corpus entry.
8. **Final answer**: `fault_type=REG_TIMEOUT_DROPPED_NAS`, citing the second
   RegistrationRequest verbatim (`ts=3058.559`, `layer=NAS`) -- **correct**,
   and the citation verifies clean against the real log.

This is the adaptive, cross-tool behavior the spec asked for: it didn't call
all four tools in a fixed order, it widened a window after an empty result,
switched layers based on what RRC actually showed, and only reached for RAG
once it had a concrete pattern to match against.

## Evaluation

Run: `python -m eval.run_eval` (or `make eval` / `make docker-eval`). Every one
of the 86 labeled incidents is run through a fresh investigation against the
real `LogStore`, using the real local model -- no shortcuts, no mocked tools.

**Overall root-cause accuracy: 24.4% (86/86 incidents, `qwen2.5:3b-instruct`).**
Reported honestly, per fault type, because it isn't one blended number worth
hiding behind:

| fault_type | n | correct | accuracy |
|---|---:|---:|---:|
| RRC_CONN_FAIL_CONGESTION | 10 | 6 | 60.0% |
| SERVICE_REQUEST_NO_CONTEXT | 10 | 5 | 50.0% |
| PHY_SIGNAL_RLF | 8 | 3 | 37.5% |
| AUTH_BAD_KEY | 6 | 2 | 33.3% |
| DEREGISTRATION_IMPLICIT | 7 | 2 | 28.6% |
| SECURITY_MODE_FAILURE | 8 | 2 | 25.0% |
| REG_TIMEOUT_DROPPED_NAS | 10 | 1 | 10.0% |
| HANDOVER_RECONFIG_TIMEOUT | 10 | 0 | 0.0% |
| HO_MISSED_MEASUREMENT | 7 | 0 | 0.0% |
| PAGING_TIMEOUT | 10 | 0 | 0.0% |

The split is not random. **The agent does reasonably well on fault types with
one explicit, unambiguous reject message in a single layer** (a plain
RRCReject or ServiceReject) and **fails completely on fault types whose
signature is the *absence* of an expected message, or requires correlating
two layers to notice that absence** (a MeasurementReport that should have
been sent but wasn't; an RRCReconfigurationComplete that never arrives; a
Paging message nobody answers). `qwen2.5:3b-instruct` is a small, local,
3-billion-parameter model -- this result is a real, if unflattering, measure
of what that scale can and can't do on genuinely open-ended cross-layer
diagnostic reasoning, not a bug being reported around.

**Citation grounding: 75.6% of investigations contain at least one fabricated
or misquoted citation.** Breaking that down (see
`eval/recompute_verification.py`'s output): the dominant cause (49 of 65
flagged investigations) is the agent submitting **zero citations** alongside
a guess -- which this harness counts as ungrounded, correctly, since a claim
with no evidence isn't grounded regardless of whether the guess happened to
be right. A smaller number are genuinely invented evidence: timestamps that
look like Unix epoch values (`1583930304.021`) instead of this project's
simulation-clock seconds, or timestamps a few tenths of a second off from
any real message. (An earlier version of this verifier flagged citations
that quoted a real field value in readable prose, e.g. `"-107.6 dBm"` for
`{"rsrp_dbm": -107.6}`, as fabricated purely because it wasn't a verbatim
JSON substring -- that was a bug in the *checker*, not the agent, fixed by
also matching on real field values/numbers with rounding tolerance, not just
raw-string containment; see LEARNING.md. The 75.6% above is post-fix.)

**Tool-use efficiency**: average 3.85 tool calls per investigation (median 3).
Investigations that stayed at or below the median tool-call count scored
**34.1% accuracy**; investigations that needed more tool calls than that
scored **14.3%**. This answers the spec's efficiency question directly: more
tool calls here does **not** mean the agent successfully dug deeper to crack
a hard case -- it means the case was hard, the agent kept poking at it, and
still got it wrong more often than not. The "decide to look further" behavior
is working exactly as designed (it does check more layers when the first one
is inconclusive -- see the example transcript below), it just isn't enough to
overcome this model's limits on the hardest fault types.

![Accuracy vs. investigation length](eval_results/tool_calls_vs_accuracy.png)

### How citation grounding is checked

`eval/verify_citations.py` re-queries the *same* `LogStore` the agent's tools
used for the exact `(layer, ts, session_id)` in each citation. A citation
passes if the quoted text is a verbatim substring of the real log line, OR
references the real `msg_type`, OR its content semantically matches the
message's actual field values (string field values, or numbers within
rounding tolerance) -- the agent is allowed to quote a value in prose
("-107.6 dBm") rather than exact JSON syntax. It fails if no message exists
at that layer/ts/session at all, or if one does but nothing in the quote
matches its real content. This means a "hallucination" here is the agent
misquoting its own tool output (or citing nothing), not a discrepancy between
the parser and some other source of truth.

## Responsible data handling

Simulated UEs carry a SUPI-like synthetic identifier (`imsi_sim_*`) that
**never touches disk or the LLM prompt**. Before any log line is written,
`synthesizer/anonymize.py` replaces it with `ue_<8 hex chars>` = the first 8
hex characters of an HMAC-SHA256 of the raw id, keyed with a key that exists
only in memory during generation. The same real UE always maps to the same
pseudonym (so cross-layer/cross-session correlation still works), but the
mapping is one-way and irreversible from the output alone.

**Not anonymized, deliberately**: `session_id` and `cell_id` are not treated as
sensitive (they don't identify a subscriber) and are left as-is for
readability. This is control-plane signaling only -- there's no IMEI,
location beyond coarse cell_id, or user-plane payload data anywhere in this
system, so subscriber identifiers are the only thing that needed this
treatment.

## Running it

```bash
# local, no docker
make venv
make data           # generates data/logs/*.jsonl + data/incidents.jsonl
make parser-build    # builds the pybind11 extension
make parser-test     # Catch2 tests under ASan+UBSan
make parser-bench    # throughput benchmark
make eval            # runs the full agent eval (needs `ollama pull qwen2.5:3b-instruct`)

# docker compose (ollama + agent, model auto-pulled on first run)
make data            # data/ is bind-mounted into the container, generate it first
make docker-eval
```

## Repo layout

```
DESIGN.md              shared contract: log schema, fault mechanisms, anonymization, bridge design
synthesizer/            5G RRC/NAS message builders, fault injectors, generator, anonymizer
parser/                 C++ streaming parser + pybind11 bridge + Catch2 tests + benchmark
rag/                    hand-authored failure-pattern corpus + retriever
agent/                  tool definitions + Ollama tool-calling investigator loop
eval/                   citation verifier, eval runner, report/chart generation
tests/                  Python unit tests (synthesizer, citation verifier)
docker-compose.yml, docker/  containerized bring-up
.github/workflows/ci.yml     C++ tests (ASan+UBSan) + Python tests on every push
LEARNING.md             protocol/systems notes written for the author, not the reader
```
