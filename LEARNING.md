# LEARNING.md

Written for me, six months from now, to defend every part of this system without
re-reading the code first.

## What RRC and NAS actually are

**RRC (Radio Resource Control, 3GPP TS 38.331 for 5G NR)** is the layer that manages
the radio connection itself between a UE and a base station (gNB): setting up a
connection, reconfiguring it (including handovers), measuring signal quality, and
releasing it. It's a peer protocol between UE and gNB -- the core network isn't
directly involved in RRC messages, though the gNB relays NAS messages inside RRC
containers.

**NAS (Non-Access Stratum, 3GPP TS 24.501 for 5G)** is the layer above RRC that
runs between the UE and the core network's AMF (Access and Mobility Management
Function), tunneled transparently through the RRC/gNB layer. NAS handles
identity/registration, authentication, security context setup, and
session/service management. The gNB doesn't interpret NAS content -- it just
carries it.

The practical split I kept coming back to while writing the fault injectors:
**RRC failures are about the radio connection existing at all** (can the UE and
gNB talk, is the signal good enough, did a handover complete), while **NAS
failures are about what the UE is allowed to do once connected** (is it who it
says it is, does it have a valid security context, does the network still
remember it). A connection can have perfect RRC and still fail entirely at NAS
(`AUTH_BAD_KEY` is the clearest example: RRCSetupComplete happens fine, the
whole failure is a NAS authentication mismatch).

## What each fault type actually breaks

- **`AUTH_BAD_KEY`**: the UE's derived RES* doesn't match the network's XRES*
  during the 5G-AKA authentication challenge-response. This is what actually
  happens if a UE's stored subscriber key (K) is out of sync with the
  HSS/UDM's provisioned value -- the authentication vector challenge only the
  real key holder can answer correctly fails.
- **`REG_TIMEOUT_DROPPED_NAS`**: everything succeeds up through
  SecurityModeComplete, but the final RegistrationAccept never arrives. This
  models a plain delivery failure, not a protocol rejection -- the UE's T3510
  timer (registration procedure timer, ~15-16s in my synthesized timing) fires
  and it retries. The tell in the log is a real, large timing gap with nothing
  on NAS during it, vs. an explicit reject which shows up in milliseconds.
- **`RRC_CONN_FAIL_CONGESTION`**: admission control at the cell has no free
  signalling radio bearer to admit a new connection, so RRCSetupRequest gets
  an RRCReject instead of RRCSetup. This is the earliest possible failure
  point -- NAS never even starts.
- **`HO_MISSED_MEASUREMENT`**: an A3-event handover trigger (neighbor cell RSRP
  beats serving cell RSRP by some offset) should fire a MeasurementReport as
  RSRP declines, but the report never appears. No report means the network
  never knows it should hand the UE off, so no RRCReconfiguration is ever
  sent. This is the fault type with no error message anywhere -- the evidence
  is a *gap* (PHY shows the decline, RRC shows nothing responding to it),
  which is exactly why the RAG corpus entry for it is titled "the absence of a
  MeasurementReport is itself the evidence."
- **`HANDOVER_RECONFIG_TIMEOUT`**: unlike the above, the MeasurementReport
  *did* arrive and the network *did* send RRCReconfiguration with
  mobilityControlInfo (a real handover command to a target cell) -- but the
  UE never confirms with RRCReconfigurationComplete. In real networks this is
  the t304 timer (~1s) expiring because the target cell couldn't be reached
  post-handover. The UE's fallback is RRCReestablishmentRequest, which fails
  too because the serving cell has already handed off its context.
- **`PHY_SIGNAL_RLF`**: no handover was even attempted -- RSRP/SINR just decay
  past out-of-sync thresholds with nothing in RRC responding. This is a
  "no safety net" failure: real radio link failure from coverage, not a
  procedural miss.
- **`SECURITY_MODE_FAILURE`**: happens *after* a fully successful RRC handover.
  The post-handover SecurityModeCommand carries a key context (ngKSI) the UE's
  integrity check rejects -- I modeled this as a stale/mismatched key
  (`ng_ksi ^ 1` in the generator) specifically to make the point that RRC can
  be perfectly clean while NAS fails for an unrelated reason right after.
