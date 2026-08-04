"""freelancer-bid-bot package.

macOS + python.org Python ships without a usable CA certificate store, so every
HTTPS call (Freelancer API, OpenAI, Telegram) can die with:

    ssl.SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED] certificate
    verify failed: unable to get local issuer certificate

Fix it once, for the WHOLE process, before any networking library is imported:

  1. Prefer ``truststore`` -> patch stdlib ``ssl`` to verify against the OS trust
     store (macOS keychain / Windows cert store). This is the project's preferred
     fix (see telegram_notify.py) and also survives antivirus/proxy SSL
     interception. It makes requests, httpx AND stdlib urllib all use the OS roots.
  2. Fall back to pointing the process at ``certifi``'s CA bundle via the env vars
     OpenSSL/requests read, for environments without truststore.

Both are best-effort: a failure here must never block startup.
"""
from __future__ import annotations

import os

try:
    import truststore  # OS trust store -> most robust, project-preferred

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001 - fall back to certifi below
    try:
        import certifi

        _ca = certifi.where()
        if _ca and os.path.exists(_ca):
            os.environ.setdefault("SSL_CERT_FILE", _ca)       # stdlib ssl / urllib
            os.environ.setdefault("REQUESTS_CA_BUNDLE", _ca)  # requests
    except Exception:  # noqa: BLE001 - certificate wiring must never block startup
        pass
