import json
import subprocess
import sys
from pathlib import Path

import pytest

from synthesizer.faults import FAULT_TYPES
from synthesizer.generator import generate


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("data")
    generate(seed=123, out_dir=out_dir)
    incidents = [json.loads(l) for l in (out_dir / "incidents.jsonl").open()]
    return out_dir, incidents


def test_incident_count_in_target_range(generated):
    _, incidents = generated
    assert 60 <= len(incidents) <= 100


def test_every_fault_type_represented(generated):
    _, incidents = generated
    seen = {i["fault_type"] for i in incidents}
    assert seen == set(FAULT_TYPES)


def test_no_raw_subscriber_id_leaks_into_logs(generated):
    out_dir, _ = generated
    for name in ["rrc.jsonl", "nas.jsonl", "phy.jsonl"]:
        text = (out_dir / "logs" / name).read_text()
        assert "imsi_sim_" not in text


def test_incidents_reference_real_sessions(generated):
    out_dir, incidents = generated
    rrc_sessions = set()
    for line in (out_dir / "logs" / "rrc.jsonl").open():
        rrc_sessions.add(json.loads(line)["session_id"])
    nas_sessions = set()
    for line in (out_dir / "logs" / "nas.jsonl").open():
        nas_sessions.add(json.loads(line)["session_id"])
    for inc in incidents:
        assert inc["session_id"] in rrc_sessions or inc["session_id"] in nas_sessions


def test_logs_are_globally_sorted_by_timestamp(generated):
    out_dir, _ = generated
    for name in ["rrc.jsonl", "nas.jsonl", "phy.jsonl"]:
        prev = float("-inf")
        for line in (out_dir / "logs" / name).open():
            ts = json.loads(line)["ts"]
            assert ts >= prev
            prev = ts


def test_no_internal_simulator_fields_leak_into_logs(generated):
    """Regression guard for a real bug: a fault builder once returned
    {"_sim_valid": False} as a message field, which went straight into the
    persisted log and handed the agent the ground-truth label for
    AUTH_BAD_KEY instead of making it infer the fault from AuthenticationReject.
    Any internal/simulator-only field must use a leading underscore and must
    never reach the serialized `fields` dict."""
    out_dir, _ = generated
    for name in ["rrc.jsonl", "nas.jsonl", "phy.jsonl"]:
        for line in (out_dir / "logs" / name).open():
            row = json.loads(line)
            for key in row["fields"]:
                assert not key.startswith("_"), f"leaked internal field {key!r} in {name}"