- **`SERVICE_REQUEST_NO_CONTEXT`**: the AMF has a context-expiry timer; if a UE
  is idle long enough, the AMF drops its stored context. When the UE later
  wakes up and sends a ServiceRequest, the AMF has nothing to match it against
  and rejects with cause `no-context`. The diagnostic move here is to look
  *backward* in time for the idle gap, not forward from the reject.
- **`PAGING_TIMEOUT`**: the network's attempt to reach an idle UE (Paging) gets
  no response within the paging timer. Like `HO_MISSED_MEASUREMENT`, this is
  an absence-based fault -- there's no NAS or further RRC activity to point
  at, just a lone Paging message and silence.
- **`DEREGISTRATION_IMPLICIT`**: the mirror image of paging timeout -- instead
  of the network failing to reach the UE, the UE fails to check in
  (misses its periodic registration update). After the mobile-reachable timer
  expires (~50-60s in my synthesis), the AMF silently drops the UE's context.
  This isn't an over-the-air message at all; I modeled it as an AMF-internal
  log event, because that's genuinely how real cores would surface it --
  there's no point notifying a UE the network already believes is
  unreachable.

## The ring-buffer / pybind11 bridge design, and why

**Why a ring buffer instead of just reading the file directly into the parser
in one thread**: the assignment specifically asked for a component that can
"keep up with logs arriving faster than they're consumed" -- i.e. decouple
producer and consumer so a burst doesn't stall either side. A single-threaded
read-then-parse loop can't demonstrate that; splitting into a producer thread
(reads lines) and consumer thread (parses them) with a bounded buffer between
is the minimal real version of that architecture.

**Why lock-free SPSC instead of a mutex+condvar queue**: with exactly one
producer and one consumer per file, a lock-free ring buffer needs no
synchronization primitive beyond two atomics (`head_`, `tail_`) -- the producer
only ever writes `tail_`, the consumer only ever writes `head_`, and each side
only *reads* the other's variable. That's the textbook SPSC case where lock-free
is not over-engineering; it's the simplest correct design available, not a
premature optimization. A mutex would be simpler to reason about but pays a
kernel-arbitration cost on every push/pop for no benefit at this contention
pattern (one writer, one reader, no third party). The cache-line padding
(`alignas(64)` on `head_` and `tail_` separately) exists because without it,
`head_` and `tail_` would likely share one cache line -- and then every
producer write to `tail_` would invalidate the consumer's cached copy of that
line (and vice versa) even though they're logically independent counters. This
is *false sharing*, and it's a real, measurable effect, not just theory: two
unrelated atomics thrashing the same cache line between cores serializes what
should be two independent operations.

**Why the JSON parser is hand-written instead of a dependency**: the whole
point of the C++ layer in this project is to demonstrate real parsing work.
Pulling in nlohmann/json or RapidJSON would produce working code but nothing
to defend in an interview beyond "I called a library." The log format's JSON
shape is bounded (flat envelope + one nested `fields` object, at most one
level of array-of-objects inside it), so a ~200 line recursive-descent parser
covers it completely without needing general robustness a production JSON
library would.

**Why `json::Value` uses `unique_ptr<Object>`/`unique_ptr<Array>` instead of
storing `vector<Value>` directly inside the variant**: `Value` is recursive --
an Object contains `pair<string, Value>`s, and a Value can *be* an Object.
`std::variant<..., vector<Value>>` as a *direct* member of `Value` is
attempting to store `vector<Value>` while `Value` is still an incomplete type
(mid-definition). C++17 relaxed `vector` to formally allow incomplete types in
some contexts, but relying on that inside a `variant`'s alternative list is
exactly the kind of "works on my compiler today" fragility not worth the risk
here. `unique_ptr<Object>` sidesteps the whole question: a pointer's size and
layout never depend on what it points to, so it's always a complete type
regardless of `Value`'s completeness. The cost is that `Value` is move-only
(deleted copy constructor) -- which is fine, because every place `Value` is
constructed (`json::parse`, `ParsedMessage.fields`) is a "build once, then
move to its final owner" pattern anyway, so move-only isn't a real constraint,
it's the correct default here (also RAII: no manual delete anywhere, the
unique_ptr destructors handle recursive teardown of the whole tree
automatically when a `ParsedMessage` goes out of scope).

