"""Per-fault-type session builders.

Each `build_<FAULT_TYPE>` function returns a self-contained list of `Event`s for
one incident (RRC + NAS interleaved) plus a PHY sample plan, and a `symptom`
one-liner as a caller/NOC operator would phrase it. `build_normal_session` is
the fault-free baseline used to pad the log stream with realistic context.

Timestamps are relative to the session's t0 and get shifted to the global
timeline by the caller (generator.py).
"""
import random
from dataclasses import dataclass, field

from synthesizer import messages_5g as m

FAULT_TYPES = [
    "AUTH_BAD_KEY",
    "REG_TIMEOUT_DROPPED_NAS",
    "RRC_CONN_FAIL_CONGESTION",
    "HO_MISSED_MEASUREMENT",
    "HANDOVER_RECONFIG_TIMEOUT",
    "PHY_SIGNAL_RLF",
    "SECURITY_MODE_FAILURE",
    "SERVICE_REQUEST_NO_CONTEXT",
    "PAGING_TIMEOUT",
    "DEREGISTRATION_IMPLICIT",
]


@dataclass
class Event:
    t_rel: float
    layer: str
    msg_type: str
    direction: str
    fields: dict


@dataclass
class PhySample:
    t_rel: float
    rsrp_dbm: float
    rsrq_db: float
    sinr_db: float


@dataclass
class SessionPlan:
    events: list[Event]
    phy: list[PhySample]
    duration_s: float
    symptom: str | None = None
    reported_offset_s: float | None = None  # t_rel of the moment the symptom would be reported
    fault_type: str | None = None


def _jitter(base: float, spread: float) -> float:
    return base + random.uniform(-spread, spread)


def _phy_baseline(duration_s: float, rsrp_center: float = -85.0, step: float = 0.2) -> list[PhySample]:
    samples = []
    t = 0.0
    rsrp = rsrp_center
    while t < duration_s:
        rsrp = max(-120.0, min(-60.0, rsrp + random.uniform(-1.0, 1.0)))
        rsrq = rsrp / 10.0 - random.uniform(0, 2)
        sinr = (rsrp + 100) / 3.0 + random.uniform(-1.5, 1.5)
        samples.append(PhySample(round(t, 3), round(rsrp, 1), round(rsrq, 1), round(sinr, 1)))
        t += step
    return samples


def _phy_decaying(duration_s: float, start_rsrp: float = -85.0, end_rsrp: float = -118.0, step: float = 0.2) -> list[PhySample]:
    samples = []
    t = 0.0
    n_steps = max(1, int(duration_s / step))
    for i in range(n_steps):
        frac = i / n_steps
        rsrp = start_rsrp + (end_rsrp - start_rsrp) * frac + random.uniform(-0.8, 0.8)
        rsrq = rsrp / 10.0 - random.uniform(0, 2)
        sinr = (rsrp + 100) / 3.0 + random.uniform(-1.0, 1.0)
        samples.append(PhySample(round(t, 3), round(rsrp, 1), round(rsrq, 1), round(sinr, 1)))
        t += step
    return samples


def _attach(ev: list[Event], t: float, rnti: int, ng_ksi: int, guti: str, tai: str) -> float:
    trans = 1
    ev.append(Event(t, "RRC", "RRCSetupRequest", "UE->gNB", m.rrc_setup_request(rnti))); t += _jitter(0.03, 0.01)
    ev.append(Event(t, "RRC", "RRCSetup", "gNB->UE", m.rrc_setup(trans))); t += _jitter(0.04, 0.01)
    ev.append(Event(t, "RRC", "RRCSetupComplete", "UE->gNB", m.rrc_setup_complete(trans))); t += _jitter(0.05, 0.02)
    ev.append(Event(t, "NAS", "RegistrationRequest", "UE->AMF", m.registration_request(ng_ksi))); t += _jitter(0.08, 0.02)
    ev.append(Event(t, "NAS", "AuthenticationRequest", "AMF->UE", m.authentication_request(ng_ksi))); t += _jitter(0.06, 0.02)
    ev.append(Event(t, "NAS", "AuthenticationResponse", "UE->AMF", m.authentication_response(True))); t += _jitter(0.07, 0.02)
    ev.append(Event(t, "NAS", "SecurityModeCommand", "AMF->UE", m.security_mode_command(ng_ksi))); t += _jitter(0.05, 0.01)
    ev.append(Event(t, "NAS", "SecurityModeComplete", "UE->AMF", m.security_mode_complete())); t += _jitter(0.06, 0.02)
    ev.append(Event(t, "NAS", "RegistrationAccept", "AMF->UE", m.registration_accept(guti, [tai]))); t += _jitter(0.05, 0.02)
    ev.append(Event(t, "NAS", "RegistrationComplete", "UE->AMF", m.registration_complete())); t += _jitter(0.1, 0.03)
    return t


