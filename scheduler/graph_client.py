"""Microsoft Graph client-credentials (app-only) authentication.

One function, get_graph_token(), used by jobs/daily_digest.py to call
https://graph.microsoft.com/v1.0/users/{mailbox}/sendMail. Relies on MSAL's
own in-memory token cache inside the ConfidentialClientApplication instance:
acquire_token_for_client() returns the cached token without a network call
until it's close to its ~1 hour expiry, so callers can invoke get_graph_token()
freely without re-authenticating every time.
"""

import msal

import config

_GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]

_app: msal.ConfidentialClientApplication | None = None


def _get_app() -> msal.ConfidentialClientApplication:
    global _app
    if _app is None:
        _app = msal.ConfidentialClientApplication(
            client_id=config.GRAPH_CLIENT_ID,
            client_credential=config.GRAPH_CLIENT_SECRET,
            authority=f"https://login.microsoftonline.com/{config.GRAPH_TENANT_ID}",
        )
    return _app


def get_graph_token() -> str:
    result = _get_app().acquire_token_for_client(scopes=_GRAPH_SCOPE)
    if "access_token" not in result:
        raise RuntimeError(
            f"Kon geen Graph-token ophalen ({result.get('error')}): {result.get('error_description')}"
        )
    return result["access_token"]
