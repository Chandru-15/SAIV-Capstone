"""Web Crypto wire-format interoperability and malformed credential checks."""

import json
import shutil
import subprocess

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.services.browser_verification import (
    BrowserProofError, base64url, parse_public_key, verify_signature,
)
from app.services.exceptions import ValidationError


def test_real_web_crypto_signature_verifies_in_python():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the Web Crypto interoperability vector")
    message = '["saiv.browser-proof.v1","checkin","one-time"]\nactual-body-digest'
    script = """
    (async () => {
      const { subtle } = require('node:crypto').webcrypto;
      const algorithm = { name: 'ECDSA', namedCurve: 'P-256' };
      const pair = await subtle.generateKey(algorithm, false, ['sign', 'verify']);
      const publicKey = await subtle.exportKey('spki', pair.publicKey);
      const signature = await subtle.sign({ name: 'ECDSA', hash: 'SHA-256' },
        pair.privateKey, new TextEncoder().encode(process.argv[1]));
      const encode = value => Buffer.from(value).toString('base64url');
      console.log(JSON.stringify({ publicKey: encode(publicKey), signature: encode(signature),
        extractable: pair.privateKey.extractable, bytes: signature.byteLength }));
    })().catch(error => { console.error(error); process.exitCode = 1; });
    """
    vector = json.loads(subprocess.check_output([node, "-e", script, message], text=True, timeout=15))
    assert vector["extractable"] is False
    assert vector["bytes"] == 64
    key, _ = parse_public_key(vector["publicKey"])
    verify_signature(key, vector["signature"], message)
    for signature, payload in [(vector["signature"], message + " "), (vector["signature"] + "=", message)]:
        with pytest.raises(BrowserProofError):
            verify_signature(key, signature, payload)


def test_rejects_malformed_keys_and_wrong_curve():
    other_curve = ec.generate_private_key(ec.SECP384R1()).public_key()
    wrong_key = other_curve.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    for value in ("~" * 122, base64url(b"\0" * 91), base64url(wrong_key)):
        with pytest.raises(ValidationError):
            parse_public_key(value)
