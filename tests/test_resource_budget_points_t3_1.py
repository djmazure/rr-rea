# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-T3.1 — each util build is judged against ITS OWN resource budget.

Since RTL-P2.1351, rr checks `technical.resources` at synth. With one flat cap
in requirements.yml, every util target was judged against the DEFAULT
elaboration's caps: `util_field_noqual` synth failed "Measured: 5259 vs Bound:
1850" (queue job 5751a6d08bd7, rr-rea a01d42c). The caps are now keyed per
build point (RTL-P2.1374): one `target:` point per non-default util target,
with the default caps as the selector-less fallback.

These tests drive routertl's own gate (`evaluate_build_technical_contracts`),
resolving each target the way a build does (RR_TARGET → the merged target
config → its generics and name). They feed it a build report carrying that
target's measured utilisation from SPEC "Resources and Fmax per configuration".
The report carries a real timing, so RTL-T2.299 (synth reports no timing and is
judged as fmax 0.0) cannot mask the resource verdict.
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
DIMS = ("lut", "ff", "bram", "dsp")
REPORT_KEYS = {"lut": "LUTs", "ff": "FFs", "bram": "BRAM", "dsp": "DSPs"}


def _spec_rows() -> dict[str, dict[str, float]]:
    """util_* rows of the SPEC cost table: {target: {lut, ff, bram, dsp}}."""
    text = (ROOT / "SPEC.md").read_text(encoding="utf-8")
    sec = text.split("## Resources and Fmax per configuration", 1)[1]
    rows: dict[str, dict[str, float]] = {}
    in_table = False
    for ln in sec.splitlines():
        if not ln.startswith("|"):
            if in_table:
                break  # the first table only: later ones are other vendors
            continue
        in_table = True
        m = re.match(r"^\| (util_\w+) \|", ln)
        if not m:
            continue
        c = [x.strip() for x in ln.strip("|").split("|")]
        # Config | Generics | LUT | FF | SRL | BRAM | DSP | Fmax
        rows[m.group(1)] = {"lut": float(c[2]), "ff": float(c[3]),
                            "bram": float(c[5]), "dsp": float(c[6])}
    return rows


SPEC_ROWS = _spec_rows()


def _build(target: str):
    """(config, generics, target name) exactly as a build of *target* sees them."""
    with target_override(str(ROOT / "targets" / f"{target}.yml")):
        cfg = load_project_config(str(ROOT / "project.yml"))
        generics, name = resolve_build_context(ROOT, config=cfg)
    return cfg, generics, name


def _caps(target: str):
    _, generics, name = _build(target)
    doc = load_requirements(ROOT / "requirements.yml")
    return resolve_technical(doc.technical, doc.parameters, generics, name).resources


def _gate(target: str, used: dict[str, float], tmp_path: Path) -> tuple[int, str]:
    """Run the real technical-contract gate on a report with *used* resources."""
    cfg, _, _ = _build(target)
    rep = tmp_path / f"rep_{target}"  # not "reports": keep the gate on this file
    rep.mkdir()
    (rep / "build_report.json").write_text(json.dumps({
        "vendor": "xilinx", "part": "xc7z020clg400-1", "top_module": "rr_rea_top",
        "tool_name": "Vivado 2024.1", "warnings": 0, "errors": 0,
        "phases": [{"name": "Implementation", "passed": True, "duration_s": 0.0}],
        "resources": {REPORT_KEYS[d]: {"used": used[d], "available": 1_000_000}
                      for d in DIMS},
        "timing": {"fmax_mhz": 250.0, "clock_name": "sample_clk_i", "wns_ns": 0.5,
                   "tns_ns": 0.0, "whs_ns": 0.05, "ths_ns": 0.0, "met": True},
    }))
    out = io.StringIO()
    with target_override(str(ROOT / "targets" / f"{target}.yml")):
        rc = evaluate_build_technical_contracts(
            ROOT, reports_dir=rep, config=cfg,
            console=Console(file=out, width=200, no_color=True))
    return rc, out.getvalue()


def test_every_util_target_has_a_measured_row():
    """A util target without a SPEC row has no measured budget to be held to."""
    assert set(TARGETS) <= set(SPEC_ROWS), sorted(set(TARGETS) - set(SPEC_ROWS))


def test_field_noqual_is_judged_against_its_own_budget(tmp_path):
    """The done-when: its measured utilisation passes the gate. Under the old flat
    contract this exits 1 on 'lut 5396 exceeds cap 1850'."""
    rc, out = _gate("util_field_noqual", SPEC_ROWS["util_field_noqual"], tmp_path)
    assert rc == 0, out
    assert "budget target=util_field_noqual binds this build" in out, out


@pytest.mark.parametrize("target", TARGETS)
def test_each_target_binds_its_own_point(target):
    """Only util_default may fall back to the default caps: any other target that
    lands on the fallback is being judged against another build's budget."""
    caps = _caps(target)
    if target == "util_default":
        assert len(caps.matched) == 1 and caps.matched[0].endswith("(fallback)"), caps.matched
    else:
        assert caps.matched == (f"target={target}",), caps.matched


@pytest.mark.parametrize("target", TARGETS)
def test_measured_row_fits_its_budget(target, tmp_path):
    rc, out = _gate(target, SPEC_ROWS[target], tmp_path)
    assert rc == 0, out


@pytest.mark.parametrize("target", ["util_default", "util_field_noqual"])
@pytest.mark.parametrize("dim", ["lut", "ff", "bram"])
def test_inflated_past_own_cap_still_fails(target, dim, tmp_path):
    """Per-point budgets must not loosen anything: one unit over this build's own
    cap, in any dimension, is still a hard failure."""
    cap = getattr(_caps(target), dim)
    used = dict(SPEC_ROWS[target])
    used[dim] = cap + 1
    rc, out = _gate(target, used, tmp_path)
    assert rc == 1, out
    assert f"resources.{dim}" in out, out


def test_default_caps_unchanged():
    """The fallback is the REA-P2.9 default contract, not a loosened one."""
    caps = _caps("util_default")
    assert {d: getattr(caps, d) for d in DIMS} == {"lut": 1850, "ff": 2900, "bram": 6, "dsp": 0}
