"""Pseudonymizes synthetic subscriber identifiers before they ever reach disk.

The HMAC key and the raw imsi_sim_* identifiers live only in memory during
generation. Nothing downstream (log files, the parser, the LLM prompt) ever
sees a raw identifier -- only the pseudonym.
"""
import hashlib
import hmac
import os

_KEY = os.urandom(32)


def pseudonymize(raw_subscriber_id: str) -> str:
    digest = hmac.new(_KEY, raw_subscriber_id.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"ue_{digest[:8]}"
