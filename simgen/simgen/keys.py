"""Secret keys: Ki, OP/OPc, and transport encryption.

Ki   = the subscriber's 128-bit secret key. Lives on the SIM and in the
       operator's authentication centre (AuC/HSS). Never leaves either in clear.
OP   = an operator-wide 128-bit secret.
OPc  = a per-card value derived from OP and Ki (3GPP TS 35.206, Milenage):
           OPc = AES-128(key=Ki, data=OP) XOR OP
       Storing OPc on the card means OP itself is never written to a SIM.

Transport keys: when generated keys travel in a file (to the operator or to
the personalization factory), they are encrypted with a key agreed in advance.
In production that key lives in an HSM and is exchanged in a key ceremony.
In this lab it lives in a JSON file marked LAB ONLY.
"""

import json
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def _aes_block(key: bytes, block: bytes) -> bytes:
    # One 16-byte block with AES. ECB on a single random block is how many
    # SIM output files carry encrypted keys; never use ECB for longer data.
    enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return enc.update(block) + enc.finalize()


def compute_opc(ki: bytes, op: bytes) -> bytes:
    """Milenage OPc derivation. Checked against 3GPP TS 35.208 test set 1."""
    if len(ki) != 16 or len(op) != 16:
        raise ValueError("Ki and OP must be 16 bytes")
    return bytes(a ^ b for a, b in zip(_aes_block(ki, op), op))


def encrypt_for_transport(transport_key: bytes, secret: bytes) -> str:
    """Encrypt a 16-byte secret with a transport key; returns upper-case hex."""
    return _aes_block(transport_key, secret).hex().upper()


def decrypt_from_transport(transport_key: bytes, hex_value: str) -> bytes:
    dec = Cipher(algorithms.AES(transport_key), modes.ECB()).decryptor()
    return dec.update(bytes.fromhex(hex_value)) + dec.finalize()


class KeyStore:
    """Loads lab keys: the operator OP, transport keys by id, and the perso key."""

    def __init__(self, op: bytes, transport_keys: dict, perso_key: bytes):
        self.op = op
        self.transport_keys = transport_keys
        self.perso_key = perso_key

    @classmethod
    def load(cls, path) -> "KeyStore":
        data = json.loads(Path(path).read_text())
        return cls(
            op=bytes.fromhex(data["op"]),
            transport_keys={k: bytes.fromhex(v) for k, v in data["transport_keys"].items()},
            perso_key=bytes.fromhex(data["perso_key"]),
        )

    def transport_key(self, key_id: str) -> bytes:
        try:
            return self.transport_keys[key_id]
        except KeyError:
            raise KeyError(f"unknown transport key id {key_id!r}") from None


def write_lab_keys(path) -> None:
    """Create a fresh random key file for the lab. Never use these for anything real."""
    data = {
        "_warning": "LAB ONLY. Synthetic keys for the telecom-data-ops-lab. Never reuse.",
        "op": secrets.token_hex(16).upper(),
        "transport_keys": {"TK01": secrets.token_hex(16).upper()},
        "perso_key": secrets.token_hex(16).upper(),
    }
    p = Path(path)
    p.write_text(json.dumps(data, indent=2) + "\n")
    p.chmod(0o600)
