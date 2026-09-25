"""Builds the full synthetic multi-UE log stream + ground-truth incident set.

Run: python -m synthesizer.generator [--seed N] [--out-dir data]
"""
import argparse
import json
import random
from pathlib import Path

from synthesizer import faults
from synthesizer.anonymize import pseudonymize

N_UES = 25
N_CELLS = 8
INSTANCES_PER_FAULT = (6, 10)  # inclusive random range
N_NORMAL_SESSIONS = 30
TIME_HORIZON_S = 20000.0


def _cell_ids():
    return [f"cell_{i:02d}" for i in range(N_CELLS)]


def _make_session(raw_ue_id: str, session_idx: int, cell_id: str, neigh_cell_id: str, fault_type: str | None):
    rnti = random.randint(1, 2**20)
    ng_ksi = random.randint(0, 6)
    guti = f"5gguti-{random.randint(10**8, 10**9-1)}"
    tai = f"tai-{cell_id}"
    if fault_type is None:
        plan = faults.build_normal_session(rnti, ng_ksi, guti, tai, cell_id, neigh_cell_id)
    else:
        plan = faults.BUILDERS[fault_type](rnti, ng_ksi, guti, tai, cell_id, neigh_cell_id)
    return plan


def generate(seed: int, out_dir: Path):
    random.seed(seed)
    ue_pool = [f"imsi_sim_{i:06d}" for i in range(N_UES)]
    cells = _cell_ids()

    rrc_lines, nas_lines, phy_lines, incidents = [], [], [], []
    session_counter = 0
    incident_counter = 0

    def emit_session(fault_type):
        nonlocal session_counter, incident_counter
        session_counter += 1
        session_id = f"sess_{session_counter:05d}"
        raw_ue = random.choice(ue_pool)
        ue_pseudo = pseudonymize(raw_ue)
        cell_id = random.choice(cells)
        neigh_cell_id = random.choice([c for c in cells if c != cell_id])
        plan = _make_session(raw_ue, session_counter, cell_id, neigh_cell_id, fault_type)
        t0 = random.uniform(0, TIME_HORIZON_S)

        for e in plan.events:
            ts = round(t0 + e.t_rel, 3)
            line = {
                "ts": ts, "session_id": session_id, "ue_pseudo": ue_pseudo,
                "layer": e.layer, "msg_type": e.msg_type, "direction": e.direction,
                "cell_id": cell_id, "fields": e.fields,
            }
            (rrc_lines if e.layer == "RRC" else nas_lines).append(line)

        for p in plan.phy:
            ts = round(t0 + p.t_rel, 3)
            phy_lines.append({
                "ts": ts, "session_id": session_id, "ue_pseudo": ue_pseudo,
                "layer": "PHY", "msg_type": "measurement", "direction": "N/A",
                "cell_id": cell_id,
                "fields": {"rsrp_dbm": p.rsrp_dbm, "rsrq_db": p.rsrq_db, "sinr_db": p.sinr_db},
            })

        if fault_type is not None:
            incident_counter += 1
            incidents.append({
                "incident_id": f"INC_{incident_counter:04d}",
                "fault_type": fault_type,
                "ue_pseudo": ue_pseudo,
                "session_id": session_id,
                "reported_ts": round(t0 + plan.reported_offset_s, 3),
                "symptom": plan.symptom,
                "window_start": round(max(0.0, t0 - 10.0), 3),
                "window_end": round(t0 + plan.duration_s + 5.0, 3),
            })

    for ft in faults.FAULT_TYPES:
        n = random.randint(*INSTANCES_PER_FAULT)
        for _ in range(n):
            emit_session(ft)

    for _ in range(N_NORMAL_SESSIONS):
        emit_session(None)

    rrc_lines.sort(key=lambda r: r["ts"])
    nas_lines.sort(key=lambda r: r["ts"])
    phy_lines.sort(key=lambda r: r["ts"])
    incidents.sort(key=lambda r: r["reported_ts"])

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "logs").mkdir(exist_ok=True)
    for name, rows in [("rrc.jsonl", rrc_lines), ("nas.jsonl", nas_lines), ("phy.jsonl", phy_lines)]:
        with open(out_dir / "logs" / name, "w") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    with open(out_dir / "incidents.jsonl", "w") as f:
        for r in incidents:
            f.write(json.dumps(r) + "\n")

    print(f"sessions: {session_counter} ({len(incidents)} faulty, {N_NORMAL_SESSIONS} clean)")
    print(f"rrc: {len(rrc_lines)} lines, nas: {len(nas_lines)} lines, phy: {len(phy_lines)} lines")
    by_type = {}
    for inc in incidents:
        by_type[inc["fault_type"]] = by_type.get(inc["fault_type"], 0) + 1
    for ft, n in sorted(by_type.items()):
        print(f"  {ft}: {n}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out-dir", type=Path, default=Path("data"))
    args = ap.parse_args()
    generate(args.seed, args.out_dir)
