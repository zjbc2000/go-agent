"""Network policy: the sandbox's deny-by-default egress guard.

``SandboxPolicy.validate_url(url)`` resolves ALL addresses behind a URL
(A/AAAA/CNAME chains) and rejects any non-global address — loopback, private
ranges, link-local, and the 169.254.169.254 cloud metadata endpoint.
The broker is the sandbox's ONLY external channel, so this policy is the
application-level egress guard; the container-level network policy (iptables/
nftables) adds a second layer at the OCI boundary.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class PolicyDenied(Exception):
    """Raised when a URL resolves to a non-public address."""

    code: str = "SANDBOX_NETWORK_DENIED"
    message: str = "Target address is not public."


# Address ranges that must never be reachable from a sandbox container.
_NON_GLOBAL_NETS = [
    ipaddress.IPv4Network("127.0.0.0/8"),       # loopback
    ipaddress.IPv4Network("10.0.0.0/8"),         # private
    ipaddress.IPv4Network("172.16.0.0/12"),      # private
    ipaddress.IPv4Network("192.168.0.0/16"),     # private
    ipaddress.IPv4Network("169.254.0.0/16"),     # link-local (incl. 169.254.169.254)
    ipaddress.IPv4Network("0.0.0.0/8"),          # "this" network
    ipaddress.IPv4Network("100.64.0.0/10"),      # carrier-grade NAT
    ipaddress.IPv4Network("192.0.0.0/24"),       # IETF protocol assignments
    ipaddress.IPv4Network("192.0.2.0/24"),       # TEST-NET-1
    ipaddress.IPv4Network("198.18.0.0/15"),      # benchmark
    ipaddress.IPv4Network("198.51.100.0/24"),    # TEST-NET-2
    ipaddress.IPv4Network("203.0.113.0/24"),     # TEST-NET-3
    ipaddress.IPv4Network("224.0.0.0/4"),        # multicast
    ipaddress.IPv4Network("240.0.0.0/4"),        # reserved
    ipaddress.IPv6Network("::1/128"),             # loopback
    ipaddress.IPv6Network("fe80::/10"),           # link-local
    ipaddress.IPv6Network("fc00::/7"),            # unique local
]


def _is_global(addr: str) -> bool:
    """True when an address is globally routable (not loopback/private/link-local)."""
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    for net in _NON_GLOBAL_NETS:
        if ip in net:
            return False
    return True


def _resolve_all(hostname: str) -> list[str]:
    """Resolve a hostname to all its A and AAAA addresses, following CNAME chains.

    Returns the list of IP address strings. ``socket.getaddrinfo`` follows
    CNAME chains and returns both IPv4 and IPv6 results.
    """
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise PolicyDenied("SANDBOX_NETWORK_DENIED", "Cannot resolve target hostname.")
    # getaddrinfo returns (family, type, proto, canonname, sockaddr); sockaddr[0] is the IP.
    addresses: list[str] = []
    seen: set[str] = set()
    for info in infos:
        addr = info[4][0]
        if addr not in seen:
            seen.add(addr)
            addresses.append(addr)
    return addresses


class SandboxPolicy:
    """The sandbox's outbound network policy: deny-by-default egress."""

    def validate_url(self, url: str) -> None:
        """Resolve all addresses behind ``url``; raise PolicyDenied for any non-global target.

        Accepts a URL string (``http://...`` or ``https://...``). Parses the
        hostname, resolves all A/AAAA/CNAME chains, and rejects if any resolved
        address is not globally routable.
        """
        try:
            parsed = urlparse(url)
        except Exception:
            raise PolicyDenied("SANDBOX_NETWORK_DENIED", "Invalid URL.")
        if parsed.scheme not in ("http", "https"):
            raise PolicyDenied("SANDBOX_NETWORK_DENIED", "Only http/https URLs are allowed.")
        hostname = parsed.hostname
        if not hostname:
            raise PolicyDenied("SANDBOX_NETWORK_DENIED", "URL has no hostname.")

        # Check the raw hostname for IP literals (e.g. http://127.0.0.1/).
        try:
            _ip = ipaddress.ip_address(hostname)
            if not _is_global(hostname):
                raise PolicyDenied("SANDBOX_NETWORK_DENIED", "Target address is not public.")
            return
        except ValueError:
            pass  # Not an IP literal — resolve via DNS.

        addresses = _resolve_all(hostname)
        if not addresses:
            raise PolicyDenied("SANDBOX_NETWORK_DENIED", "Target hostname resolved to no addresses.")
        for addr in addresses:
            if not _is_global(addr):
                raise PolicyDenied("SANDBOX_NETWORK_DENIED", "Target address is not public.")
