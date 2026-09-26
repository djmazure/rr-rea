# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P3.8 — every REA synchronizer carries the attributes each vendor reads.

Measured 2026-09-26 (util_field: G_SAMPLE_W 80, G_QUAL_CONDS 1):
- Vivado 2024.1 reads ASYNC_REG on s1/s2 (report_cdc CDC-3/CDC-6 "synchronized
  with ASYNC_REG property", all Safe).
- Quartus Pro 25.3.1 ignores ASYNC_REG ("Invalid assignment name") and finds
  the chains by Auto identification, which needs the clocks that
  rr_rea_scoped.sdc declares; nothing in the RTL can change that.
- Libero 2025.2 / Synplify rated 17 of 27 synchronizers "Divergence detected in
  the crossover path" and did not forward them to placement until s1 carried
  syn_safe_cdc; with it, all 27 report SAFE_CDC YES and reach placement.

Structural guard (no vendor tool): the two data synchronizer entities keep both
attributes on their first stage, and the header no longer claims Quartus
honours ASYNC_REG.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CDC = REPO / "rtl" / "rr_rea_cdc.vhd"


def _arch(entity: str) -> str:
    text = CDC.read_text(encoding="utf-8")
    body = text[text.index(f"architecture rtl of {entity}"):]
    return body[:body.index("end architecture")]


def _code(text: str) -> str:
    return re.sub(r"--[^\n]*", "", text)


def test_each_synchronizer_first_stage_has_asyncreg_and_syn_safe_cdc():
    for entity in ("rr_rea_sync_word", "rr_rea_pulse_xfer"):
        code = _code(_arch(entity))
        for stage in ("s1", "s2"):
            assert re.search(
                rf'attribute\s+ASYNC_REG\s+of\s+{stage}\s*:\s*signal\s+is\s+"TRUE"',
                code, re.I), f"{entity}.{stage} lost ASYNC_REG (Vivado reads it)"
        assert re.search(
            r"attribute\s+syn_safe_cdc\s+of\s+s1\s*:\s*signal\s+is\s+true\b",
            code, re.I), (
            f"{entity}.s1 lost syn_safe_cdc: Synplify then rates the crossing "
            f"'Divergence detected' and does not place it as a synchronizer")


def test_header_does_not_claim_quartus_honours_asyncreg():
    comments = " ".join(re.findall(r"--([^\n]*)", CDC.read_text(encoding="utf-8")))
    comments = re.sub(r"\s+", " ", comments)
    assert "Vivado/Quartus both honor" not in comments
    assert "disable timing analysis on the source path" not in comments
    assert re.search(r"Quartus.{0,40}ignores ASYNC_REG", comments), (
        "the measured Quartus behaviour is no longer stated")
