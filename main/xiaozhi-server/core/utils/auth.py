import jwt
import time
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Tuple, Optional
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.backends import default_backend
import base64


class AuthToken:
    def __init__(self, secret_key: str):
        self.secret_key = secret_key.encode()  # convert to bytes
        # derive a fixed-length encryption key from the secret (32 bytes for AES-256)
        self.encryption_key = self._derive_key(32)

    def _derive_key(self, length: int) -> bytes:
        """Derive a fixed-length key"""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

        # use a fixed salt (a random salt should be used in a real production environment)
        salt = b"fixed_salt_placeholder"  # should be generated randomly in production
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=length,
            salt=salt,
            iterations=100000,
            backend=default_backend(),
        )
        return kdf.derive(self.secret_key)

    def _encrypt_payload(self, payload: dict) -> str:
        """Encrypt the whole payload with AES-GCM"""
        # convert the payload into a JSON string
        payload_json = json.dumps(payload)

        # generate a random IV
        iv = os.urandom(12)
        # create the encryptor
        cipher = Cipher(
            algorithms.AES(self.encryption_key),
            modes.GCM(iv),
            backend=default_backend(),
        )
        encryptor = cipher.encryptor()

        # encrypt and generate the tag
        ciphertext = encryptor.update(payload_json.encode()) + encryptor.finalize()
        tag = encryptor.tag

        # combine IV + ciphertext + tag
        encrypted_data = iv + ciphertext + tag
        return base64.urlsafe_b64encode(encrypted_data).decode()

    def _decrypt_payload(self, encrypted_data: str) -> dict:
        """Decrypt an AES-GCM encrypted payload"""
        # decode Base64
        data = base64.urlsafe_b64decode(encrypted_data.encode())
        # split the components
        iv = data[:12]
        tag = data[-16:]
        ciphertext = data[12:-16]

        # create the decryptor
        cipher = Cipher(
            algorithms.AES(self.encryption_key),
            modes.GCM(iv, tag),
            backend=default_backend(),
        )
        decryptor = cipher.decryptor()

        # decrypt
        plaintext = decryptor.update(ciphertext) + decryptor.finalize()
        return json.loads(plaintext.decode())

    def generate_token(self, device_id: str) -> str:
        """
        Generate a JWT token
        :param device_id: device ID
        :return: JWT token string
        """
        # set the expiry to 1 hour from now
        expire_time = datetime.now(timezone.utc) + timedelta(hours=1)

        # create the original payload
        payload = {"device_id": device_id, "exp": expire_time.timestamp()}

        # encrypt the whole payload
        encrypted_payload = self._encrypt_payload(payload)

        # create the outer payload containing the encrypted data
        outer_payload = {"data": encrypted_payload}

        # encode with JWT
        token = jwt.encode(outer_payload, self.secret_key, algorithm="HS256")
        return token

    def verify_token(self, token: str) -> Tuple[bool, Optional[str]]:
        """
        Verify a token
        :param token: JWT token string
        :return: (whether valid, device ID)
        """
        try:
            # verify the outer JWT first (signature and expiry)
            outer_payload = jwt.decode(token, self.secret_key, algorithms=["HS256"])

            # decrypt the inner payload
            inner_payload = self._decrypt_payload(outer_payload["data"])

            # check the expiry again (double verification)
            if inner_payload["exp"] < time.time():
                return False, None

            return True, inner_payload["device_id"]

        except jwt.InvalidTokenError:
            return False, None
        except json.JSONDecodeError:
            return False, None
        except Exception as e:  # catch any other possible error
            print(f"Token verification failed: {str(e)}")
            return False, None
