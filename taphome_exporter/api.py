"""Read-only TapHome Core client: two GET endpoints, no writes."""

import json
import urllib.request


def fetch(url: str, token: str, endpoint: str, timeout: float = 10) -> dict:
    req = urllib.request.Request(
        f"{url.rstrip('/')}/{endpoint}", headers={"Authorization": f"TapHome {token}"}
    )
    # Raises HTTPError (401 on a wrong token), URLError or TimeoutError; the poller records it.
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)
