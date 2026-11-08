"""Pulse0 Enterprise — SSO/OIDC + RBAC gate.

When this module is present and a valid license key is set, the server's
built-in SSO/OIDC/RBAC/audit code activates.  Without the enterprise module
(i.e. in the public open-source repo), those features are inert.

The actual SSO implementation lives in server.py so that the single-file
deployment stays simple.  This module's job is to validate the license and
signal that enterprise features should be enabled.
"""


def setup_sso(app, config):
    """Called by server.py at startup when enterprise is available + licensed.

    Currently a no-op (the SSO code lives in server.py behind IS_PRO gates).
    Future versions may register additional enterprise-only endpoints here.
    """
    pass
