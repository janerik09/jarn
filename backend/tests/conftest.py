import base64
import os

# Settings are read at import time. Give the tests a valid key so sealing can be exercised.
os.environ.setdefault("CREDENTIAL_ENCRYPTION_KEY", base64.urlsafe_b64encode(os.urandom(32)).decode())
