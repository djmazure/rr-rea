# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-T3.3 — each util build is judged against ITS OWN Fmax floor.

`technical.clocks` used to carry one `sample_clk_i` floor (200 MHz) for every
build. util_big (256-bit x 4-condition qualifier compare, already registered
one cycle ahead) routes near 130 MHz by construction, so its OOC impl gate
failed forever (REA-T3.2 queue job 7eabe68a8043: "technical.fmax ... 133 <
200"). Since RTL-P3.1878 a floor can be keyed per build point: util_big binds
its own floor and every other build keeps the 200 MHz fallback.

Like test_resource_budget_points_t3_1.py, these drive routertl's own gate
(`evaluate_build_technical_contracts`), resolving each target the way a build
does, with a report carrying the measured Fmax from the SPEC cost table.
"""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest

pytest.importorskip("sdk.engine.build_technical_eval", reason="needs routertl (rr) installed")

from rich.console import Console  # noqa: E402
from routertl_core.resource_budget import resolve_technical  # noqa: E402
from sdk.cli.project_config import load_project_config, target_override  # noqa: E402
from sdk.cli.requirements import load_requirements  # noqa: E402
from sdk.engine.build_technical_eval import (  # noqa: E402
    evaluate_build_technical_contracts,
    resolve_build_context,
)

ROOT = Path(__file__).resolve().parents[1]
TARGETS = sorted(p.stem for p in (ROOT / "targets").glob("util_*.yml"))
CLOCK = "sample_clk_i"
DEFAULT_FLOOR = 200.0


def _spec_fmax() -> dict[str, float]:
    """util_* rows of the first SPEC cost table: {target: Fmax MHz}."""
    text = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    sec = text.split("## Resources and Fmax per configuration", 1)[1]
    out: dict[str, float] = {}
    in_table = False
    for ln in sec.splitlines():
        if not ln.startswith("|"):
            if in_table:
                break
            continue
        in_table = True
        m = re.match(r"^\| (util_\w+) \|", ln)
        if m:
            fmax = ln.strip("|").split("|")[7].strip()
            out[m.group(1)] = float(fmax.split()[0])
    return out


SPEC_FMAX = _spec_fmax()


def _build(target: str):
    with target_override(str(ROOT / "targets" / f"{target}.yml")):
        cfg = load_project_config(str(ROOT / "project.yml"))
        generics, name = resolve_build_context(ROOT, config=cfg)
    return cfg, generics, name


def _clock(target: str):
    _, generics, name = _build(target)
    doc = load_requirements(ROOT / "requirements.yml")
    tech = resolve_technical(doc.technical, doc.parameters, generics, name)
    return next(c for c in tech.clocks if c.name == CLOCK)


def _gate(target: str, fmax: float, tmp_path: Path) -> tuple[int, str]:
    """The real gate on an impl report that meets every resource budget."""
    cfg, _, _ = _build(target)
    rep = tmp_path / f"rep_{target}"  # not "reports": keep the gate on this file
    rep.mkdir()
    (rep / "build_report.json").write_text(json.dumps({
        "vendor": "xilinx", "part": "xc7z020clg400-1", "top_module": "rr_rea_top",
        "tool_name": "Vivado 2024.1", "warnings": 0, "errors": 0,
        "phases": [{"name": "Implementation", "passed": True, "duration_s": 0.0}],
        "resources": {k: {"used": 0, "available": 1_000_000}
                      for k in ("LUTs", "FFs", "BRAM", "DSPs")},
        "timing": {"fmax_mhz": fmax, "clock_name": CLOCK,
                   "wns_ns": 2.5 - 1000.0 / fmax, "tns_ns": 0.0, "whs_ns": 0.05,
                   "ths_ns": 0.0, "met": False},
    }))
    out = io.StringIO()
    with target_override(str(ROOT / "targets" / f"{target}.yml")):
        rc = evaluate_build_technical_contracts(
            ROOT, reports_dir=rep, config=cfg,
            console=Console(file=out, width=200, no_color=True))
    return rc, out.getvalue()


def test_every_util_target_has_a_measured_fmax():
    assert set(TARGETS) <= set(SPEC_FMAX), sorted(set(TARGETS) - set(SPEC_FMAX))


def test_util_big_passes_at_its_measured_fmax(tmp_path):
    """The done-when. Under the single 200 MHz floor this exits 1."""
    rc, out = _gate("util_big", SPEC_FMAX["util_big"], tmp_path)
    assert rc == 0, out


def test_util_big_binds_its_own_floor():
    clk = _clock("util_big")
    assert tuple(getattr(clk, "fmax_matched", ())) == ("target=util_big",), clk
    assert clk.fmax_mhz < DEFAULT_FLOOR


def test_util_big_below_its_own_floor_still_fails(tmp_path):
    """A per-point floor must still bite: 1 MHz under it is a hard failure."""
    floor = _clock("util_big").fmax_mhz
    rc, out = _gate("util_big", floor - 1.0, tmp_path)
    assert rc == 1, out
    assert "fmax" in out, out


@pytest.mark.parametrize("target", [t for t in TARGETS if t != "util_big"])
def test_other_targets_keep_the_200_mhz_floor(target, tmp_path):
    """Keying util_big's floor must not loosen anyone else's: every other build
    binds the fallback, and 1 MHz under 200 still fails."""
    clk = _clock(target)
    assert clk.fmax_mhz == DEFAULT_FLOOR, clk
    rc, out = _gate(target, DEFAULT_FLOOR - 1.0, tmp_path)
    assert rc == 1, out


@pytest.mark.parametrize("target", TARGETS)
def test_measured_fmax_meets_its_floor(target):
    """SPEC's measured Fmax and the contract's floor cannot drift apart."""
    assert SPEC_FMAX[target] >= _clock(target).fmax_mhz
