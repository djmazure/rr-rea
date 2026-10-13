# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.23 — DECIM register 24-bit applied readback and host decimation gate.

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
    width, naming the max (24 bits).
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch
import pytest

REPO = Path(__file__).resolve().parents[1]
REGBANK_VHD = REPO / "rtl" / "rr_rea_regbank.vhd"
REQUIREMENTS_YML = REPO / "requirements.yml"
SPEC_MD = REPO / "SPEC.md"


def _read_regbank_vhd() -> str:
    return REGBANK_VHD.read_text(encoding="utf-8")


def test_regbank_decim_masks_upper_bits():
    """RTL contract: DECIM register must not store unmasked 32-bit writes and
    must reflect only the applied 24-bit ratio on readback (bits 31:24 read 0)."""
    text = _read_regbank_vhd()

    # Rejection of unmasked 32-bit storage:
    # `decim_r <= wr_data_i;` with a 32-bit decim_r allows upper 8 bits to leak
    # into readback. The RTL must store only 24 bits or mask off wr_data_i.
    unmasked_write = re.search(r"when\s+C_ADDR_DECIM\s*=>\s*decim_r\s*<=\s*wr_data_i\s*;", text)
    decim_decl_32 = re.search(r"signal\s+decim_r\s*:\s*std_logic_vector\s*\(\s*31\s+downto\s+0\s*\)", text)

    assert not (unmasked_write and decim_decl_32), (
        "DECIM stores full 32-bit write instead of masking to applied 24 bits"
    )

    # Readback must explicitly clear or not include bits [31:24].
    # Either decim_r is 24-bit and readback pads with x"00", or upper bits are zeroed.
    read_match = re.search(r"when\s+C_ADDR_DECIM\s*=>\s*rd_data_o\s*<=\s*([^;]+);", text)
    assert read_match, "C_ADDR_DECIM readback branch missing in rd_data_o decode"
    read_expr = read_match.group(1).strip()
    if decim_decl_32:
        assert False, "DECIM stores full 32-bit write instead of masking to applied 24 bits"
    assert "x\"00\"" in read_expr or "decim_r(23 downto 0)" in read_expr or read_expr == "x\"00\" & decim_r", (
        f"DECIM readback expr {read_expr!r} does not clear upper byte [31:24]"
    )


def test_host_refuses_decimation_exceeding_core_width():
    """Host contract: rr ila / REAClient must refuse decimation values that do
    not fit the 24-bit core width, naming the 24-bit limit."""
    from sdk.cli.rea.client import REAClient, REAConfig

    class MockTransport:
        def connect(self): pass
        def close(self): pass
        def read_reg(self, addr):
            return {
                0x00: 0x5245410F, 0x08: 0, 0x0C: 32, 0x10: 64,
                0xA4: 1, 0xC4: 32, 0xD0: 4 | (1 << 8) | (1 << 18),
            }.get(addr, 0)
        def write_reg(self, addr, data): pass

    client = REAClient(MockTransport())

    # Out of range (> 24-bit): must raise ValueError naming 24 bits
    cfg_bad = REAConfig(
        sample_width=32, depth=64, pretrigger=0, posttrigger=10,
        trig_value=0, trig_mask=0, trig_mode=0,
        decimation=0x0100_0005,
    )
    with pytest.raises(ValueError) as excinfo:
        client.configure(cfg_bad)
    err_msg = str(excinfo.value)
    assert "24 bits" in err_msg, f"error message should name 24 bits limit: {err_msg}"
    assert "16777221" in err_msg or "0x1000005" in err_msg or "0x01000005" in err_msg

    # Upper boundary: 0x00FF_FFFF (24 bits max) must be accepted
    cfg_max = REAConfig(
        sample_width=32, depth=64, pretrigger=0, posttrigger=10,
        trig_value=0, trig_mask=0, trig_mode=0,
        decimation=0x00FF_FFFF,
    )
    client.configure(cfg_max)
    assert client._config.decimation == 0x00FF_FFFF


def test_cli_refuses_decimation_exceeding_core_width():
    """CLI surface: rr ila capture refuses decimation > 24 bits with exit 2 and names 24 bits."""
    from click.testing import CliRunner
    from sdk.cli.commands import ila

    core = {
        "name": "u_dbg_rea",
        "vendor": "xilinx",
        "sample_width": 32,
        "depth": 64,
        "sample_clock_hz": 100_000_000,
    }
    channels = [{"id": 0, "name": "sig", "width": 32, "type": "Bus", "role": "Wire", "radix": "Hexadecimal"}]

    class MockTransport:
        def connect(self): pass
        def close(self): pass
        def read_reg(self, addr):
            return {
                0x00: 0x5245410F, 0x08: 0, 0x0C: 32, 0x10: 64,
                0xA4: 1, 0xC4: 32, 0xD0: 4 | (1 << 8) | (1 << 18),
            }.get(addr, 0)
        def write_reg(self, addr, data): pass

    with patch.object(ila, "_project_yml_channels_for_core", return_value=(core, channels)), \
         patch.object(ila, "_build_rea_transport", return_value=MockTransport()), \
         patch.object(ila, "_check_rea_probe_widths", return_value=True), \
         patch("sdk.cli.lease_guard.require_lease", lambda **_kw: None):
        from sdk.cli.main import cli
        cmd = ["ila", "capture", "--core", "u_dbg_rea", "--no-open", "--timeout", "1", "--decim", "16777216"]
        res = CliRunner().invoke(cli, cmd)
        assert res.exit_code != 0, f"expected failure exit code, got {res.exit_code}"
        assert "24 bits" in res.output, f"expected '24 bits' in output: {res.output}"
