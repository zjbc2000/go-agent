"""Network policy tests: the sandbox must never reach non-public addresses.

The broker is the sandbox's ONLY external channel. ``SandboxPolicy.validate_url``
resolves all addresses (A/AAAA/CNAME chains) and rejects any non-global target
— loopback, private ranges, link-local, and 169.254.169.254 metadata.

These are the exact parametrized tests from the Task-3 brief.
"""

import pytest
from worker.policy import PolicyDenied, SandboxPolicy


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1",
        "http://169.254.169.254",
        "http://10.0.0.1",
    ],
)
def test_network_policy_rejects_non_public_targets(url: str):
    with pytest.raises(PolicyDenied):
        SandboxPolicy().validate_url(url)


def test_private_ip_ranges_rejected():
    """Additional non-global addresses from common private ranges."""
    blocked = [
        "http://192.168.1.1",
        "http://172.16.0.1",
        "http://[::1]",
        "http://[fe80::1]",
    ]
    for url in blocked:
        with pytest.raises(PolicyDenied):
            SandboxPolicy().validate_url(url)


def test_public_urls_pass():
    """Well-known public addresses should pass validation (no DNS needed for these).
    These are IP literals that are globally routable.
    """
    # 8.8.8.8 (Google DNS) and 1.1.1.1 (Cloudflare DNS) are globally routable.
    SandboxPolicy().validate_url("http://8.8.8.8")
    SandboxPolicy().validate_url("http://1.1.1.1")


def test_dns_chain_that_resolves_to_private_is_denied():
    """localhost always resolves to 127.0.0.1 or ::1 — must be denied."""
    with pytest.raises(PolicyDenied):
        SandboxPolicy().validate_url("http://localhost")


def test_invalid_urls_rejected():
    """Garbage / non-URL input must be rejected cleanly."""
    with pytest.raises(PolicyDenied):
        SandboxPolicy().validate_url("not-a-url")
    with pytest.raises(PolicyDenied):
        SandboxPolicy().validate_url("")
