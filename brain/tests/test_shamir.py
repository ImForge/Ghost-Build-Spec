"""The cryptography the privacy claim rests on.

If any of these fail, "nobody can open the footage alone" is not true.
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from Crypto.Cipher import AES
from Crypto.Protocol.SecretSharing import Shamir
from Crypto.Random import get_random_bytes

from node.escrow_client import KEY_BYTES, SHARES, THRESHOLD, seal_bytes


def _zip_of_frames(n: int = 5) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("meta.json", json.dumps({"cam": "cam-01", "n_frames": n, "fps": 5}))
        for i in range(1, n + 1):
            zf.writestr(f"frames/{i:06d}.jpg", b"\xff\xd8\xff" + bytes([i]) * 64)
    return buf.getvalue()


# --- splitting and combining --------------------------------------------
def test_split_produces_three_shares():
    shares = Shamir.split(THRESHOLD, SHARES, get_random_bytes(KEY_BYTES))
    assert len(shares) == SHARES
    assert sorted(idx for idx, _s in shares) == [1, 2, 3]


def test_any_two_shares_recover_the_key():
    key = get_random_bytes(KEY_BYTES)
    shares = Shamir.split(THRESHOLD, SHARES, key)
    for a, b in ((0, 1), (0, 2), (1, 2)):
        assert Shamir.combine([shares[a], shares[b]]) == key


def test_all_three_shares_also_recover_the_key():
    key = get_random_bytes(KEY_BYTES)
    assert Shamir.combine(Shamir.split(THRESHOLD, SHARES, key)) == key


def test_one_share_alone_is_useless():
    """The whole point: a single keyholder, or a single stolen share, learns nothing."""
    key = get_random_bytes(KEY_BYTES)
    shares = Shamir.split(THRESHOLD, SHARES, key)
    for share in shares:
        assert bytes(share[1]) != key


# --- sealing a clip ------------------------------------------------------
def test_a_sealed_clip_round_trips():
    plain = _zip_of_frames()
    ciphertext, nonce, tag, shares = seal_bytes(plain)
    key = Shamir.combine(shares[:2])
    assert AES.new(key, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(ciphertext, tag) == plain


def test_the_ciphertext_does_not_contain_the_plaintext():
    plain = _zip_of_frames()
    ciphertext, _nonce, _tag, _shares = seal_bytes(plain)
    assert plain[:32] not in ciphertext
    assert b"meta.json" not in ciphertext
    assert b"\xff\xd8\xff" not in ciphertext, "a JPEG header must not survive encryption"


def test_the_wrong_key_fails_to_verify():
    """GCM's tag is what makes a wrong key an error rather than garbage output."""
    plain = _zip_of_frames()
    ciphertext, nonce, tag, _shares = seal_bytes(plain)
    wrong = get_random_bytes(KEY_BYTES)
    with pytest.raises(ValueError):
        AES.new(wrong, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(ciphertext, tag)


def test_a_key_combined_from_one_share_plus_a_forgery_fails():
    """Half the shares plus a guess is not two thirds of the way in."""
    plain = _zip_of_frames()
    ciphertext, nonce, tag, shares = seal_bytes(plain)
    forged = (shares[1][0], get_random_bytes(KEY_BYTES))
    bogus = Shamir.combine([shares[0], forged])
    with pytest.raises(ValueError):
        AES.new(bogus, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(ciphertext, tag)


def test_tampering_with_the_ciphertext_is_detected():
    plain = _zip_of_frames()
    ciphertext, nonce, tag, shares = seal_bytes(plain)
    tampered = bytearray(ciphertext)
    tampered[len(tampered) // 2] ^= 0x01
    key = Shamir.combine(shares[:2])
    with pytest.raises(ValueError):
        AES.new(key, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(bytes(tampered), tag)


def test_the_wrong_nonce_fails_to_verify():
    plain = _zip_of_frames()
    ciphertext, _nonce, tag, shares = seal_bytes(plain)
    key = Shamir.combine(shares[:2])
    with pytest.raises(ValueError):
        AES.new(key, AES.MODE_GCM, nonce=get_random_bytes(16)).decrypt_and_verify(
            ciphertext, tag
        )


def test_every_seal_uses_a_fresh_key_and_nonce():
    """Two clips sealed from identical frames must not look identical."""
    plain = _zip_of_frames()
    first = seal_bytes(plain)
    second = seal_bytes(plain)
    assert first[0] != second[0], "identical ciphertext means a reused key and nonce"
    assert first[1] != second[1]


def test_the_decrypted_clip_is_a_readable_zip():
    plain = _zip_of_frames(7)
    ciphertext, nonce, tag, shares = seal_bytes(plain)
    key = Shamir.combine(shares[:2])
    recovered = AES.new(key, AES.MODE_GCM, nonce=nonce).decrypt_and_verify(ciphertext, tag)
    with zipfile.ZipFile(io.BytesIO(recovered)) as zf:
        assert json.loads(zf.read("meta.json"))["n_frames"] == 7
        assert len([n for n in zf.namelist() if n.startswith("frames/")]) == 7
