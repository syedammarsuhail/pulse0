# Pulse0 Enterprise Module

This directory contains proprietary code for Pulse0 Pro features:

- **License key validation** — HMAC-based offline verification
- **SSO/OIDC gate** — enables single sign-on when a valid key is present
- **Key generation** — `python -m enterprise.keygen`

## License

This module is **NOT** covered by the BSL license. It is proprietary software
owned by Pulse0 Inc. A valid Pulse0 Pro license key is required.

## For the public repo

When publishing the open-source version, rename `.gitignore-public` to
`.gitignore` in this directory. The enterprise module will be absent, and
server.py will gracefully fall back to community tier.
