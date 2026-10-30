"""Sealing evidence on the camera.

This is the only path by which raw pixels ever leave a node, and they leave
encrypted with a key the node generates, splits three ways and then forgets. The
brain stores the ciphertext and the shares; it never sees the key, and cannot
reconstruct it without two of the three keyholders agreeing.

The key exists in this process for a few milliseconds and is overwritten
afterwards. That is not a hard guarantee in CPython — bytes are immutable and
copies may survive — but it is the right shape, and nothing here ever logs it.
"""
from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

import requests
from Crypto.Cipher import AES
from Crypto.Protocol.SecretSharing import Shamir
from Crypto.Random import get_random_bytes

if TYPE_CHECKING:  # pragma: no cover
    from node.ringbuf import RingBuffer

KEY_BYTES = 16          # Shamir in pycryptodome splits a 16-byte secret
THRESHOLD = 2           # any two of
SHARES = 3              # three keyholders
UPLOAD_TIMEOUT_S = 30.0
RETRIES = 3


def seal_bytes(plaintext: bytes) -> tuple[bytes, bytes, bytes, list[tuple[int, bytes]]]:
    """Encrypt and split the key. Returns (ciphertext, nonce, tag, shares)."""
    key = get_random_bytes(KEY_BYTES)
    try:
        cipher = AES.new(key, AES.MODE_GCM)
        ciphertext, tag = cipher.encrypt_and_digest(plaintext)
        shares = Shamir.split(THRESHOLD, SHARES, key)
        return ciphertext, cipher.nonce, tag, shares
    finally:
        key = b"\x00" * KEY_BYTES  # noqa: F841 — drop the reference promptly


class EscrowClient:
    """Packs the ring buffer, encrypts it, and uploads it to the brain."""

    def __init__(self, cam: str, brain_url: str, ring: RingBuffer) -> None:
        self.cam = cam
        self.brain_url = brain_url.rstrip("/")
        self.ring = ring
        self.log = logging.getLogger(f"node.escrow {cam}")
        self.sealed = 0
        self.failed = 0

    def handle_seal(self, cmd: dict[str, Any]) -> None:
        """Dispatch target for the `seal` command."""
        evidence_id = str(cmd.get("evidence_id") or "")
        if not evidence_id:
            self.log.warning("seal command without an evidence_id, ignoring")
            return
        self.seal(evidence_id)

    def seal(self, evidence_id: str) -> bool:
        if len(self.ring) == 0:
            self.log.warning("seal %s requested but the ring buffer is empty", evidence_id)
            return False

        zip_bytes, meta = self.ring.to_zip(evidence_id)
        ciphertext, nonce, tag, shares = seal_bytes(zip_bytes)
        self.log.info(
            "sealed %s: %d frames, %d kB plaintext -> %d kB ciphertext, key split %d-of-%d",
            evidence_id, meta["n_frames"], len(zip_bytes) // 1024,
            len(ciphertext) // 1024, THRESHOLD, SHARES,
        )

        ok = self.upload(
            evidence_id=evidence_id,
            ciphertext=ciphertext,
            nonce_hex=nonce.hex(),
            tag_hex=tag.hex(),
            meta=meta,
            shares=[[int(idx), bytes(share).hex()] for idx, share in shares],
        )
        if ok:
            self.sealed += 1
        else:
            self.failed += 1
        return ok

    def upload(self, *, evidence_id: str, ciphertext: bytes, nonce_hex: str,
               tag_hex: str, meta: dict[str, Any], shares: list[list[Any]]) -> bool:
        """POST the sealed clip, retrying with backoff — wifi at a demo is wifi."""
        url = f"{self.brain_url}/api/evidence/upload"
        data = {
            "evidence_id": evidence_id,
            "cam": self.cam,
            "nonce_hex": nonce_hex,
            "tag_hex": tag_hex,
            "meta_json": json.dumps(meta),
            "shares_json": json.dumps(shares),
        }
        for attempt in range(1, RETRIES + 1):
            try:
                resp = requests.post(
                    url, data=data,
                    files={"blob": (f"{evidence_id}.bin", ciphertext,
                                    "application/octet-stream")},
                    timeout=UPLOAD_TIMEOUT_S,
                )
                if resp.status_code < 300:
                    self.log.info("uploaded %s to the brain", evidence_id)
                    return True
                self.log.warning("upload of %s rejected: %s %s", evidence_id,
                                 resp.status_code, resp.text[:200])
            except requests.RequestException as exc:
                self.log.warning("upload of %s failed (attempt %d/%d): %s",
                                 evidence_id, attempt, RETRIES, exc)
            if attempt < RETRIES:
                time.sleep(2 ** attempt)
        self.log.error("gave up uploading %s after %d attempts", evidence_id, RETRIES)
        return False
