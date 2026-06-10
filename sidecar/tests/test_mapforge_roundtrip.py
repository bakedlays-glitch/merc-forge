"""B0 round-trip harness — diagnostic-logic tests.

The full corpus sweep is an ON-DEMAND script (`tools/roundtrip_audit.py`,
which needs the JA2 installs mounted). These tests lock the harness's
divergence CLASSIFIER + first-diff finder so its diagnostics stay
trustworthy without a real corpus in CI. `tools/` isn't a package, so the
module is loaded by path.
"""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "roundtrip_audit",
    Path(__file__).resolve().parents[1] / "tools" / "roundtrip_audit.py",
)
rta = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rta)


# ── first-diff finder ──────────────────────────────────────────────────────


def test_first_diff_equal_returns_minus_one():
    assert rta._first_diff(b"abc", b"abc") == -1


def test_first_diff_mid():
    assert rta._first_diff(b"abcXe", b"abcde") == 3


def test_first_diff_prefix_returns_shorter_len():
    # One a prefix of the other → the divergence is at the shorter length
    # (a truncated/extended re-encode shows up exactly there).
    assert rta._first_diff(b"abc", b"abcde") == 3
    assert rta._first_diff(b"abcde", b"abc") == 3


# ── region map + classifier ────────────────────────────────────────────────


def _fake_parsed(header_len=25, rows=2, cols=2, appendix_offset=None):
    wm = rows * cols
    layers = header_len + 2 * wm + 4 * wm
    if appendix_offset is None:
        appendix_offset = layers + 10  # pretend 10 bytes of layer+room data
    return {"header_len": header_len, "rows": rows, "cols": cols,
            "appendix_offset": appendix_offset}


def test_regions_are_contiguous_and_ordered():
    regs = rta._regions(_fake_parsed())
    assert [r[0] for r in regs] == [
        "header", "heights", "layer-counts", "layer+room", "appendix",
    ]
    # Each region starts exactly where the previous ended (no gaps/overlap).
    for (_, _, e_prev), (_, s_next, _) in zip(regs, regs[1:]):
        assert e_prev == s_next
    assert regs[-1][2] is None  # appendix runs to EOF


def test_classify_maps_offsets_to_regions():
    p = _fake_parsed(header_len=25, rows=2, cols=2)
    # header [0,25) heights [25,33) layer-counts [33,49) layer+room [49,59)
    # appendix [59, EOF)
    assert rta._classify(p, 0) == "header"
    assert rta._classify(p, 24) == "header"
    assert rta._classify(p, 25) == "heights"
    assert rta._classify(p, 32) == "heights"
    assert rta._classify(p, 33) == "layer-counts"
    assert rta._classify(p, 48) == "layer-counts"
    assert rta._classify(p, 49) == "layer+room"
    assert rta._classify(p, 58) == "layer+room"
    assert rta._classify(p, 59) == "appendix"
    assert rta._classify(p, 10_000) == "appendix"


# ── end-to-end on a non-map ────────────────────────────────────────────────


def test_roundtrip_one_reports_parse_error_on_garbage():
    r = rta.roundtrip_one("junk", b"\x00\x01\x02")
    assert r["status"] == "parse_error"
    assert r["map"] == "junk"


def test_is_map_path_filters_to_maps_dirs():
    assert rta._is_map_path(Path("Data-1.13/Maps/A9.DAT"))
    assert rta._is_map_path(Path("Data/Maps/n7.dat"))
    # A .dat that isn't under a Maps/ dir (e.g. Ja2Set.dat) is not a map.
    assert not rta._is_map_path(Path("Data/Ja2Set.dat"))
    assert not rta._is_map_path(Path("Data/Tilesets/foo.dat"))
