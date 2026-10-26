"""The shipped Shadowrocket module must cover every API node, and nothing else.

Two hard constraints pull in opposite directions:

* The game balances MySekai traffic across numbered API nodes, and picks per
  request, so covering one node silently drops captures.
* Decrypting the whole publisher domain breaks the game. ``edf6627`` narrowed
  ``*.dailygn.com`` to one host precisely because the module made the game
  unreachable: login and anti-cheat SDKs live on sibling subdomains.

The module therefore pins the MySekai API host family by wildcard and matches
the script pattern on path, so a new node needs no pattern change while the
SDK subdomains stay encrypted end-to-end.
"""

from pathlib import Path

TEMPLATE = (
    Path(__file__).resolve().parents[1] / "app" / "templates" / "mysekai.sgmodule"
)
TEXT = TEMPLATE.read_text(encoding="utf-8")


def _line(prefix: str) -> str:
    for line in TEXT.splitlines():
        if line.startswith(prefix):
            return line
    raise AssertionError(f"module declares no line starting with {prefix!r}")


def test_mitm_hostname_covers_the_mysekai_node_family():
    hostname = _line("hostname")
    assert "mkcn-prod-public-60001-*.dailygn.com" in hostname


def test_mitm_hostname_does_not_decrypt_the_whole_publisher_domain():
    hostname = _line("hostname")
    assert "*.dailygn.com" not in hostname.replace(
        "mkcn-prod-public-60001-*.dailygn.com", ""
    ), "decrypting every dailygn subdomain breaks login and anti-cheat SDKs"


def test_mitm_hostname_does_not_pin_a_single_node():
    hostname = _line("hostname")
    assert "mkcn-prod-public-60001-1.dailygn.com" not in hostname


def test_script_patterns_match_by_path_not_host():
    for prefix in ("mysekai-capture", "mysekai-convert"):
        line = _line(prefix)
        assert "/api/user/\\d+/mysekai" in line
        assert "dailygn" not in line
        assert "volcalb" not in line
