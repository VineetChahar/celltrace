"""Unit tests for the absence / timing tools (agent v2): find_missing_response,
gap_vs_timer, last_event_before. Small hand-written fixture logs, each session
built to exercise one behavior, including the negative cases (answer present,
gap that does NOT match a timer, nothing before t, unknown timer...).

Also: every evidence object a tool returns must survive the real citation
verifier, and the v2 prompt / tool descriptions must not name any fault type.
"""
import json

import pytest

from agent.investigator import SYSTEM_PROMPT, SYSTEM_PROMPT_V2, _toolset_config
from agent.tools import ABSENCE_TOOL_SCHEMAS, TIMER_TABLE, InvestigationTools, build_log_store
from eval.verify_citations import verify_citation
from synthesizer.faults import FAULT_TYPES


def _line(ts, sess, layer, msg_type, fields=None, direction="N/A"):
    return {"ts": ts, "session_id": sess, "ue_pseudo": "ue_test", "layer": layer, "msg_type": msg_type,
            "direction": direction, "cell_id": "cell_00", "fields": fields or {}}


def _phy(ts, sess, rsrp=-90.0):
    return _line(ts, sess, "PHY", "measurement", {"rsrp_dbm": rsrp, "rsrq_db": -10.0, "sinr_db": 5.0})


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    d = tmp_path_factory.mktemp("absence_logs")
    rrc, nas, phy = [], [], []

    # s_unanswered: a page that is never answered; PHY keeps logging 3 s after it
    rrc.append(_line(100.0, "s_unanswered", "RRC", "Paging", {"pagingRecordList": [{"ue-Identity": "g1"}]}, "gNB->UE"))
    phy += [_phy(100.0 + 0.5 * i, "s_unanswered") for i in range(7)]  # 100.0 .. 103.0

    # s_answered: the same page, answered 0.4 s later
    rrc.append(_line(200.0, "s_answered", "RRC", "Paging", {"pagingRecordList": [{"ue-Identity": "g2"}]}, "gNB->UE"))
    rrc.append(_line(200.4, "s_answered", "RRC", "RRCSetupRequest", {"establishmentCause": "mt-Access"}, "UE->gNB"))
    phy += [_phy(200.0 + 0.5 * i, "s_answered") for i in range(7)]

    # s_retry: RegistrationRequest, no accept, retried 16.3 s later (T3510 is 15 s)
    nas.append(_line(10.0, "s_retry", "NAS", "RegistrationRequest", {"ngKSI": 1}, "UE->AMF"))
    nas.append(_line(10.1, "s_retry", "NAS", "AuthenticationRequest", {"ngKSI": 1}, "AMF->UE"))
    nas.append(_line(26.3, "s_retry", "NAS", "RegistrationRequest", {"ngKSI": 1}, "UE->AMF"))
    phy += [_phy(10.0 + i, "s_retry") for i in range(19)]  # 10 .. 28

    # s_ho: handover command with a logged t304 of 500 ms, re-establishment 0.5 s later
    rrc.append(_line(50.0, "s_ho", "RRC", "RRCReconfiguration",
                     {"mobilityControlInfo": {"targetCellId": "cell_01", "t304_ms": 500}}, "gNB->UE"))
    rrc.append(_line(50.5, "s_ho", "RRC", "RRCReestablishmentRequest", {"reestablishmentCause": "handoverFailure"}, "UE->gNB"))

    # s_fast: gap far below the timer (should NOT match expiry)
    nas.append(_line(300.0, "s_fast", "NAS", "RegistrationRequest", {"ngKSI": 2}, "UE->AMF"))
    nas.append(_line(302.0, "s_fast", "NAS", "RegistrationRequest", {"ngKSI": 2}, "UE->AMF"))

    # s_short_log: trigger right before the log ends -> window not fully logged
    rrc.append(_line(400.0, "s_short_log", "RRC", "RRCSetupRequest", {}, "UE->gNB"))
    phy.append(_phy(400.2, "s_short_log"))

    for name, rows in (("rrc", rrc), ("nas", nas), ("phy", phy)):
        rows.sort(key=lambda r: r["ts"])
        (d / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    return build_log_store(str(d / "rrc.jsonl"), str(d / "nas.jsonl"), str(d / "phy.jsonl"))


def tools_for(store, sess):
    return InvestigationTools(store, sess)


# --- find_missing_response ---------------------------------------------------

def test_missing_response_detected(store):
    r = tools_for(store, "s_unanswered").find_missing_response("Paging", "RRCSetupRequest", 2.0)
    assert r["n_trigger_occurrences"] == 1
    c = r["checks"][0]
    assert c["expected_found"] is False and r["any_missing"] is True
    assert c["trigger"]["line_id"] == "RRC@100.0"
    assert c["window_fully_logged"] is True  # PHY runs to 103.0, window ends 102.0


def test_present_response_is_not_reported_missing(store):
    r = tools_for(store, "s_answered").find_missing_response("Paging", "RRCSetupRequest", 2.0)
    c = r["checks"][0]
    assert c["expected_found"] is True and r["any_missing"] is False
    assert c["response"]["line_id"] == "RRC@200.4"
    assert c["response_delay_s"] == pytest.approx(0.4)


def test_response_outside_window_counts_as_missing(store):
    # answered at +0.4 s, but only a 0.2 s window is checked
    r = tools_for(store, "s_answered").find_missing_response("Paging", "RRCSetupRequest", 0.2)
    assert r["checks"][0]["expected_found"] is False


def test_no_trigger_in_session(store):
    r = tools_for(store, "s_answered").find_missing_response("RegistrationRequest", "RegistrationAccept", 15)
    assert r["n_trigger_occurrences"] == 0 and r["checks"] == [] and r["any_missing"] is False
    assert "no RegistrationRequest" in r["note"]


def test_every_trigger_occurrence_is_checked(store):
    r = tools_for(store, "s_retry").find_missing_response("RegistrationRequest", "RegistrationAccept", 15)
    assert r["n_trigger_occurrences"] == 2
    assert [c["expected_found"] for c in r["checks"]] == [False, False]


def test_window_not_fully_logged_is_flagged(store):
    r = tools_for(store, "s_short_log").find_missing_response("RRCSetupRequest", "RRCSetup", 1.0)
    c = r["checks"][0]
    assert c["expected_found"] is False
    assert c["window_fully_logged"] is False
    assert c["logged_after_trigger_s"] == pytest.approx(0.2)


def test_msg_type_match_is_case_insensitive_and_window_accepts_strings(store):
    r = tools_for(store, "s_answered").find_missing_response("paging", "rrcsetuprequest", "2")
    assert r["checks"][0]["expected_found"] is True


def test_tool_is_bound_to_its_session(store):
    # s_unanswered's tools must not see s_answered's response
    r = tools_for(store, "s_unanswered").find_missing_response("Paging", "RRCSetupRequest", 500)
    assert r["checks"][0]["expected_found"] is False


# --- gap_vs_timer --------------------------------------------------------------

def test_retry_gap_matches_t3510(store):
    r = tools_for(store, "s_retry").gap_vs_timer("RegistrationRequest", "RegistrationRequest", "T3510")
    p = r["pairs"][0]
    assert p["gap_s"] == pytest.approx(16.3)
    assert p["nominal_s"] == 15.0
    assert p["gap_matches_timer_expiry"] is True
    assert p["event_a"]["line_id"] == "NAS@10.0" and p["event_b"]["line_id"] == "NAS@26.3"


def test_short_gap_does_not_match_timer(store):
    r = tools_for(store, "s_fast").gap_vs_timer("RegistrationRequest", "RegistrationRequest", "T3510")
    p = r["pairs"][0]
    assert p["gap_s"] == pytest.approx(2.0)
    assert p["gap_matches_timer_expiry"] is False


def test_t304_uses_value_logged_in_the_reconfiguration(store):
    r = tools_for(store, "s_ho").gap_vs_timer("RRCReconfiguration", "RRCReestablishmentRequest", "t304")
    p = r["pairs"][0]
    assert p["nominal_s"] == 0.5  # from the logged t304_ms=500, not the 1 s table default
    assert "t304_ms logged" in p["nominal_source"]
    assert p["gap_matches_timer_expiry"] is True


def test_unanswered_event_reports_timer_expiry(store):
    r = tools_for(store, "s_unanswered").gap_vs_timer("Paging", "RRCSetupRequest", "T3513")
    p = r["pairs"][0]
    assert p["event_b"] is None and p["event_b_never_logged_after_event_a"] is True
    assert p["logged_after_event_a_s"] == pytest.approx(3.0)
    assert p["timer_would_have_expired_unanswered"] is True


def test_unanswered_but_log_too_short_does_not_claim_expiry(store):
    r = tools_for(store, "s_short_log").gap_vs_timer("RRCSetupRequest", "RRCSetup", "T300")
    p = r["pairs"][0]
    assert p["event_b"] is None
    assert p["timer_would_have_expired_unanswered"] is False  # only 0.2 s logged, T300 is 1 s


def test_unknown_timer_is_an_error_listing_known_timers(store):
    r = tools_for(store, "s_retry").gap_vs_timer("RegistrationRequest", "RegistrationRequest", "T9999")
    assert "error" in r and set(r["known_timers"]) == set(TIMER_TABLE)


def test_timer_name_is_case_insensitive(store):
    r = tools_for(store, "s_retry").gap_vs_timer("RegistrationRequest", "RegistrationRequest", "t3510")
    assert r["timer"] == "T3510"


def test_missing_event_a(store):
    r = tools_for(store, "s_retry").gap_vs_timer("Paging", "RRCSetupRequest", "T3513")
    assert r["pairs"] == [] and "no Paging" in r["note"]


# --- last_event_before -----------------------------------------------------------

def test_last_event_before_on_one_layer(store):
    r = tools_for(store, "s_retry").last_event_before(26.0, "NAS")
    assert r["event"]["line_id"] == "NAS@10.1"
    assert r["seconds_before_t"] == pytest.approx(15.9)


def test_last_event_before_is_strict(store):
    # an event exactly at t is not "before" t
    r = tools_for(store, "s_retry").last_event_before(26.3, "NAS")
    assert r["event"]["line_id"] == "NAS@10.1"


def test_last_event_before_any_layer_includes_phy(store):
    r = tools_for(store, "s_retry").last_event_before(26.3, "ANY")
    assert r["event"]["layer"] == "PHY" and r["event"]["ts"] == 26.0


def test_nothing_before_t(store):
    r = tools_for(store, "s_retry").last_event_before(5.0, "NAS")
    assert r["event"] is None and "no NAS event" in r["note"]


def test_empty_layer_for_session(store):
    r = tools_for(store, "s_ho").last_event_before(1000.0, "NAS")
    assert r["event"] is None


def test_bad_layer_is_an_error(store):
    assert "error" in tools_for(store, "s_retry").last_event_before(20.0, "MAC")


# --- evidence objects stay citable ------------------------------------------------

def _all_evidence(obj):
    if isinstance(obj, dict):
        if "line_id" in obj:
            yield obj
        for v in obj.values():
            yield from _all_evidence(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_evidence(v)


def test_every_evidence_object_passes_the_citation_verifier(store):
    outputs = [
        ("s_unanswered", tools_for(store, "s_unanswered").find_missing_response("Paging", "RRCSetupRequest", 2)),
        ("s_answered", tools_for(store, "s_answered").find_missing_response("Paging", "RRCSetupRequest", 2)),
        ("s_retry", tools_for(store, "s_retry").gap_vs_timer("RegistrationRequest", "RegistrationRequest", "T3510")),
        ("s_ho", tools_for(store, "s_ho").gap_vs_timer("RRCReconfiguration", "RRCReestablishmentRequest", "t304")),
        ("s_retry", tools_for(store, "s_retry").last_event_before(26.3, "ANY")),
    ]
    n = 0
    for sess, out in outputs:
        for ev in _all_evidence(out):
            layer, ts = ev["line_id"].split("@")
            assert layer == ev["layer"] and float(ts) == ev["ts"]
            v = verify_citation(store, sess, {"layer": ev["layer"], "ts": ev["ts"], "quoted_text": ev["msg_type"]})
            assert v["ok"], (sess, ev, v)
            n += 1
    assert n >= 8


# --- prompt / schema hygiene ----------------------------------------------------------

def test_v2_prompt_and_tool_descriptions_name_no_fault_type():
    text = (SYSTEM_PROMPT_V2 + json.dumps(ABSENCE_TOOL_SCHEMAS)).lower()
    for ft in FAULT_TYPES:
        assert ft.lower() not in text
        assert ft.lower().replace("_", " ") not in text


def test_v1_toolset_is_unchanged(store):
    prompt, schemas, fns = _toolset_config("v1", tools_for(store, "s_retry"))
    assert prompt == SYSTEM_PROMPT
    assert [s["function"]["name"] for s in schemas] == ["get_rrc_window", "get_nas_window", "get_phy_window", "rag_lookup"]
    assert set(fns) == {"get_rrc_window", "get_nas_window", "get_phy_window", "rag_lookup"}


def test_v2_toolset_adds_exactly_the_three_tools(store):
    _, schemas, fns = _toolset_config("v2", tools_for(store, "s_retry"))
    names = [s["function"]["name"] for s in schemas]
    assert names[-3:] == ["find_missing_response", "gap_vs_timer", "last_event_before"]
    assert set(names) == set(fns)
