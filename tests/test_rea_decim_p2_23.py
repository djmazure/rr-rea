# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.23 — DECIM register 24-bit applied readback (REA-REQ-971).

Measured on origin/main 6ee3e01 (rtl/rr_rea_regbank.vhd): the DECIM register
stored the whole 32-bit write (decim_r <= wr_data_i) and read it back unchanged
(rd_data_o <= decim_r), but only decim_r(23 downto 0) drove decim_ratio_o.
A host writing >= 2**24 and verifying by readback saw a match while the core
ran at (value mod 2**24)+1.

Done when:
(a) the DECIM readback reflects the APPLIED value (bits 31:24 read 0, or the
    write saturates/rejects), with a cocotb test that writes 0x01000005 and
    asserts readback equals what the decimator uses;
(b) rr ila host-side refuses a requested decimation that does not fit the core
    width, naming the max (24 bits; verified in RouteRTL SDK).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REGBANK_VHD = REPO / "rtl" / "rr_rea_regbank.vhd"


def _read_regbank_vhd() -> str:
    return REGBANK_VHD.read_text(encoding="utf-8")


def test_regbank_decim_masks_upper_bits():
    """RTL contract: DECIM register must reflect only the applied 24-bit ratio
    on readback (bits 31:24 read 0), matching what is applied to decim_ratio_o."""
    text = _read_regbank_vhd()

    # decim_ratio_o must be driven by the register storing the ratio
    ratio_match = re.search(r"decim_ratio_o\s*<=\s*([^;]+);", text)
    assert ratio_match, "decim_ratio_o assignment missing in rr_rea_regbank.vhd"
    ratio_expr = ratio_match.group(1).strip()
    assert "decim_r" in ratio_expr, (
        f"decim_ratio_o expr {ratio_expr!r} does not connect to decim_r"
    )

    # Readback must explicitly clear bits [31:24]
    read_match = re.search(r"when\s+C_ADDR_DECIM\s*=>\s*rd_data_o\s*<=\s*([^;]+);", text)
    assert read_match, "C_ADDR_DECIM readback branch missing in rd_data_o decode"
    read_expr = read_match.group(1).strip()
    assert "x\"00\"" in read_expr or "decim_r(23 downto 0)" in read_expr or read_expr == "x\"00\" & decim_r", (
        f"DECIM readback expr {read_expr!r} does not clear upper byte [31:24]"
    )

    # Rejection of unmasked 32-bit storage and readback:
    # A 32-bit register storing unmasked wr_data_i and returning it directly on
    # readback allows upper bits to leak into readback.
    unmasked_write = re.search(r"when\s+C_ADDR_DECIM\s*=>\s*decim_r\s*<=\s*wr_data_i\s*;", text)
    unmasked_read = re.search(r"when\s+C_ADDR_DECIM\s*=>\s*rd_data_o\s*<=\s*decim_r\s*;", text)
    decim_decl_32 = re.search(r"signal\s+decim_r\s*:\s*std_logic_vector\s*\(\s*31\s+downto\s+0\s*\)", text)
    assert not (unmasked_write and unmasked_read and decim_decl_32), (
        "DECIM stores full 32-bit write instead of masking to applied 24 bits"
    )


def test_rea_req_971_decim_readback_reflects_applied_ratio():
    """Behavioral oracle (REA-REQ-971): write 0x01000005 to DECIM and assert
    readback equals what the decimator uses (decim_ratio_o, 0x00000005)."""
    res = subprocess.run(
        ["rr", "sim", "run", "test_rea_regbank", "--wait"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, (
        f"DECIM readback does not match applied ratio:\n{res.stdout}\n{res.stderr}"
    )
