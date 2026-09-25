import json

import pytest

from agent.tools import build_log_store
from eval.verify_citations import verify_citation, verify_investigation


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    d = tmp_path_factory.mktemp("logs")
    rrc = d / "rrc.jsonl"
    nas = d / "nas.jsonl"
    phy = d / "phy.jsonl"
    rrc.write_text(
        json.dumps({
            "ts": 10.0, "session_id": "sX", "ue_pseudo": "ue_1", "layer": "RRC",
            "msg_type": "RRCReject", "direction": "gNB->UE", "cell_id": "cell_00",
            "fields": {"cause": "congestion"},
        }) + "\n"
    )
    nas.write_text("")
    phy.write_text("")
    return build_log_store(str(rrc), str(nas), str(phy))


def test_verifies_real_citation(store):
    result = verify_citation(store, "sX", {"layer": "RRC", "ts": 10.0, "quoted_text": "RRCReject"})
    assert result["ok"]


def test_rejects_citation_at_nonexistent_timestamp(store):
    result = verify_citation(store, "sX", {"layer": "RRC", "ts": 999.0, "quoted_text": "RRCReject"})
    assert not result["ok"]


def test_rejects_citation_with_wrong_session(store):
    result = verify_citation(store, "sOther", {"layer": "RRC", "ts": 10.0, "quoted_text": "RRCReject"})
    assert not result["ok"]


def test_rejects_citation_whose_quoted_text_doesnt_match(store):
    result = verify_citation(store, "sX", {"layer": "RRC", "ts": 10.0, "quoted_text": "AuthenticationReject"})
    assert not result["ok"]


def test_verify_investigation_flags_any_bad_citation(store):
    good = {"layer": "RRC", "ts": 10.0, "quoted_text": "congestion"}
    bad = {"layer": "RRC", "ts": 10.0, "quoted_text": "something that was never there"}
    result = verify_investigation(store, "sX", [good, bad])
    assert result["any_fabricated"]


def test_verify_investigation_passes_when_all_citations_good(store):
    good = {"layer": "RRC", "ts": 10.0, "quoted_text": "congestion"}
    result = verify_investigation(store, "sX", [good])
    assert not result["any_fabricated"]


def test_no_citations_counts_as_fabricated(store):
    result = verify_investigation(store, "sX", [])
    assert result["any_fabricated"]
