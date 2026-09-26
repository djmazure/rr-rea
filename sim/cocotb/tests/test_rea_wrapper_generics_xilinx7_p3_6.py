# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P3.6: rr_rea_xilinx7 passes G_TRIG_CONDS and G_QUAL_CONDS to rr_rea_top,
and its ext_trigger_i reaches the core. Body in rea_wrapper_generics.py."""
from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_tb = str(_Path(__file__).resolve().parent)
if _tb not in _sys.path:
    _sys.path.insert(0, _tb)
del _tb

import cocotb  # noqa: E402

import rea_wrapper_generics as rwg  # noqa: E402
from engine.simulation import run_simulation  # noqa: E402

VENDOR = "xilinx7"


@cocotb.test()
async def test_wrapper_passes_trig_and_qual_conds(dut):
    await rwg.start(dut)
    rwg.check_features(await rwg.read(dut, rwg.ADDR_FEATURES))


@cocotb.test()
async def test_wrapper_ext_trigger_reaches_core(dut):
    await rwg.start(dut)
    await rwg.ext_trigger_fires_only_on_the_pin(dut)


def main() -> None:
    run_simulation(
        top_level="rr_rea_wrapper_harness",
        module="test_rea_wrapper_generics_xilinx7_p3_6",
        custom_libraries=rwg.libraries(),
        generics={**rwg.GENERICS, "G_VENDOR": VENDOR},
        waves=True,
        simulator="nvc",
    )


if __name__ == "__main__":
    main()
