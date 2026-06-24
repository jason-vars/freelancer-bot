from __future__ import annotations

from freelancersdk.session import Session

def make_session(oauth_token: str, url: str | None = None) -> Session:
    # The official SDK supports passing a base URL (e.g. sandbox) if needed.
    if url:
        return Session(oauth_token=oauth_token, url=url)
    return Session(oauth_token=oauth_token)
