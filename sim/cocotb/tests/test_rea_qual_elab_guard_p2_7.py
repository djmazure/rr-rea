# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.7 / REA-REQ-957 — illegal storage-qualifier builds fail elaboration.

A qualified sample's index no longer says when it was taken, so
G_QUAL_CONDS > 0 without a timestamp plane is refused; and FEATURES[27:24]
carries the slot count, so more than 15 slots is refused. Both refusals are
static range violations (the RTL-P2.895 pattern) so no vendor tool can
downgrade them to a warning. The legal neighbours must still elaborate —
a guard that also rejects legal builds is a lockout, not a guard.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from sdk.cocotb_helpers import requires

_RTL = Path(__file__).resolve().parents[3] / "rtl"
_FIX = Path(__file__).resolve().parent / "fixtures"
_SOURCES = [
    _RTL / "rr_rea_pkg.vhd",
    _FIX / "rr_rea_build_id_stub.vhd",
    _RTL / "rr_rea_dpram.vhd",
    _RTL / "rr_rea_capture_fsm.vhd",
    _RTL / "rr_rea_regbank.vhd",
    _RTL / "rr_rea_cdc.vhd",
    _RTL / "rr_rea_jtag_iface.vhd",
    _RTL / "rr_rea_crc_sweep.vhd",
    _RTL / "rr_rea_fill_fsm.vhd",
    _RTL / "rr_rea_trust_core.vhd",
    _RTL / "rr_rea_axis_window.vhd",
    _RTL / "rr_rea_top.vhd",
]


def _elaborate(generics: dict) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        work = f"--work={tmp}/work"
        analyze = subprocess.run(
            ["nvc", work, "--std=2008", "-a", *map(str, _SOURCES)],
            capture_output=True, text=True, errors="replace")
        assert analyze.returncode == 0, f"analyze failed: {analyze.stderr}"
        return subprocess.run(
            ["nvc", work, "--std=2008", "-e", "rr_rea_top",
             *[f"-g{k}={v}" for k, v in generics.items()]],
            capture_output=True, text=True, errors="replace")


_BASE = {"G_SAMPLE_W": 12, "G_DEPTH": 16}

needs_nvc = pytest.mark.skipif(shutil.which("nvc") is None,
                               reason="nvc not on PATH")


@needs_nvc
@requires("REA-REQ-957")
@pytest.mark.parametrize("generics, guard", [
    ({"G_QUAL_CONDS": 2, "G_TIMESTAMP_W": 0}, "C_QUAL_TIMESTAMP_GUARD"),
    ({"G_QUAL_CONDS": 16, "G_TIMESTAMP_W": 16}, "C_QUAL_CONDS_GUARD"),
])
def test_rea_req_957_illegal_qualifier_build_fails_elaboration(generics, guard):
    result = _elaborate({**_BASE, **generics})
    out = result.stdout + result.stderr
    assert result.returncode != 0, (
        f"{generics} elaborated cleanly — the REA-REQ-957 guard did not halt "
        f"the build:\n{out}")
    assert guard in out, (
        f"{generics} failed, but not through {guard}; another error may be "
        f"masking the guard:\n{out}")


@needs_nvc
@requires("REA-REQ-957")
@pytest.mark.parametrize("generics", [
    {"G_QUAL_CONDS": 0, "G_TIMESTAMP_W": 0},   # no qualifier: timestamps optional
    {"G_QUAL_CONDS": 1, "G_TIMESTAMP_W": 1},   # smallest legal qualified build
    {"G_QUAL_CONDS": 15, "G_TIMESTAMP_W": 16},  # the ceiling itself
])
def test_rea_req_957_legal_neighbours_elaborate(generics):
    result = _elaborate({**_BASE, **generics})
    assert result.returncode == 0, (
        f"legal build {generics} was refused:\n{result.stdout + result.stderr}")


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
