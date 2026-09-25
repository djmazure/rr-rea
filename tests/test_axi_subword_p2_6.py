# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.6 — AXI4-Lite low address bits masked to 00 and sub-word writes dropped.

The AXI4-Lite bridge (rr_rea_axi4lite) must mask araddr[1:0] and awaddr[1:0] to
"00" when driving reg_addr_o, so narrow (byte and halfword) AXI reads decode
the containing 32-bit register and return the full register word on rdata_o
(with the byte/halfword placed in the appropriate byte lanes).

Sub-word writes (wstrb /= "1111") must be dropped rather than half-applied to
side-effect or toggle registers (such as CTRL's arm_toggle). wstrb_i must be
latched into w_strb_r when wvalid_i is accepted, so channel reordering or
backpressure does not evaluate subsequent idle bus state in W_APPLY.

This structural test needs no simulator, so it runs in CI while the cocotb job
is blocked (RTL-T1.26). Behavioural verification runs in cocotb
test_rea_axi4lite_p3_931.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
AXI_VHD = (REPO / "rtl" / "rr_rea_axi4lite.vhd").read_text()
SPEC_MD = (REPO / "SPEC.md").read_text()
REQS_YML = (REPO / "requirements.yml").read_text()


def _strip_vhdl_comments(text: str) -> str:
    return "\n".join(line.split("--", 1)[0].rstrip() for line in text.splitlines())


AXI_CODE = _strip_vhdl_comments(AXI_VHD)


def test_axi_address_masking_in_to_reg_addr():
    """to_reg_addr must explicitly mask bits 1 downto 0 to 00."""
    func_match = re.search(
        r"function\s+to_reg_addr\b.*?\bend\s+function",
        AXI_CODE,
        re.DOTALL,
    )
    assert func_match, "function to_reg_addr not found in rr_rea_axi4lite.vhd"
    func_body = func_match.group(0)
    assert re.search(r'v\(\s*1\s+downto\s+0\s*\)\s*:=\s*"00"', func_body), (
        "to_reg_addr must mask bits 1 downto 0 to \"00\" so narrow AXI reads "
        "and writes are word-aligned on the register bus (REA-P2.6)"
    )


def test_axi_wstrb_latched():
    """w_strb_r must be declared and latched when wvalid_i arrives."""
    assert re.search(
        r"signal\s+w_strb_r\s*:\s*std_logic_vector\(\s*3\s+downto\s+0\s*\)",
        AXI_CODE,
    ), "signal w_strb_r must be declared as 4-bit std_logic_vector"

    latch_match = re.search(
        r"if\s+wvalid_i\s*=\s*'1'\s+and\s+w_seen_r\s*=\s*'0'\s+then(.*?)end\s+if",
        AXI_CODE,
        re.DOTALL,
    )
    assert latch_match, "wvalid_i latch block not found in W_IDLE"
    assert re.search(r"w_strb_r\s*<=\s*wstrb_i", latch_match.group(1)), (
        "wstrb_i must be latched into w_strb_r when wvalid_i is accepted (REA-P2.6)"
    )


def test_axi_subword_write_guarded_by_latched_wstrb():
    """reg_wr_en_r must only be asserted when latched w_strb_r is all-ones."""
    apply_match = re.search(
        r"elsif\s+wr_state_r\s*=\s*W_APPLY\s+then(.*?)else\s+--\s*W_RESP",
        AXI_VHD,
        re.DOTALL,
    )
    assert apply_match, "W_APPLY block not found in rr_rea_axi4lite.vhd"
    apply_body = apply_match.group(1)
    assert re.search(r'if\s+w_strb_r\s*=\s*"1111"\s+then', apply_body), (
        "W_APPLY must guard reg_wr_en_r with latched w_strb_r = \"1111\", "
        "not live unlatched wstrb_i (REA-P2.6)"
    )
    assert not re.search(r'if\s+wstrb_i\s*=\s*"1111"\s+then', apply_body), (
        "W_APPLY still references live input wstrb_i instead of latched w_strb_r"
    )


def test_axi_header_corrected():
    """Header must not contradict itself with unmodified address pass-through."""
    assert "passes through to reg_addr_o unmodified" not in AXI_VHD, (
        "header comment must not claim AXI address passes through unmodified (REA-P2.6)"
    )
    assert 'low 2 bits masked to "00"' in AXI_VHD, (
        "header comment must document low 2 bits masked to \"00\" (REA-P2.6)"
    )


def test_spec_and_requirements_cite_p2_6():
    """Contract documents must record REA-P2.6 requirements."""
    assert "REA-P2.6" in SPEC_MD, "SPEC.md must cite REA-P2.6"
    assert "REA-P2.6" in REQS_YML, "requirements.yml must cite REA-P2.6"
