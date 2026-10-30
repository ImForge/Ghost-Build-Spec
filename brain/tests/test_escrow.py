"""The escrow workflow end to end, through the real HTTP API.

The access rules are the product here, so they are tested the way a person
would hit them: upload, approve, approve again as the same role, approve as a
second role, fetch the clip, and check the audit trail recorded all of it.
"""
from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import pytest
from fastapi.testclient import TestClient

from node.escrow_client import seal_bytes

SECURITY = {"X-Role-Token": "sec-123"}
WARDEN = {"X-Role-Token": "war-123"}
PRIVACY = {"X-Role-Token": "priv-123"}
BOGUS = {"X-Role-Token": "not-a-real-token"}


def _clip_zip(frames: int = 4) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("meta.json", json.dumps({
            "cam": "cam-01", "evidence_id": "e_test", "fps": 5,
            "started_ts": 1000.0, "ended_ts": 1006.0, "n_frames": frames,
        }))
        for i in range(1, frames + 1):
            zf.writestr(f"frames/{i:06d}.jpg", b"\xff\xd8\xff\xe0" + bytes([i]) * 128)
    return buf.getvalue()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("EVIDENCE_DIR", str(tmp_path / "evidence"))
    # Settings are read at import; rebuild them for this temp directory.
    import importlib

    import brain.settings as settings_mod

    importlib.reload(settings_mod)
    import brain.app as app_mod

    importlib.reload(app_mod)
    with TestClient(app_mod.app) as c:
        yield c


