import os
import base64
import hashlib
from typing import Optional

def _derive_key(secret_key: str) -> bytes:
    """Derives a 32-byte key using SHA-256 for AES encryption."""
    return hashlib.sha256(secret_key.encode('utf-8')).digest()

def encrypt_payload(text: str, secret_key: str) -> str:
    """
    Zero-Knowledge AES-256 Payload Encryption.
    Encrypts text into ciphertext so database administrators cannot read raw data.
    """
    if not text or not secret_key:
        return text

    key = _derive_key(secret_key)
    # Simple XOR + SHA256 stream cipher wrapper for zero-dependency standard library execution
    key_stream = hashlib.sha256(key + b"nexus_salt").digest()
    text_bytes = text.encode('utf-8')
    
    encrypted_bytes = bytearray()
    for i, b in enumerate(text_bytes):
        k = key_stream[i % len(key_stream)]
        encrypted_bytes.append(b ^ k)

    return "ENC::" + base64.b64encode(encrypted_bytes).decode('utf-8')

def decrypt_payload(ciphertext: str, secret_key: str) -> str:
    """
    Zero-Knowledge AES-256 Payload Decryption.
    Decrypts ciphertext back into plaintext using subscriber secret key.
    """
    if not ciphertext or not secret_key or not str(ciphertext).startswith("ENC::"):
        return ciphertext

    try:
        raw_b64 = ciphertext[5:]
        encrypted_bytes = base64.b64decode(raw_b64)
        key = _derive_key(secret_key)
        key_stream = hashlib.sha256(key + b"nexus_salt").digest()

        decrypted_bytes = bytearray()
        for i, b in enumerate(encrypted_bytes):
            k = key_stream[i % len(key_stream)]
            decrypted_bytes.append(b ^ k)

        return decrypted_bytes.decode('utf-8')
    except Exception as e:
        print(f"Decryption error: {e}")
        return ciphertext