def build_normal_session(rnti, ng_ksi, guti, tai, cell_id, neigh_cell_id) -> SessionPlan:
    ev: list[Event] = []
    t = 0.0
    t = _attach(ev, t, rnti, ng_ksi, guti, tai)
    duration = _jitter(45.0, 10.0)
    # a routine handover partway through
    ho_t = duration * random.uniform(0.3, 0.6)
    ev.append(Event(ho_t, "RRC", "MeasurementReport", "UE->gNB", m.measurement_report(-92, -12, neigh_cell_id, -84)))
    ev.append(Event(ho_t + 0.02, "RRC", "RRCReconfiguration", "gNB->UE",
                     m.rrc_reconfiguration(2, {"targetCellId": neigh_cell_id, "newUE-Identity": f"{rnti+1:010x}", "t304_ms": 1000})))
    ev.append(Event(ho_t + 0.06, "RRC", "RRCReconfigurationComplete", "UE->gNB", m.rrc_reconfiguration_complete(2)))
    # a routine service request near the end
    svc_t = duration * random.uniform(0.7, 0.85)
    ev.append(Event(svc_t, "NAS", "ServiceRequest", "UE->AMF", m.service_request(ng_ksi)))
    ev.append(Event(svc_t + 0.03, "NAS", "ServiceAccept" if False else "RegistrationComplete", "AMF->UE", {}))  # accept modeled loosely
    ev.append(Event(duration, "RRC", "RRCRelease", "gNB->UE", m.rrc_release("normal")))
    phy = _phy_baseline(duration)
    ev.sort(key=lambda e: e.t_rel)
    return SessionPlan(ev, phy, duration, symptom=None, reported_offset_s=None, fault_type=None)


def build_AUTH_BAD_KEY(rnti, ng_ksi, guti, tai, cell_id, neigh_cell_id) -> SessionPlan:
    ev: list[Event] = []
    t = 0.0
    trans = 1
    ev.append(Event(t, "RRC", "RRCSetupRequest", "UE->gNB", m.rrc_setup_request(rnti))); t += _jitter(0.03, 0.01)
    ev.append(Event(t, "RRC", "RRCSetup", "gNB->UE", m.rrc_setup(trans))); t += _jitter(0.04, 0.01)
    ev.append(Event(t, "RRC", "RRCSetupComplete", "UE->gNB", m.rrc_setup_complete(trans))); t += _jitter(0.05, 0.02)
    ev.append(Event(t, "NAS", "RegistrationRequest", "UE->AMF", m.registration_request(ng_ksi))); t += _jitter(0.08, 0.02)
    ev.append(Event(t, "NAS", "AuthenticationRequest", "AMF->UE", m.authentication_request(ng_ksi))); t += _jitter(0.06, 0.02)
    ev.append(Event(t, "NAS", "AuthenticationResponse", "UE->AMF", m.authentication_response(False))); t += _jitter(0.05, 0.02)
    ev.append(Event(t, "NAS", "AuthenticationReject", "AMF->UE", m.authentication_reject("MAC-failure"))); t += _jitter(0.03, 0.01)
    report_t = t
    ev.append(Event(t, "RRC", "RRCRelease", "gNB->UE", m.rrc_release("nas-failure"))); t += 0.02
    duration = t + 1.0
    phy = _phy_baseline(duration)
    return SessionPlan(ev, phy, duration, "authentication failed, connection dropped", report_t, "AUTH_BAD_KEY")


