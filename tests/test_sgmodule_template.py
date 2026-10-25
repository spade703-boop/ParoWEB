"""The shipped Shadowrocket module must cover every node the game hands out.

The API host rotates between numbered nodes (``...-1``, ``...-2`` ...) and has
also been seen under an Alibaba ALB domain. A module that names one host only
silently stops intercepting when the game lands elsewhere, which looks exactly
like "the watch stopped working" (see the 2026-10-21 MITM narrowing in git).

The script pattern therefore matches on path, not host, so a newly discovered
node only needs a MITM hostname entry, not a pattern change.
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


def test_mitm_hostname_covers_every_known_node_family():
    hostname = _line("hostname")
    assert "*.dailygn.com" in hostname
    assert "*.volcalb.com" in hostname


def test_mitm_hostname_does_not_pin_a_single_node():
    hostname = _line("hostname")
    assert "mkcn-prod-public-60001-1.dailygn.com" not in hostname


def test_script_pattern_matches_by_path_not_host():
    pattern_line = _line("mysekai-capture")
    assert "/api/user/\\d+/mysekai" in pattern_line
    assert "dailygn" not in pattern_line
    assert "volcalb" not in pattern_line


def test_convert_pattern_matches_by_path_not_host():
    pattern_line = _line("mysekai-convert")
    assert "/api/user/\\d+/mysekai/convert/" in pattern_line
    assert "dailygn" not in pattern_line
    assert "volcalb" not in pattern_line
