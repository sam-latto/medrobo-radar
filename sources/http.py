"""Shared HTTP session with per-host throttling for the public data APIs."""
import threading
import time
from urllib.parse import urlparse

import requests

DEFAULT_TIMEOUT = 30

# Minimum seconds between requests to a host (documented public rate limits)
_MIN_INTERVAL = {
    "efts.sec.gov": 0.15,        # SEC: <= 10 requests/second
    "www.sec.gov": 0.15,
    "export.arxiv.org": 3.0,     # arXiv: ~1 request / 3 seconds
    "eutils.ncbi.nlm.nih.gov": 0.35,  # NCBI: 3/sec without an API key
    "api.fda.gov": 0.25,         # openFDA: 240/minute
    "clinicaltrials.gov": 0.2,
}

_session = requests.Session()
_lock = threading.Lock()
_last_request: dict[str, float] = {}


def _throttle(host: str) -> None:
    interval = _MIN_INTERVAL.get(host, 0)
    with _lock:
        wait = _last_request.get(host, 0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request[host] = time.monotonic()


def get(url: str, params: dict | None = None, headers: dict | None = None,
        timeout: float = DEFAULT_TIMEOUT) -> requests.Response:
    _throttle(urlparse(url).netloc)
    response = _session.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response