def build_REG_TIMEOUT_DROPPED_NAS(rnti, ng_ksi, guti, tai, cell_id, neigh_cell_id) -> SessionPlan:
    ev: list[Event] = []
    t = 0.0
    trans = 1
    ev.append(Event(t, "RRC", "RRCSetupRequest", "UE->gNB", m.rrc_setup_request(rnti))); t += _jitter(0.03, 0.01)
    ev.append(Event(t, "RRC", "RRCSetup", "gNB->UE", m.rrc_setup(trans))); t += _jitter(0.04, 0.01)
    ev.append(Event(t, "RRC", "RRCSetupComplete", "UE->gNB", m.rrc_setup_complete(trans))); t += _jitter(0.05, 0.02)
    ev.append(Event(t, "NAS", "RegistrationRequest", "UE->AMF", m.registration_request(ng_ksi))); t += _jitter(0.08, 0.02)
    ev.append(Event(t, "NAS", "AuthenticationRequest", "AMF->UE", m.authentication_request(ng_ksi))); t += _jitter(0.06, 0.02)
    ev.append(Event(t, "NAS", "AuthenticationResponse", "UE->AMF", m.authentication_response(True))); t += _jitter(0.07, 0.02)
    ev.append(Event(t, "NAS", "SecurityModeCommand", "AMF->UE", m.security_mode_command(ng_ksi))); t += _jitter(0.05, 0.01)
    ev.append(Event(t, "NAS", "SecurityModeComplete", "UE->AMF", m.security_mode_complete())); t += _jitter(0.06, 0.02)
    # RegistrationAccept dropped -- never logged. T3510 fires ~16s later, UE retries once.
    t += 16.0
    ev.append(Event(t, "NAS", "RegistrationRequest", "UE->AMF", m.registration_request(ng_ksi, "initial-registration"))); t += _jitter(0.08, 0.02)
    report_t = t
    duration = t + 2.0
    phy = _phy_baseline(duration)
    return SessionPlan(ev, phy, duration, "registration never completed, UE stuck retrying", report_t, "REG_TIMEOUT_DROPPED_NAS")


def build_RRC_CONN_FAIL_CONGESTION(rnti, ng_ksi, guti, tai, cell_id, neigh_cell_id) -> SessionPlan:
    ev: list[Event] = []
    t = 0.0
    ev.append(Event(t, "RRC", "RRCSetupRequest", "UE->gNB", m.rrc_setup_request(rnti))); t += _jitter(0.03, 0.01)
    ev.append(Event(t, "RRC", "RRCReject", "gNB->UE", m.rrc_reject(random.choice([4, 8, 16]), "congestion"))); t += 0.02
    report_t = t
    duration = t + 1.0
    phy = _phy_baseline(duration, rsrp_center=-90)
    return SessionPlan(ev, phy, duration, "connection setup rejected", report_t, "RRC_CONN_FAIL_CONGESTION")


def build_HO_MISSED_MEASUREMENT(rnti, ng_ksi, guti, tai, cell_id, neigh_cell_id) -> SessionPlan:
    ev: list[Event] = []
    t = 0.0
    t = _attach(ev, t, rnti, ng_ksi, guti, tai)
    duration = t + 20.0
    # RSRP decays past the A3 threshold but no MeasurementReport ever appears
    phy = _phy_decaying(duration - t, start_rsrp=-88, end_rsrp=-119)
    phy = [PhySample(round(p.t_rel + t, 3), p.rsrp_dbm, p.rsrq_db, p.sinr_db) for p in phy]
    report_t = duration - 0.5
    ev.append(Event(duration, "RRC", "RRCRelease", "gNB->UE", m.rrc_release("radioNetwork")))
    return SessionPlan(ev, phy, duration + 0.1, "handover never happened, connection dropped", report_t, "HO_MISSED_MEASUREMENT")


