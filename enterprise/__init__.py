"""Pulse0 Enterprise — SSO/OIDC, RBAC, audit log, and license validation.

This module is proprietary. It is NOT covered by the BSL license that applies
to the rest of the Pulse0 codebase.  Distribution, copying, or use of this
module requires a valid Pulse0 Pro license key.

Copyright (c) 2024-2026 Pulse0 Inc.  All rights reserved.
"""
from enterprise.license import validate_license, LICENSE_OK  # noqa: F401
from enterprise.sso import setup_sso  # noqa: F401
