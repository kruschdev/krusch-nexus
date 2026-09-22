import os
import sys
import unittest

# Set Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.backend.crypto_utils import encrypt_payload, decrypt_payload

class TestZeroKnowledgeCrypto(unittest.TestCase):

    def test_encryption_decryption(self):
        secret_key = "nx_live_secret_customer_key_123"
        original_text = "CONFIDENTIAL FINANCIAL REPORT: Q2 Profit $4.2M, Tax ID 99-88776655."

        ciphertext = encrypt_payload(original_text, secret_key)
        self.assertTrue(ciphertext.startswith("ENC::"))
        self.assertNotIn("CONFIDENTIAL", ciphertext)
        self.assertNotIn("4.2M", ciphertext)

        decrypted_text = decrypt_payload(ciphertext, secret_key)
        self.assertEqual(decrypted_text, original_text)

    def test_wrong_key_fails_decryption(self):
        secret_key = "correct_customer_key"
        wrong_key = "attacker_or_admin_key"
        original_text = "Internal SOP: Secret Refund Code 9988."

        ciphertext = encrypt_payload(original_text, secret_key)
        decrypted_text = decrypt_payload(ciphertext, wrong_key)

        self.assertNotEqual(decrypted_text, original_text)

    def test_unencrypted_text_pass_through(self):
        plaintext = "Regular unencrypted text"
        res = decrypt_payload(plaintext, "any_key")
        self.assertEqual(res, plaintext)

if __name__ == '__main__':
    unittest.main()