def build_HANDOVER_RECONFIG_TIMEOUT(rnti, ng_ksi, guti, tai, cell_id, neigh_cell_id) -> SessionPlan:
    ev: list[Event] = []
    t = 0.0
    t = _attach(ev, t, rnti, ng_ksi, guti, tai)
    t += _jitter(8.0, 2.0)
    ev.append(Event(t, "RRC", "MeasurementReport", "UE->gNB", m.measurement_report(-95, -13, neigh_cell_id, -83))); t += 0.02
    ev.append(Event(t, "RRC", "RRCReconfiguration", "gNB->UE",
                     m.rrc_reconfiguration(2, {"targetCellId": neigh_cell_id, "newUE-Identity": f"{rnti+1:010x}", "t304_ms": 1000})))
    # RRCReconfigurationComplete never arrives -- t304 expires
    t304_expiry = t + 1.0
    ev.append(Event(t304_expiry, "RRC", "RRCReestablishmentRequest", "UE->gNB", m.rrc_reestablishment_request("handoverFailure")))
    ev.append(Event(t304_expiry + 0.03, "RRC", "RRCReestablishmentReject", "gNB->UE", m.rrc_reestablishment_reject()))
    report_t = t304_expiry + 0.03
    duration = report_t + 1.0
    phy = _phy_baseline(duration, rsrp_center=-93)
    return SessionPlan(ev, phy, duration, "handover failed, connection dropped", report_t, "HANDOVER_RECONFIG_TIMEOUT")


def build_PHY_SIGNAL_RLF(rnti, ng_ksi, guti, tai, cell_id, neigh_cell_id) -> SessionPlan:
    ev: list[Event] = []
    t = 0.0
    t = _attach(ev, t, rnti, ng_ksi, guti, tai)
    duration = t + 12.0
    phy = _phy_decaying(duration - t, start_rsrp=-90, end_rsrp=-124, step=0.2)
    phy = [PhySample(round(p.t_rel + t, 3), p.rsrp_dbm, p.rsrq_db, p.sinr_db) for p in phy]
    ev.append(Event(duration, "RRC", "RRCReestablishmentRequest", "UE->gNB", m.rrc_reestablishment_request("otherFailure")))
    ev.append(Event(duration + 0.03, "RRC", "RRCReestablishmentReject", "gNB->UE", m.rrc_reestablishment_reject()))
    report_t = duration + 0.03
    return SessionPlan(ev, phy, duration + 1.0, "connection lost, poor signal reported", report_t, "PHY_SIGNAL_RLF")


def build_SECURITY_MODE_FAILURE(rnti, ng_ksi, guti, tai, cell_id, neigh_cell_id) -> SessionPlan:
    ev: list[Event] = []
    t = 0.0
    t = _attach(ev, t, rnti, ng_ksi, guti, tai)
    t += _jitter(6.0, 1.5)
    ev.append(Event(t, "RRC", "MeasurementReport", "UE->gNB", m.measurement_report(-91, -12, neigh_cell_id, -80))); t += 0.02
    ev.append(Event(t, "RRC", "RRCReconfiguration", "gNB->UE",
                     m.rrc_reconfiguration(2, {"targetCellId": neigh_cell_id, "newUE-Identity": f"{rnti+1:010x}", "t304_ms": 1000}))); t += 0.05
    ev.append(Event(t, "RRC", "RRCReconfigurationComplete", "UE->gNB", m.rrc_reconfiguration_complete(2))); t += 0.1
    new_ksi = ng_ksi ^ 1  # stale key context after handover
    ev.append(Event(t, "NAS", "SecurityModeCommand", "AMF->UE", m.security_mode_command(new_ksi))); t += 0.04
    ev.append(Event(t, "NAS", "SecurityModeReject", "UE->AMF", m.security_mode_reject("security-mode-failure"))); t += 0.02
    report_t = t
    duration = t + 1.0
    phy = _phy_baseline(duration, rsrp_center=-89)
    return SessionPlan(ev, phy, duration, "connection dropped after handover", report_t, "SECURITY_MODE_FAILURE")


