from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "metadata.google.internal",
        "metadata",
    }
)


class UnsafeUrlError(ValueError):
    pass


def _is_blocked_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _resolve_host_ips(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"Cannot resolve host: {hostname}") from exc

    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip not in ips:
            ips.append(ip)
    return ips


def validate_fetch_url(url: str) -> None:
    """Reject URLs that could reach private networks or cloud metadata."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError("Only http and https URLs are allowed")

    hostname = (parsed.hostname or "").strip().lower().rstrip(".")
    if not hostname:
        raise UnsafeUrlError("URL must include a hostname")

    if hostname in _BLOCKED_HOSTNAMES:
        raise UnsafeUrlError(f"Blocked hostname: {hostname}")

    if hostname.endswith(".local") or hostname.endswith(".internal"):
        raise UnsafeUrlError(f"Blocked hostname suffix: {hostname}")

    for ip in _resolve_host_ips(hostname):
        if _is_blocked_ip(ip):
            raise UnsafeUrlError(f"Hostname resolves to blocked address: {ip}")
