"""Pairing crypto, Home Assistant's end (docs/protocol.md, "Pairing").

Must agree with the firmware's common/ohr_crypto.h; tests/vectors.json (a copy
of param-key's docs/test-vectors.json) pins both.
"""

from dataclasses import dataclass
import struct

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PAIR_SALT = b"param-key pair v2"


@dataclass(frozen=True)
class Pairing:
    """What a pairing exchange yields: HA's public key, the link key and the code."""

    ha_public: bytes
    key: bytes
    code: str  # 6 digits, as the remote shows it ("482913")


def mac_bytes(mac: str) -> bytes:
    """'AA:BB:CC:DD:EE:FF' -> 6 bytes."""
    return bytes.fromhex(mac.replace(":", ""))


def derive(shared: bytes, remote_public: bytes, ha_public: bytes, remote_mac: bytes) -> tuple[bytes, str]:
    """Link key and code from the X25519 shared secret."""
    okm = HKDF(algorithm=hashes.SHA256(), length=20, salt=PAIR_SALT,
               info=remote_public + ha_public + remote_mac).derive(shared)
    return okm[:16], f"{struct.unpack('>I', okm[16:20])[0] % 1_000_000:06d}"


def pair(remote_public: bytes, remote_mac: str, private: X25519PrivateKey | None = None) -> Pairing:
    """HA's side of pairing with a remote that sent this public key.

    Raises ValueError for a malformed or low-order public key.
    """
    if len(remote_public) != 32:
        raise ValueError("public key must be 32 bytes")
    private = private or X25519PrivateKey.generate()
    ha_public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    shared = private.exchange(X25519PublicKey.from_public_bytes(remote_public))
    key, code = derive(shared, remote_public, ha_public, mac_bytes(remote_mac))
    return Pairing(ha_public, key, code)


def format_code(code: str) -> str:
    """'482913' -> '482 913', as on the remote's screen."""
    return f"{code[:3]} {code[3:]}"