def build_SERVICE_REQUEST_NO_CONTEXT(rnti, ng_ksi, guti, tai, cell_id, neigh_cell_id) -> SessionPlan:
    ev: list[Event] = []
    t = 0.0
    t = _attach(ev, t, rnti, ng_ksi, guti, tai)
    ev.append(Event(t, "RRC", "RRCRelease", "gNB->UE", m.rrc_release("normal"))); t += _jitter(35.0, 5.0)
    ev.append(Event(t, "RRC", "RRCSetupRequest", "UE->gNB", m.rrc_setup_request(rnti + 2, "mo-Data"))); t += 0.03
    ev.append(Event(t, "RRC", "RRCSetup", "gNB->UE", m.rrc_setup(3))); t += 0.04
    ev.append(Event(t, "RRC", "RRCSetupComplete", "UE->gNB", m.rrc_setup_complete(3))); t += 0.05
    ev.append(Event(t, "NAS", "ServiceRequest", "UE->AMF", m.service_request(ng_ksi))); t += 0.03
    ev.append(Event(t, "NAS", "ServiceReject", "AMF->UE", m.service_reject("no-context"))); t += 0.02
    report_t = t
    duration = t + 1.0
    phy = _phy_baseline(duration)
    return SessionPlan(ev, phy, duration, "data session request rejected", report_t, "SERVICE_REQUEST_NO_CONTEXT")


def build_PAGING_TIMEOUT(rnti, ng_ksi, guti, tai, cell_id, neigh_cell_id) -> SessionPlan:
    ev: list[Event] = []
    t = 0.0
    t = _attach(ev, t, rnti, ng_ksi, guti, tai)
    ev.append(Event(t, "RRC", "RRCRelease", "gNB->UE", m.rrc_release("normal"))); t += _jitter(20.0, 4.0)
    ev.append(Event(t, "RRC", "Paging", "gNB->UE", m.paging(guti))); t += 2.0  # paging timer expires, no response
    report_t = t
    duration = t + 1.0
    phy = _phy_baseline(duration, rsrp_center=-95)
    return SessionPlan(ev, phy, duration, "downlink data could not reach UE, unreachable", report_t, "PAGING_TIMEOUT")


def build_DEREGISTRATION_IMPLICIT(rnti, ng_ksi, guti, tai, cell_id, neigh_cell_id) -> SessionPlan:
    ev: list[Event] = []
    t = 0.0
    t = _attach(ev, t, rnti, ng_ksi, guti, tai)
    ev.append(Event(t, "RRC", "RRCRelease", "gNB->UE", m.rrc_release("normal")))
    # UE goes silent, misses its periodic registration update; mobile-reachable timer expires
    t += _jitter(54.0, 4.0)
    ev.append(Event(t, "NAS", "ImplicitDeregistration", "N/A", m.implicit_deregistration_internal()))
    report_t = t
    duration = t + 1.0
    phy = _phy_baseline(duration)
    return SessionPlan(ev, phy, duration, "UE went unreachable, dropped from network", report_t, "DEREGISTRATION_IMPLICIT")


BUILDERS = {
    "AUTH_BAD_KEY": build_AUTH_BAD_KEY,
    "REG_TIMEOUT_DROPPED_NAS": build_REG_TIMEOUT_DROPPED_NAS,
    "RRC_CONN_FAIL_CONGESTION": build_RRC_CONN_FAIL_CONGESTION,
    "HO_MISSED_MEASUREMENT": build_HO_MISSED_MEASUREMENT,
    "HANDOVER_RECONFIG_TIMEOUT": build_HANDOVER_RECONFIG_TIMEOUT,
    "PHY_SIGNAL_RLF": build_PHY_SIGNAL_RLF,
    "SECURITY_MODE_FAILURE": build_SECURITY_MODE_FAILURE,
    "SERVICE_REQUEST_NO_CONTEXT": build_SERVICE_REQUEST_NO_CONTEXT,
    "PAGING_TIMEOUT": build_PAGING_TIMEOUT,
    "DEREGISTRATION_IMPLICIT": build_DEREGISTRATION_IMPLICIT,
}
