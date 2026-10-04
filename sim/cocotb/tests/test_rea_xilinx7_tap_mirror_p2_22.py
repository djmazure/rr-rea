# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.22: rr_rea_xilinx7's tap_tck/tap_tms/tap_tdi mirror what BSCANE2
drives and tap_tdo mirrors the TDO the core returns to it, through a real
register scan, while the core still answers that scan."""
from __future__ import annotations

import random
import sys as _sys
from pathlib import Path as _Path

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

import cocotb  # noqa: E402
from cocotb.triggers import FallingEdge, ReadOnly, Timer  # noqa: E402

import rea_wrapper_generics as rwg  # noqa: E402
from engine.simulation import run_simulation  # noqa: E402
from sdk.cocotb_helpers import requires  # noqa: E402

HARNESS = rwg.FIX / "rr_rea_xilinx7_tap_mirror_harness.vhd"


def _check(dut, where: str):
    pairs = (("mon_tck", "tck_i"), ("mon_tms", "tms_i"),
             ("mon_tdi", "tdi_i"), ("mon_tdo", "tdo_o"))
    for mon, ref in pairs:
        got, want = str(getattr(dut, mon).value), str(getattr(dut, ref).value)
        assert got == want, f"{where}: {mon}={got} but {ref}={want}"


@cocotb.test()
@requires("REA-REQ-970")
async def test_mirrors_follow_the_tap_through_a_register_scan(dut):
    dut.tms_i.value = 0
    await rwg.start(dut)
    rng = random.Random(22)
    seen = {"tms": set(), "tdo": set()}

    async def watch():
        while True:
            await FallingEdge(dut.tck_i)
            dut.tms_i.value = rng.getrandbits(1)
            await ReadOnly()
            _check(dut, "mid-scan")
            seen["tms"].add(str(dut.mon_tms.value))
            seen["tdo"].add(str(dut.mon_tdo.value))

    cocotb.start_soon(watch())
    version = await rwg.read(dut, 0x00)
    assert version == 0x5245410F, f"VERSION=0x{version:08X} through the wrapper"
    # Both levels observed on the two signals the core cannot fake: TMS (now
    # connected) and TDO (the scan's response bits).
    assert seen["tms"] == {"0", "1"}, seen
    assert seen["tdo"] == {"0", "1"}, seen


@cocotb.test()
@requires("REA-REQ-970")
async def test_mirrors_follow_the_tap_between_scans(dut):
    await rwg.start(dut)
    for k in range(32):
        dut.tms_i.value = k & 1
        dut.tdi_i.value = (k >> 1) & 1
        await Timer(3, unit="ns")
        await ReadOnly()
        _check(dut, f"idle step {k}")
        await Timer(1, unit="ns")


def main() -> None:
    libs = rwg.libraries()
    libs["work"] = libs["work"] + [str(HARNESS)]
    gens = {k: v for k, v in rwg.GENERICS.items() if k != "G_VENDOR"}
    run_simulation(
        top_level="rr_rea_xilinx7_tap_mirror_harness",
        module="test_rea_xilinx7_tap_mirror_p2_22",
        custom_libraries=libs,
        generics=gens,
        waves=False,
        simulator="nvc",
    )


if __name__ == "__main__":
    main()
