"""L1: one canonical spelling per page."""

from __future__ import annotations

import re
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PREFIXES: Final = ("utm_", "mc_", "pk_", "ga_")
_TRACKING_KEYS: Final = frozenset(
    {
        "gclid",
        "fbclid",
        "msclkid",
        "dclid",
        "yclid",
        "igshid",
        "ref",
        "ref_src",
        "amp",
        "outputtype",
        "cmpid",
        "ocid",
        "sr_share",
        "spm",
        "_ga",
        "si",
    }
)
_MOBILE_HOST = re.compile(r"^(?:m|mobile|amp)\.")
_AMP_SUFFIX = re.compile(r"(?:/amp/?|\.amp)$", re.IGNORECASE)


def canonicalize_url(raw: str) -> str | None:
    """Canonical https URL, or None for anything that is not a web page address.

    Scheme is unified to https (the same page is often indexed under both), `www.` and mobile/AMP
    subdomains are dropped, tracking parameters removed, remaining parameters sorted, fragments
    and trailing slashes discarded. Path case is kept: it is significant on many servers.
    """
    try:
        parts = urlsplit(raw.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return None

    host = parts.hostname.lower().removeprefix("www.")
    host = _MOBILE_HOST.sub("", host)
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"

    path = _AMP_SUFFIX.sub("", parts.path) or "/"
    if len(path) > 1:
        path = path.rstrip("/") or "/"

    query = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=False)
        if not key.lower().startswith(_TRACKING_PREFIXES) and key.lower() not in _TRACKING_KEYS
    )
    return urlunsplit(("https", netloc, path, urlencode(query), ""))


def domain_of(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return _MOBILE_HOST.sub("", host.lower().removeprefix("www."))
