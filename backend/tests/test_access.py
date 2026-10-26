from app.schemas.access import (
    AccessLogEntry,
    AccessReason,
    Role,
    append_log,
    tiers_for,
    verify_chain,
)
from app.schemas.record import Tier


def _entry(actor="dr-a"):
    return AccessLogEntry(patient_id="p1", actor_id=actor, actor_role=Role.DOCTOR,
                          actor_label="Dr. A", reason=AccessReason.EMERGENCY,
                          tiers=[Tier.EMERGENCY_CARD])


def test_chain_verifies_and_detects_tampering():
    log = []
    for i in range(3):
        append_log(log, _entry(f"dr-{i}"))
    assert verify_chain(log)
    log[1].actor_label = "Someone else"
    assert not verify_chain(log)


def test_chain_detects_deletion():
    log = []
    for i in range(3):
        append_log(log, _entry(f"dr-{i}"))
    del log[1]
    assert not verify_chain(log)


def test_policy():
    assert Tier.FULL_RECORD in tiers_for(Role.PATIENT, AccessReason.OWN)
    em = tiers_for(Role.DOCTOR, AccessReason.EMERGENCY)
    assert em == {Tier.EMERGENCY_CARD, Tier.CLINICAL_SUMMARY}
    assert tiers_for(Role.TRUSTED, AccessReason.EMERGENCY) == set()  # basics and a code only
    assert tiers_for(Role.DOCTOR, AccessReason.OWN) == set()


def test_log_entries_without_via_keep_their_old_hash():
    """`via` was added later; entries written before it must still verify."""
    import hashlib
    import json

    e = _entry("dr-1")
    old_body = e.model_dump(mode="json", exclude={"hash", "via"})
    old_hash = hashlib.sha256(
        json.dumps(old_body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert e.compute_hash() == old_hash
    e.via = "Ravi"
    assert e.compute_hash() != old_hash  # once set, it is part of the chain