**Why pybind11 in-process instead of a socket/shared-memory protocol**: the
spec allowed either. A socket protocol is real systems work too, but it adds a
serialization format and a second process to manage for no benefit here --
the agent and the parser are always co-located (there's no reason to run them
on different machines), so an in-process native extension gets the same "real
bridge, not a shelled-out script" property with less incidental complexity.
The actual non-negotiable was: Python must never re-parse JSON that C++
already parsed. `bindings.cpp`'s `to_python()` walks the already-built
`json::Value` tree and constructs native Python dicts/lists directly -- there
is no second JSON.parse anywhere in the path from log line to agent tool
result.

## A real bug I hit (and how I found it)

While writing `test_json.cpp`, I wrote `REQUIRE_THROWS_AS(parse("1.2.3"),
ParseError)`, fully expecting that to obviously throw. It didn't -- the test
failed with "no exception was thrown." What was actually happening: my
`parse_number()` correctly parses `1.2` as a complete, valid number and stops
there (a JSON number grammar has no way to have two decimal points), and my
top-level `parse()` function was only parsing *one value* from the front of
the string and silently ignoring anything left over -- so `"1.2.3"` parsed as
`1.2` with `.3` just... dropped. That's technically defensible for a generic
JSON-value parser (lots of parsers work that way, e.g. `strtod` itself
ignores trailing garbage), but for *this* project it's wrong: every use of
`json::parse()` here is parsing one complete log line, and a line with
trailing garbage after a syntactically valid prefix is exactly the kind of
"malformed" input the unit tests (and real truncated/corrupted logs) are
supposed to catch. I fixed it by adding a `check_fully_consumed()` step after
parsing the top-level value that throws if any non-whitespace remains, and
updated the docstring to state that trailing content is rejected, not
silently ignored -- silent partial-success is a worse failure mode for a log
parser than a loud error, because it would have let real corruption through
undetected in the ring-buffer pipeline.

A related, separate issue turned up during eval, not unit testing: my first
version of the agent loop, when it ran out of its tool-call step budget
without submitting a final answer, just gave up and reported `OTHER`. Watching
early full traces, I found cases where the model had *already correctly
identified the root cause in its reasoning text* by step 2 or 3, but then kept
re-querying already-seen data instead of calling `submit_root_cause`, burned
its whole step budget, and got scored as a failure despite having the right
answer internally. I fixed this with a forced final call: when the step budget
runs out, one last `ollama.chat` call is made with the tool list restricted to
*only* `submit_root_cause`, explicitly telling the model its time is up --
which reliably (though not universally -- see the honest timeout rate in the
README) converts "technically correct but never committed" into an actual
scored answer, the same way a real on-call engineer has to write down their
best guess when told to stop investigating and report.

## A second real bug: the citation verifier was too strict

After the first full eval run, the hallucination rate came back at 77.9% --
suspiciously high even for a small local model. Looking at individual
flagged citations, I found cases like: the agent quoted `"-107.6 dBm"` for a
PHY sample whose real `rsrp_dbm` field was exactly `-107.6`. My verifier's
only content check was "is `quoted_text` a verbatim substring of the raw JSON
line" -- and `"-107.6 dbm"` is *not* a substring of
`..."rsrp_dbm": -107.6, "rsrq_db"...` because the agent wrote it in readable
prose, not JSON syntax. That's not a hallucination, it's a completely correct
citation being punished for not matching my formatting assumption. I fixed
this by making the verifier check semantic content instead of exact string
containment: it now also extracts numeric literals from the quoted text and
compares them (with rounding tolerance) against the message's actual field
values, and checks whether any string field value appears in the quote. This
dropped the rate to 75.6% -- a real but small correction, which told me
something useful: most of the *actual* hallucination rate wasn't a checker
artifact, it was mainly investigations where the agent submitted zero
citations alongside a guess (49 of 65 flagged cases) plus a handful of
genuinely invented evidence (timestamps that look like Unix epoch values, or
off by a fraction of a second from any real message). The lesson: when a
harness produces a suspiciously extreme number, check whether the harness
itself is the thing that's wrong before writing the number down as a finding
about the system it's measuring.

## Diagnose-then-fix pass on the eval numbers

After the first full 86-incident eval (24.4% accuracy, 75.6% citation
hallucination rate), the instruction was explicit: read the failures before
changing anything, because "the agent reasons fine but its answer doesn't
land in a recognizable format" and "the agent genuinely can't do multi-hop
synthesis" need completely different fixes, and guessing wrong wastes time.

**Part 1 -- reading the failures (no code changes, ~40 min).** Of the 65
investigations the harness flagged as ungrounded, I read the raw transcripts
and classified every one:

