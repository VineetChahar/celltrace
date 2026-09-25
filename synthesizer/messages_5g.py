"""5G NR RRC (3GPP TS 38.331) and 5G NAS (3GPP TS 24.501) message builders.

Every `fields` dict below uses the real information-element (IE) names from the
public specs, simplified to the subset relevant for the fault types this project
injects (e.g. we don't model every optional IE in an RRCReconfiguration -- we
model the ones that matter for handover/measurement fault stories).
"""
import random

# --- RRC (38.331) ------------------------------------------------------------

def rrc_setup_request(rnti: int, cause: str = "mo-Data") -> dict:
    return {
        "ue-Identity": f"{rnti:010x}",
        "establishmentCause": cause,
    }


def rrc_setup(transaction_id: int) -> dict:
    return {
        "rrc-TransactionIdentifier": transaction_id,
        "radioBearerConfig": {"srb-ToAddModList": ["SRB1"]},
    }


def rrc_setup_complete(transaction_id: int, plmn: str = "00101") -> dict:
    return {
        "rrc-TransactionIdentifier": transaction_id,
        "selectedPLMN-Identity": plmn,
    }


def rrc_reject(wait_time_s: int, cause: str = "congestion") -> dict:
    return {"rrc-RejectWaitTime": wait_time_s, "cause": cause}


def rrc_reconfiguration(transaction_id: int, mobility_control_info: dict | None = None) -> dict:
    f = {"rrc-TransactionIdentifier": transaction_id}
    if mobility_control_info:
        f["mobilityControlInfo"] = mobility_control_info
    else:
        f["measConfig"] = {"reportConfigId": 1, "eventId": "A3", "a3-offset_dB": 3}
    return f


def rrc_reconfiguration_complete(transaction_id: int) -> dict:
    return {"rrc-TransactionIdentifier": transaction_id}


def measurement_report(serving_rsrp: float, serving_rsrq: float, neigh_cell: str, neigh_rsrp: float) -> dict:
    return {
        "measResultServingCell": {"rsrp": round(serving_rsrp, 1), "rsrq": round(serving_rsrq, 1)},
        "measResultNeighCells": [{"physCellId": neigh_cell, "rsrp": round(neigh_rsrp, 1)}],
    }


def rrc_reestablishment_request(reestab_cause: str = "otherFailure") -> dict:
    return {"reestablishmentCause": reestab_cause}


def rrc_reestablishment_reject() -> dict:
    return {"cause": "unspecified"}


def paging(ue_identity_5g_s_tmsi: str) -> dict:
    return {"pagingRecordList": [{"ue-Identity": ue_identity_5g_s_tmsi, "accessType": "non3GPP-not-used"}]}


def rrc_release(cause: str = "other") -> dict:
    return {"cause": cause}


# --- NAS (24.501) -------------------------------------------------------------

def registration_request(ng_ksi: int, reg_type: str = "initial-registration") -> dict:
    return {
        "5gsRegistrationType": reg_type,
        "ngKSI": ng_ksi,
        "ueSecurityCapability": {"5g-EA": [0, 1, 2], "5g-IA": [0, 1, 2]},
    }


def authentication_request(ng_ksi: int) -> dict:
    return {
        "ngKSI": ng_ksi,
        "authParamRAND": random.getrandbits(128).to_bytes(16, "big").hex(),
        "authParamAUTN": random.getrandbits(128).to_bytes(16, "big").hex(),
    }


def authentication_response(valid: bool) -> dict:
    res = random.getrandbits(64).to_bytes(8, "big").hex()
    return {"authParamRES": res, "_sim_valid": valid}


def authentication_reject(cause: str = "MAC-failure") -> dict:
    return {"cause": cause}


def security_mode_command(ng_ksi: int) -> dict:
    return {
        "selectedNASSecurityAlgorithms": {"cipher": "5G-EA2", "integrity": "5G-IA2"},
        "ngKSI": ng_ksi,
        "replayedUESecurityCapabilities": {"5g-EA": [0, 1, 2], "5g-IA": [0, 1, 2]},
    }


def security_mode_complete() -> dict:
    return {}


def security_mode_reject(cause: str = "security-mode-failure") -> dict:
    return {"cause": cause}


def registration_accept(guti: str, tai_list: list[str]) -> dict:
    return {"5gGUTI": guti, "registrationResult": "3GPP-access", "taiList": tai_list}


def registration_complete() -> dict:
    return {}


def service_request(ng_ksi: int, service_type: str = "data") -> dict:
    return {"ngKSI": ng_ksi, "serviceType": service_type}


def service_reject(cause: str = "no-context") -> dict:
    return {"cause": cause}


def deregistration_request_ue_terminated(cause: str = "reregistration-required") -> dict:
    return {"deregistrationType": "3GPP-access", "cause": cause}


def implicit_deregistration_internal(reason: str = "periodic-registration-timer-expired") -> dict:
    """Not an over-the-air message -- an AMF-internal context-drop event. Real AMFs log
    these; we surface it because it's the only observable trace of an implicit dereg."""
    return {"reason": reason}