def _upload(client: TestClient, evidence_id: str = "e_test1",
            plain: bytes | None = None) -> dict[str, Any]:
    plain = plain if plain is not None else _clip_zip()
    ciphertext, nonce, tag, shares = seal_bytes(plain)
    resp = client.post(
        "/api/evidence/upload",
        data={
            "evidence_id": evidence_id,
            "cam": "cam-01",
            "nonce_hex": nonce.hex(),
            "tag_hex": tag.hex(),
            "meta_json": json.dumps({"cam": "cam-01", "n_frames": 4, "fps": 5}),
            "shares_json": json.dumps([[int(i), bytes(s).hex()] for i, s in shares]),
        },
        files={"blob": ("clip.bin", ciphertext, "application/octet-stream")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


# --- upload --------------------------------------------------------------
def test_upload_seals_the_evidence(client):
    body = _upload(client)
    assert body["status"] == "sealed"
    assert body["approvals"] == []
    assert body["approvals_required"] == 2
    assert body["size"] > 0


def test_the_summary_never_exposes_key_shares(client):
    _upload(client, "e_secret")
    body = client.get("/api/evidence/e_secret").json()
    blob = json.dumps(body)
    assert "share" not in blob.lower() or "shares" not in body
    assert "share_hex" not in blob


def test_upload_rejects_the_wrong_number_of_shares(client):
    ciphertext, nonce, tag, shares = seal_bytes(_clip_zip())
    resp = client.post(
        "/api/evidence/upload",
        data={
            "evidence_id": "e_short", "cam": "cam-01",
            "nonce_hex": nonce.hex(), "tag_hex": tag.hex(),
            "meta_json": "{}",
            "shares_json": json.dumps([[1, bytes(shares[0][1]).hex()]]),
        },
        files={"blob": ("clip.bin", ciphertext, "application/octet-stream")},
    )
    assert resp.status_code == 422
    assert "3 key shares" in resp.json()["detail"]


# --- the approval gate ---------------------------------------------------
def test_a_sealed_clip_cannot_be_viewed(client):
    _upload(client, "e_sealed")
    resp = client.get("/api/evidence/e_sealed/clip", headers=SECURITY)
    assert resp.status_code == 409
    assert "approve" in resp.json()["detail"]


def test_one_approval_is_not_enough(client):
    _upload(client, "e_one")
    body = client.post("/api/evidence/e_one/approve", headers=SECURITY).json()
    assert body["status"] == "sealed"
    assert body["approvals"] == ["security"]
    assert client.get("/api/evidence/e_one/clip", headers=SECURITY).status_code == 409


def test_the_same_role_approving_twice_does_not_unseal(client):
    """The point of 2-of-3 is two *people*, not two clicks."""
    _upload(client, "e_same")
    client.post("/api/evidence/e_same/approve", headers=SECURITY)
    body = client.post("/api/evidence/e_same/approve", headers=SECURITY).json()
    assert body["status"] == "sealed"
    assert body["approvals"] == ["security"]
    assert client.get("/api/evidence/e_same/clip", headers=SECURITY).status_code == 409


def test_two_distinct_roles_unseal_it(client):
    _upload(client, "e_two")
    client.post("/api/evidence/e_two/approve", headers=SECURITY)
    body = client.post("/api/evidence/e_two/approve", headers=WARDEN).json()
    assert body["status"] == "unsealed"
    assert body["approvals"] == ["security", "warden"]
    assert body["expires_at"] is not None


def test_any_two_of_the_three_roles_will_do(client):
    _upload(client, "e_pair")
    client.post("/api/evidence/e_pair/approve", headers=WARDEN)
    body = client.post("/api/evidence/e_pair/approve", headers=PRIVACY).json()
    assert body["status"] == "unsealed"


# --- viewing -------------------------------------------------------------
def test_the_unsealed_clip_round_trips_to_the_original_zip(client):
    plain = _clip_zip(6)
    _upload(client, "e_view", plain)
    client.post("/api/evidence/e_view/approve", headers=SECURITY)
    client.post("/api/evidence/e_view/approve", headers=WARDEN)

    resp = client.get("/api/evidence/e_view/clip", headers=SECURITY)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert resp.content == plain

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        assert json.loads(zf.read("meta.json"))["n_frames"] == 6
        assert len([n for n in zf.namelist() if n.startswith("frames/")]) == 6


def test_a_third_party_token_is_refused_everywhere(client):
    _upload(client, "e_denied")
    client.post("/api/evidence/e_denied/approve", headers=SECURITY)
    client.post("/api/evidence/e_denied/approve", headers=WARDEN)

    assert client.post("/api/evidence/e_denied/approve", headers=BOGUS).status_code == 401
    assert client.get("/api/evidence/e_denied/clip", headers=BOGUS).status_code == 401
    assert client.post("/api/evidence/e_denied/flag", headers=BOGUS).status_code == 401


def test_no_token_at_all_is_refused(client):
    _upload(client, "e_anon")
    assert client.post("/api/evidence/e_anon/approve").status_code == 401
    assert client.get("/api/evidence/e_anon/clip").status_code == 401


def test_viewing_is_audited_with_the_role_that_did_it(client):
    _upload(client, "e_audit")
    client.post("/api/evidence/e_audit/approve", headers=SECURITY)
    client.post("/api/evidence/e_audit/approve", headers=WARDEN)
    client.get("/api/evidence/e_audit/clip", headers=PRIVACY)

    trail = client.get("/api/audit?target=e_audit").json()
    actions = [(row["actor"], row["action"]) for row in trail]
    assert ("security", "approve") in actions
    assert ("warden", "approve") in actions
    assert ("warden", "unseal") in actions
    assert ("privacy", "view") in actions


def test_the_unseal_expires_and_needs_re_approval(client):
    """Approval is for an occasion, not for good."""
    import brain.app as app_mod

    _upload(client, "e_expire")
    client.post("/api/evidence/e_expire/approve", headers=SECURITY)
    client.post("/api/evidence/e_expire/approve", headers=WARDEN)
    assert client.get("/api/evidence/e_expire/clip", headers=SECURITY).status_code == 200

    brain = app_mod.app.state.brain
    row = brain.db.get_evidence("e_expire")
    brain.db.update_evidence(
        "e_expire", unsealed_at=float(row["unsealed_at"]) - 16 * 60
    )

    assert client.get("/api/evidence/e_expire/clip", headers=SECURITY).status_code == 409
    body = client.get("/api/evidence/e_expire").json()
    assert body["status"] == "sealed"
    assert body["approvals"] == [], "expiring must clear the approvals too"


# --- lifecycle -----------------------------------------------------------
def test_flagging_marks_the_clip_for_keeping(client):
    _upload(client, "e_flag")
    body = client.post("/api/evidence/e_flag/flag", headers=PRIVACY).json()
    assert body["flagged"] is True
    trail = client.get("/api/audit?target=e_flag").json()
    assert any(row["action"] == "flag" for row in trail)


def test_an_unanswered_seal_becomes_unavailable(client):
    """A browser node cannot seal at all, and must not leave a stuck request."""
    import brain.app as app_mod

    import time

    brain = app_mod.app.state.brain
    brain.db.insert_evidence("e_never", "a_1", "cam-02", status="requested")
    # Past the 20 s seal timeout but well inside the retention window: an
    # older timestamp would be purged by the same housekeeping pass.
    brain.db.update_evidence("e_never", created_at=time.time() - 30)

    brain.escrow.housekeep()
    assert client.get("/api/evidence/e_never").json()["status"] == "unavailable"


def test_housekeeping_purges_clips_past_retention(client):
    """Evidence is deleted when it ages out, unless someone flagged it."""
    import time

    import brain.app as app_mod

    brain = app_mod.app.state.brain
    ancient = time.time() - (brain_ttl_days(brain) + 1) * 86400

    _upload(client, "e_old")
    _upload(client, "e_kept")
    brain.db.update_evidence("e_old", created_at=ancient)
    brain.db.update_evidence("e_kept", created_at=ancient)
    client.post("/api/evidence/e_kept/flag", headers=PRIVACY)

    counts = brain.escrow.housekeep()
    assert counts["purged"] == 1
    assert client.get("/api/evidence/e_old").status_code == 404
    assert client.get("/api/evidence/e_kept").status_code == 200
    trail = client.get("/api/audit?target=e_old").json()
    assert any(row["action"] == "purge" for row in trail)


def brain_ttl_days(brain) -> float:
    from brain.settings import settings

    return settings.evidence_ttl_days


def test_approving_an_unavailable_clip_is_refused(client):
    import brain.app as app_mod

    brain = app_mod.app.state.brain
    brain.db.insert_evidence("e_gone", "a_1", "cam-02", status="unavailable")
    resp = client.post("/api/evidence/e_gone/approve", headers=SECURITY)
    assert resp.status_code == 409
    assert "never sealed" in resp.json()["detail"]


def test_unknown_evidence_is_404_not_500(client):
    assert client.get("/api/evidence/e_nothere").status_code == 404
    assert client.post("/api/evidence/e_nothere/approve", headers=SECURITY).status_code == 404
    assert client.get("/api/evidence/e_nothere/clip", headers=SECURITY).status_code == 404


def test_whoami_identifies_each_keyholder(client):
    for headers, role in ((SECURITY, "security"), (WARDEN, "warden"), (PRIVACY, "privacy")):
        assert client.post("/api/auth/whoami", headers=headers).json()["role"] == role
    assert client.post("/api/auth/whoami", headers=BOGUS).status_code == 401


def test_approving_before_the_clip_arrives_is_refused(client):
    """Otherwise the counter reaches 2 of 2 against nothing and the clip is
    stuck: the unseal check has already gone past by the time it uploads."""
    import brain.app as app_mod

    brain = app_mod.app.state.brain
    brain.db.insert_evidence("e_pending", "a_1", "cam-01", status="requested")

    resp = client.post("/api/evidence/e_pending/approve", headers=SECURITY)
    assert resp.status_code == 409
    assert "not uploaded" in resp.json()["detail"]
    assert client.get("/api/evidence/e_pending").json()["approvals"] == []


def test_approvals_taken_after_upload_still_unseal(client):
    """The normal order of events, for contrast with the test above."""
    import brain.app as app_mod

    brain = app_mod.app.state.brain
    brain.db.insert_evidence("e_order", "a_1", "cam-01", status="requested")
    assert client.post("/api/evidence/e_order/approve", headers=SECURITY).status_code == 409

    _upload(client, "e_order")
    client.post("/api/evidence/e_order/approve", headers=SECURITY)
    body = client.post("/api/evidence/e_order/approve", headers=WARDEN).json()
    assert body["status"] == "unsealed"