| category | count | % of 65 |
|---|---:|---:|
| Fabricated/misquoted (real tool call made, citation genuinely doesn't match) | 14 | 22% |
| Answered but never invoked (full correct-looking answer written as prose, `submit_root_cause` never actually called) | 27 | 42% |
| Incomplete (still mid-investigation when the step budget ran out, no conclusion attempted) | 21 | 32% |
| True abstention (explicitly said "insufficient evidence") | 1 | 2% |
| Wrong-layer-label on otherwise-real content (a genuine one-off, not counted above) | 1 | — |
| Absence-of-evidence description forced into the citation schema (a genuine one-off) | 1 | — |

The two mechanical/format categories (42% + 32% = 74%) dwarfed genuine
fabrication (22%). Concrete example of "answered but never invoked"
(`INC_0021`, verbatim from its transcript's last reasoning turn): the model
had already written out a complete, correct JSON answer --
`"fault_type": "RRC_CONN_FAIL_CONGESTION"` with two real citations -- as
prose text, then ended with *"Call the `submit_root_cause` function with the
provided details"* instead of an actual tool invocation. It had the right
answer. It just never pressed the button.

I also spot-checked 6 incidents from the three 0%-accuracy fault types
(`HANDOVER_RECONFIG_TIMEOUT`, `HO_MISSED_MEASUREMENT`, `PAGING_TIMEOUT`) for
whether the agent even reached the layer where the real evidence lives.
It did, in every one -- e.g. one `HANDOVER_RECONFIG_TIMEOUT` case checked
RRC, then NAS, then RRC again, then PHY (all three layers) before still
failing to conclude. So "stops before checking" wasn't the problem either;
the same mechanical/format gap was.

One interesting fabrication sub-pattern from the 14 genuine cases: several
invented timestamps looked like real Unix epoch values
(`1583930304.021`, `1650034389.568`, `1677538912.345` -- all plausible
2020s calendar dates) instead of this project's actual simulation-clock
seconds (small numbers like `2461.72`). The model reached for a
plausible-*looking* timestamp from its training distribution rather than one
it had actually seen in a tool result -- a different and more concerning
failure than a rounding error, and worth knowing the difference between the
two when reading any hallucination-rate number.

**Part 2 -- fixing the format/completion gap.** Since the failures were
mostly categories 2+3+4 above (format/completion, not synthesis), I rebuilt
how the final answer is produced. `submit_root_cause` is no longer a native
tool call the model has to remember to invoke correctly under pressure;
evidence-gathering still uses native tool-calling (that part worked fine --
the model reliably called `get_rrc_window` etc.), but the final answer now
comes from one dedicated call using Ollama's JSON-schema-constrained
structured output (`ollama.chat(..., format=<schema>)`), which reliably
returns parseable JSON instead of hoping a stressed, budget-limited model
correctly emits a well-formed function call. The schema also adds an
explicit `insufficient_evidence` boolean, separate from `fault_type`, so a
genuine "I don't know" is now a valid, honest, machine-checkable answer
instead of indistinguishable from a wrong guess.

On a 4-incident spot check before committing to a full re-run, this
immediately flipped two previously-broken cases to correct, including one
from the 0%-accuracy `HANDOVER_RECONFIG_TIMEOUT` category -- and every
citation it did produce verified clean (no fabrication) in that sample.

**Before/after, full 86-incident re-run:**

| metric | before | after |
|---|---:|---:|
| Overall accuracy | 24.4% | 25.6% |
| Citation hallucination rate | 75.6% | 58.1% |
| Avg tool calls / investigation | 3.85 | 1.83 |

Full per-fault-type breakdown is in the README. The honest summary: this
fixed exactly the thing it targeted (completion reliability -> grounding),
and I was wrong to assume that would also lift accuracy much, because I
hadn't accounted for a side effect of the fix's mechanism. The old loop kept
nudging the model to make another tool call whenever it stopped ("continue
the investigation or call submit_root_cause"), which -- inadvertently --
forced more investigation depth as a side effect of chasing a tool call that
often never landed anyway. The new loop finalizes the moment the model stops
issuing tool calls, because that pause is now a *reliable* signal ("ready to
answer") instead of a signal that usually led nowhere. Reliable is good, but
it also means the agent now finalizes on however much evidence it happened
to gather in however many calls it felt like making, which is less for
fault types that only look wrong after several patient checks. Average tool
calls per investigation fell from 3.85 to 1.83 as a direct result --
`AUTH_BAD_KEY` (one NAS exchange, quick to resolve) went from 33.3% to
100.0%, while `SERVICE_REQUEST_NO_CONTEXT` and `REG_TIMEOUT_DROPPED_NAS`
(both need noticing something *after* a longer gap) lost ground. If I had
more time, the next experiment I'd run isn't a different model, it's
requiring at least 2 evidence-gathering calls (not 1) before allowing early
finalization, to get some of that lost depth back without reintroducing the
unreliable nudge loop.

**Part 3 (larger local model on the cross-layer gap):**

Same agent code, same prompts, only the model swapped
(`qwen2.5:7b-instruct` in place of `qwen2.5:3b-instruct`), run against the
27 incidents from the three fault types still at 0% after Part 2
(`HO_MISSED_MEASUREMENT`, `PAGING_TIMEOUT`, `REG_TIMEOUT_DROPPED_NAS`):
**0/27 correct on the 7B model, identical to the 3B model's 0/27 on the same
subset.** Citation hallucination was also identical: 23/27 (85.2%) for
*both* models -- not just a similar rate, the same count. The 7B model made
71% more tool calls on average (3.93 vs 2.30), so it wasn't giving up early
or being lazier about investigating; it just couldn't turn that extra
investigation into a correct answer.

This is the useful negative result the instructions asked for if it came out
this way: model size was not the bottleneck for this task. All three of
these fault types share a specific structural property -- the diagnostic
signal is the *absence* of a message that should have appeared (no
MeasurementReport, no RRCSetupRequest response to a Paging, no second
RegistrationAccept), not a message that did appear and needs interpreting.
My tools return "what happened in this window," and the model has to notice,
unprompted, that something specific *didn't* happen among a list of things
that did -- and apparently that's genuinely hard for a small-to-mid local
model regardless of size, at least with this tool design. If I revisited
this, the experiment I'd try next isn't a bigger model again, it's a
different tool: something like `check_message_absence(msg_type, t_start,
t_end)` that directly answers the yes/no question instead of making the
model infer it from a returned list -- turning "notice an absence" into
"read a boolean," which is a much easier thing to ask any size of model to
do.

## C++ mechanics used and why

- **Move semantics** (`ParsedMessage`, `json::Value`): both are move-only by
  design (deleted copy ctor/assignment) because the value being moved
  (recursively-owned JSON trees, or a struct containing one) is expensive to
  deep-copy and there's never a legitimate reason to copy a parsed log message
  in this codebase -- it's built once by the parser and then either queried by
  reference (`LogStore::query` returns `const ParsedMessage*`) or handed off
  to its owning container. Deleting copy operations turns "accidentally
  copied something expensive" from a silent performance bug into a compile
  error.
- **RAII**: no `new`/`delete` anywhere in this codebase. `unique_ptr` owns the
  recursive JSON structure, `std::vector` owns the per-layer message stores,
  `std::thread` + explicit `.join()` (never detach) own the producer/consumer
  lifetimes.
- **Atomics + memory ordering** (`ring_buffer.hpp`): `try_push` writes `tail_`
  with `memory_order_release` after writing the slot, and reads `head_` with
  `memory_order_acquire`; `try_pop` is the mirror image. This acquire/release
  pairing is what actually makes the ring buffer correct, not just "using
  atomics" -- it guarantees that when the consumer's acquire-load of `tail_`
  observes the producer's release-store, every write the producer did *before*
  that store (i.e., writing the actual item into the slot) is visible to the
  consumer too. Without that ordering (e.g. using `memory_order_relaxed`
  everywhere) the compiler/CPU would be free to reorder the slot write after
  the index update, and the consumer could read a half-written or stale slot.
- **`std::string_view`** for parser input: the JSON parser takes
  `std::string_view` rather than `const std::string&` so it can be called
  directly on a line already owned by a `std::string` in the ring buffer
  without an extra copy, and so it can equally be called on a `const char*`
  literal in tests without constructing a temporary `std::string` first.
- **Templates** (`SpscRingBuffer<T>`): the same ring buffer implementation is
  used for `std::string` (raw log lines, in production ingestion) and `int`
  (in the concurrency stress test) with zero code duplication, and the
  capacity-rounding/masking logic is verified once for both usages.
