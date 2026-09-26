# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P3.7 / REA-REQ-967 — a sequencer deeper than the SEQ window fails
elaboration.

The SEQ window 0x40..0x9F holds four 20-byte stages and FEATURES[30:28]
carries the depth, so G_TRIG_STAGES > 4 is refused by a static range
violation (the RTL-P2.895 pattern), which no vendor tool can downgrade to a
warning. Every legal depth must still elaborate: a guard that also rejects
legal builds is a lockout, not a guard.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

import pytest

# The sibling helper import needs this directory on sys.path. pytest's default
# prepend mode supplies it, but --import-mode=importlib does not: that is the
# mode tich-super's pytest.ini sets, and it applies whenever rr-rea is checked
# out as its submodule (REA-P3.12).
_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

from sdk.cocotb_helpers import requires  # noqa: E402
from test_rea_qual_elab_guard_p2_7 import _elaborate, needs_nvc  # noqa: E402

_BASE = {"G_SAMPLE_W": 40, "G_DEPTH": 16}


@needs_nvc
@requires("REA-REQ-967")
def test_rea_req_967_five_stages_fail_elaboration():
    result = _elaborate({**_BASE, "G_TRIG_STAGES": 5})
    out = result.stdout + result.stderr
    assert result.returncode != 0, (
        f"G_TRIG_STAGES=5 elaborated cleanly — the SEQ window holds four "
        f"stages:\n{out}")
    assert "C_TRIG_STAGES_GUARD" in out, (
        f"G_TRIG_STAGES=5 failed, but not through C_TRIG_STAGES_GUARD:\n{out}")


@needs_nvc
@requires("REA-REQ-967")
@pytest.mark.parametrize("stages", [0, 1, 2, 3, 4])
def test_rea_req_967_every_legal_depth_elaborates(stages):
    result = _elaborate({**_BASE, "G_TRIG_STAGES": stages})
    assert result.returncode == 0, (
        f"legal G_TRIG_STAGES={stages} refused:\n{result.stdout}{result.stderr}")
