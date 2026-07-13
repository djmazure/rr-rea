# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
#
# rr_rea — un-ignorable over-ceiling elaboration guard (RTL-P2.895).
#
# This is a PYTEST (not a cocotb test): it proves the NEGATIVE — that a
# G_SAMPLE_W above C_MAX_SAMPLE_W (1024) FAILS ELABORATION — which a cocotb
# test (whose own harness must elaborate first) structurally cannot assert.
# It shells nvc directly and checks the elaboration exits non-zero with the
# C_CEILING_GUARD static-range-violation message, and that an in-ceiling width
# elaborates cleanly. No cocotb import, so `rr sim` does not try to run it;
# `rr sim coverage-map` still scans its @requires tag.
#
# Background: the field shipped 704-bit silicon that was out of the OLD 256-bit
# contract; its `assert ... severity failure` was downgraded to a WARNING by
# vendor synth, producing undefined silicon (bit0=1 reads → 0xFFFFFFFF). The
# durable capacity fix (RTL-P2.876) raised the ceiling; THIS guard (RTL-P2.895)
# makes any FUTURE over-ceiling build fail the build in ALL tools.

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from sdk.cocotb_helpers import requires

_RTL_DIR = Path(__file__).resolve().parents[4] / "ip" / "routertl" / "rea" / "rtl"
_PKG = _RTL_DIR / "rr_rea_pkg.vhd"
_FSM = _RTL_DIR / "rr_rea_capture_fsm.vhd"

# Above C_MAX_SAMPLE_W=1024 — must hard-fail elaboration.
_OVER_CEILING = 1056
# At/below the ceiling — must elaborate cleanly.
_AT_CEILING = 1024


def _elaborate(width: int, work: Path) -> subprocess.CompletedProcess:
    """Analyze pkg+fsm and elaborate rr_rea_capture_fsm at G_SAMPLE_W=width."""
    analyze = subprocess.run(
        ["nvc", f"--work={work}/work", "-a", str(_PKG), str(_FSM)],
        capture_output=True, text=True, errors="replace",
    )
    assert analyze.returncode == 0, f"analyze failed: {analyze.stderr}"
    return subprocess.run(
        ["nvc", f"--work={work}/work", "-e", "rr_rea_capture_fsm",
         f"-gG_SAMPLE_W={width}", "-gG_DEPTH=1024"],
        capture_output=True, text=True, errors="replace",
    )


@pytest.mark.skipif(shutil.which("nvc") is None, reason="nvc not on PATH")
@requires("REA-REQ-018")
def test_over_ceiling_elaboration_hard_fails():
    """G_SAMPLE_W > C_MAX_SAMPLE_W must FAIL elaboration (exit != 0) via the
    C_CEILING_GUARD static range violation — un-ignorable, not a warning."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _elaborate(_OVER_CEILING, Path(tmp))
    combined = result.stdout + result.stderr
    assert result.returncode != 0, (
        f"RTL-P2.895 FAIL: G_SAMPLE_W={_OVER_CEILING} (> ceiling 1024) "
        f"elaborated cleanly — the over-ceiling guard did NOT halt the build. "
        f"Output:\n{combined}"
    )
    assert "C_CEILING_GUARD" in combined, (
        "RTL-P2.895: elaboration failed but not via the C_CEILING_GUARD static "
        f"range bomb — a different error may be masking the guard:\n{combined}"
    )


@pytest.mark.skipif(shutil.which("nvc") is None, reason="nvc not on PATH")
@requires("REA-REQ-018")
def test_at_ceiling_elaborates_cleanly():
    """The boundary width (== C_MAX_SAMPLE_W) must still elaborate — the guard
    fires strictly ABOVE the ceiling, never at it (no off-by-one lockout)."""
    with tempfile.TemporaryDirectory() as tmp:
        result = _elaborate(_AT_CEILING, Path(tmp))
    assert result.returncode == 0, (
        f"RTL-P2.895 FAIL: G_SAMPLE_W={_AT_CEILING} (== ceiling) must elaborate "
        f"cleanly but the guard rejected it:\n{result.stdout + result.stderr}"
    )


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
