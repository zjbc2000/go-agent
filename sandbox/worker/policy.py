"""Network policy: the sandbox's deny-by-default egress guard.

``SandboxPolicy.validate_url(url)`` resolves ALL addresses behind a URL
(A/AAAA/CNAME chains) and rejects any non-global address — loopback, private
ranges, link-local, and the 169.254.169.254 cloud metadata endpoint.
The broker is the sandbox's ONLY external channel, so this policy is the
application-level egress guard; the container-level network policy (iptables/
nftables) adds a second layer at the OCI boundary.

IPv4-mapped-IPv6 addresses (::ffff:x.x.x.x) are checked: a mapped address whose
embedded v4 is non-global is rejected.
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
    # IPv4
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
    # IPv6
    ipaddress.IPv6Network("::1/128"),             # loopback
    ipaddress.IPv6Network("::/128"),              # unspecified
    ipaddress.IPv6Network("fe80::/10"),           # link-local
    ipaddress.IPv6Network("fc00::/7"),            # unique local
    ipaddress.IPv6Network("2001:db8::/32"),       # documentation
    ipaddress.IPv6Network("2001:10::/28"),        # deprecated (ORCHID)
    # IPv4-mapped-IPv6: the IPv4 address embedded in ::ffff:x.x.x.x is checked
    # separately via ipv4_mapped below; listing ::ffff:0:0/96 here would reject
    # ALL mapped addresses including public ones, so we check per-address.
    #
    # IPv4-compatible-IPv6 (::/96, excluding ::/128 which is already listed):
    # addresses like ::127.0.0.1 embed an IPv4 address too, but ipv4_mapped is
    # None for these — ipaddress calls them "IPv4-compatible". We reject the
    # entire ::/96 range (minus ::1/128 which is already covered) because the
    # embedded v4 is checked per-address below.
    ipaddress.IPv6Network("::/96"),             # IPv4-compatible (checked per-addr)
    # NEW #5: same-family IPv4-embedding ranges — 6to4, NAT64, Teredo.
    ipaddress.IPv6Network("2002::/16"),          # 6to4 (embeds IPv4)
    ipaddress.IPv6Network("64:ff9b::/96"),       # NAT64 well-known prefix
    ipaddress.IPv6Network("64:ff9b:1::/48"),     # NAT64 (reserved)
    ipaddress.IPv6Network("2001::/32"),          # Teredo
]


def _is_global(addr: str) -> bool:
    """True when an address is globally routable (not loopback/private/link-local).

    IPv4-mapped-IPv6 (::ffff:x.x.x.x → ipv4_mapped) and IPv4-compatible-IPv6
    (::x.x.x.x → ipv4_mapped is None but the address is in ::/96) both have
    their embedded IPv4 portion checked.
    """
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False

    if isinstance(ip, ipaddress.IPv6Address):
        # IPv4-mapped-IPv6: the canonical way py 3.x exposes ::ffff:x.x.x.x.
        if ip.ipv4_mapped is not None:
            return _is_global(str(ip.ipv4_mapped))
        # IPv4-compatible-IPv6: ::x.x.x.x (::/96). ipv4_mapped is None for
        # these; extract the last 32 bits as an IPv4 address.
        if _is_ipv4_compatible(ip):
            embedded = str(ipaddress.IPv4Address(ip.packed[-4:]))
            return _is_global(embedded)

    for net in _NON_GLOBAL_NETS:
        if ip in net:
            return False
    return True


def _is_ipv4_compatible(ip: ipaddress.IPv6Address) -> bool:
    """True when an IPv6 address is an IPv4-compatible address (::/96).

    An IPv4-compatible address has the form ::x.x.x.x — the upper 96 bits
    are zero and it is NOT the unspecified address (::).
    """
    if ip == ipaddress.IPv6Address("::"):
        return False
    return ip.packed[:12] == b"\x00" * 12


def _resolve_all(hostname: str) -> list[str]:
    """Resolve a hostname to all its A and AAAA addresses, following CNAME chains.

    Returns the list of IP address strings. ``socket.getaddrinfo`` follows
    CNAME chains and returns both IPv4 and IPv6 results.
    """
    try:
        infos = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise PolicyDenied("SANDBOX_NETWORK_DENIED", "Cannot resolve target hostname.")
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

        # Check the raw hostname for IP literals including IPv6 bracket notation
        # (e.g. http://[::ffff:127.0.0.1]/).
        try:
            ipaddress.ip_address(hostname)
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
