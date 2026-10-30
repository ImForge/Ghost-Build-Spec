"""Evidence escrow: sealed on the camera, opened only by agreement.

The design question this answers is "who can watch the footage?". The honest
answer in most systems is "whoever runs the server". Here the answer is "nobody
alone, and every viewing is recorded".

How it works:

* the node encrypts the clip with AES-GCM and a key it generates locally;
* that key is split with Shamir's scheme into three shares, any two of which
  reconstruct it, and the shares are handed to three different keyholder roles;
* the brain stores the ciphertext and the shares but never the key;
* two distinct roles must approve before the brain will combine shares at all;
* the unsealed state expires after fifteen minutes, so approval is for an
  occasion rather than forever;
* every approval, unseal, view and purge lands in the audit table.

The shares are stored as plaintext hex in sqlite, which is a real limitation and
is called out in docs/ARCHITECTURE.md: production would encrypt each share to its
keyholder's own public key so the database alone is useless.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from Crypto.Cipher import AES
from Crypto.Protocol.SecretSharing import Shamir

from brain.constants import APPROVALS_REQUIRED, KEYHOLDER_ROLES, UNSEAL_WINDOW_S
from brain.db import new_id
from brain.settings import settings

if TYPE_CHECKING:  # pragma: no cover
    from brain.db import Database
    from brain.mqtt_bus import MqttBus

log = logging.getLogger("brain.escrow")

SEAL_TIMEOUT_S = 20.0  # a node that has not uploaded by now is not going to


class EscrowError(RuntimeError):
    """Something went wrong that the caller should turn into an HTTP error."""


class Escrow:
    def __init__(self, db: Database, bus: MqttBus,
                 evidence_dir: Path | None = None) -> None:
        self.db = db
        self.bus = bus
        self.dir = evidence_dir or settings.evidence_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    # --- sealing ----------------------------------------------------------
    def request_seal(self, alert: dict[str, Any]) -> str:
        """Ask the camera to seal its ring buffer for this alert."""
        evidence_id = new_id("e")
        cam = str(alert["cam"])
        self.db.insert_evidence(evidence_id, str(alert["id"]), cam, status="requested")
        self.db.update_alert(str(alert["id"]), evidence_id=evidence_id)
        self.bus.send_command(cam, {"type": "seal", "evidence_id": evidence_id})
        log.info("[%s] requested seal %s for alert %s", cam, evidence_id, alert["id"])
        return evidence_id

    def store_upload(self, *, evidence_id: str, cam: str, nonce_hex: str, tag_hex: str,
                     meta_json: str, shares: list[list[Any]], blob: bytes) -> dict[str, Any]:
        """Accept a sealed clip from a node. The brain never sees the key."""
        if len(shares) != len(KEYHOLDER_ROLES):
            raise EscrowError(
                f"expected {len(KEYHOLDER_ROLES)} key shares, got {len(shares)}"
            )

        row = self.db.get_evidence(evidence_id)
        if row is None:
            # A node may seal without having been asked (the debug endpoint, or
            # a retry after the brain restarted). Record it rather than lose it.
            self.db.insert_evidence(evidence_id, None, cam, status="requested")

        path = self.dir / f"{evidence_id}.bin"
        path.write_bytes(blob)

        self.db.insert_shares(
            evidence_id,
            [(role, int(idx), str(share_hex))
             for role, (idx, share_hex) in zip(KEYHOLDER_ROLES, shares)],
        )
        self.db.update_evidence(
            evidence_id, status="sealed", nonce_hex=nonce_hex, tag_hex=tag_hex,
            meta_json=meta_json, size=len(blob), path=str(path),
        )
        self.db.audit(f"node:{cam}", "seal", evidence_id, {"bytes": len(blob)})
        log.info("[%s] sealed %s (%d bytes, %d shares)", cam, evidence_id,
                 len(blob), len(shares))
        return self.summary(evidence_id) or {}

    # --- approvals --------------------------------------------------------
    def approve(self, evidence_id: str, role: str) -> dict[str, Any]:
        row = self.db.get_evidence(evidence_id)
        if row is None:
            raise EscrowError(f"no such evidence: {evidence_id}")
        if row["status"] == "unavailable":
            raise EscrowError("that camera never sealed this clip")
        if row["status"] == "requested":
            # Approvals only count against a clip that exists. Accepting them
            # earlier looks like it worked — the counter reaches 2 of 2 — but
            # the unseal check has already gone past, so the clip stays locked
            # with no way to explain why.
            raise EscrowError(
                "the camera has not uploaded this clip yet; try again in a moment"
            )

        fresh = self.db.add_approval(evidence_id, role)
        self.db.audit(role, "approve", evidence_id,
                      {"new": fresh, "status": row["status"]})
        if not fresh:
            log.info("%s had already approved %s", role, evidence_id)

        approvals = self.db.get_approvals(evidence_id)
        roles = {a["keyholder_role"] for a in approvals}
        if len(roles) >= APPROVALS_REQUIRED and row["status"] == "sealed":
            self.db.update_evidence(evidence_id, status="unsealed", unsealed_at=time.time())
            self.db.audit(role, "unseal", evidence_id, {"roles": sorted(roles)})
            log.info("%s unsealed by %s", evidence_id, " + ".join(sorted(roles)))
        return self.summary(evidence_id) or {}

    def flag(self, evidence_id: str, role: str) -> dict[str, Any]:
        """Keep this clip past the retention window."""
        if self.db.get_evidence(evidence_id) is None:
            raise EscrowError(f"no such evidence: {evidence_id}")
        self.db.update_evidence(evidence_id, flagged=1)
        self.db.audit(role, "flag", evidence_id, "kept beyond TTL")
        return self.summary(evidence_id) or {}

    # --- viewing ----------------------------------------------------------
    def clip(self, evidence_id: str, role: str) -> bytes:
        """Decrypt and return the ZIP. Only possible while unsealed."""
        row = self.db.get_evidence(evidence_id)
        if row is None:
            raise EscrowError(f"no such evidence: {evidence_id}")
        if row["status"] != "unsealed":
            raise EscrowError(
                f"evidence is {row['status']}; {APPROVALS_REQUIRED} keyholders must "
                "approve before it can be viewed"
            )
        if self._expired(row):
            self.relock(evidence_id)
            raise EscrowError("the unseal window has expired; re-approval is needed")

        blob = Path(str(row["path"])).read_bytes()
        key = self.recover_key(evidence_id)
        try:
            cipher = AES.new(key, AES.MODE_GCM, nonce=bytes.fromhex(row["nonce_hex"]))
            plaintext = cipher.decrypt_and_verify(blob, bytes.fromhex(row["tag_hex"]))
        except (ValueError, KeyError) as exc:
            raise EscrowError(f"the sealed clip did not verify: {exc}") from exc
        finally:
            # Not a security guarantee in CPython, but the key does not linger
            # in this frame any longer than it has to.
            key = b"\x00" * len(key)

        self.db.audit(role, "view", evidence_id, {"bytes": len(plaintext)})
        log.info("%s viewed by %s (%d bytes)", evidence_id, role, len(plaintext))
        return plaintext

    def recover_key(self, evidence_id: str) -> bytes:
        """Combine the stored shares. Any two of the three suffice."""
        shares = self.db.get_shares(evidence_id)
        if len(shares) < APPROVALS_REQUIRED:
            raise EscrowError(
                f"only {len(shares)} key shares stored; {APPROVALS_REQUIRED} are needed"
            )
        pairs = [(int(s["idx"]), bytes.fromhex(s["share_hex"])) for s in shares]
        try:
            return Shamir.combine(pairs[:APPROVALS_REQUIRED])
        except ValueError as exc:
            raise EscrowError(f"key shares did not combine: {exc}") from exc

    # --- lifecycle --------------------------------------------------------
    @staticmethod
    def _expired(row: dict[str, Any]) -> bool:
        if row["status"] != "unsealed" or not row["unsealed_at"]:
            return False
        return time.time() - float(row["unsealed_at"]) > UNSEAL_WINDOW_S

    def relock(self, evidence_id: str) -> None:
        """Back to sealed, approvals cleared: approval is for an occasion."""
        self.db.update_evidence(evidence_id, status="sealed", unsealed_at=None)
        self.db.clear_approvals(evidence_id)
        self.db.audit("system", "flag", evidence_id, "unseal window expired, re-locked")
        log.info("%s re-locked after the unseal window expired", evidence_id)

    def housekeep(self, now: float | None = None) -> dict[str, int]:
        """Mark unanswered seals, re-lock expired unseals, purge old clips."""
        now = time.time() if now is None else now
        counts = {"unavailable": 0, "relocked": 0, "purged": 0}

        for row in self.db.list_evidence(status="requested"):
            if now - float(row["created_at"]) > SEAL_TIMEOUT_S:
                self.db.update_evidence(row["id"], status="unavailable")
                counts["unavailable"] += 1
                log.info("%s marked unavailable — %s never uploaded a clip",
                         row["id"], row["cam"])

        for row in self.db.list_evidence(status="unsealed"):
            if self._expired(row):
                self.relock(row["id"])
                counts["relocked"] += 1

        cutoff = now - settings.evidence_ttl_days * 86400
        for row in self.db.list_evidence():
            if row["flagged"] or float(row["created_at"]) >= cutoff:
                continue
            if row["path"]:
                Path(str(row["path"])).unlink(missing_ok=True)
            self.db.delete_evidence(row["id"])
            self.db.audit("system", "purge", row["id"], "past retention")
            counts["purged"] += 1

        return counts

    # --- reading ----------------------------------------------------------
    def summary(self, evidence_id: str) -> dict[str, Any] | None:
        """What the dashboard may know: status and which roles approved. No shares."""
        row = self.db.get_evidence(evidence_id)
        if row is None:
            return None
        approvals = [a["keyholder_role"] for a in self.db.get_approvals(evidence_id)]
        meta: dict[str, Any] | None = None
        if row["meta_json"]:
            try:
                meta = json.loads(row["meta_json"])
            except ValueError:
                meta = None
        return {
            "id": row["id"],
            "alert_id": row["alert_id"],
            "cam": row["cam"],
            "status": row["status"],
            "created_at": row["created_at"],
            "unsealed_at": row["unsealed_at"],
            "flagged": bool(row["flagged"]),
            "size": row["size"],
            "meta": meta,
            "approvals": sorted(approvals),
            "approvals_required": APPROVALS_REQUIRED,
            "keyholder_roles": list(KEYHOLDER_ROLES),
            "unseal_window_s": UNSEAL_WINDOW_S,
            "expires_at": (
                float(row["unsealed_at"]) + UNSEAL_WINDOW_S
                if row["status"] == "unsealed" and row["unsealed_at"] else None
            ),
        }
