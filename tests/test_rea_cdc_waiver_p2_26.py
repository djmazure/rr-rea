# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P2.26 — Upstream RR-CDC-002 quasi-static waiver on rr_rea_sync_word s1/s2.

Multi-bit words crossing clock domains through plain 2-FF synchronizers trip
RouteRTL's RR-CDC-002 check. REA trigger, qualifier, and configuration words
crossing via rr_rea_sync_word are quasi-static MCPs held stable while armed
and taking effect on arm_pulse_i (REA-REQ-959), bounded by half the faster
clock period in constraints/rr_rea_scoped.xdc/.sdc.

This test module verifies that:
1. rr_rea_sync_word s1/s2 carry the inline RR-CDC-002 waiver citing REA-REQ-959
   and bounding constraints, and rtl/rr_rea_cdc.vhd has 0 unsuppressed findings.
2. The waiver has power: stripping the waiver comment immediately produces
   unsuppressed RR-CDC-002 findings for s1 and s2.
3. The waiver is not widened: only s1/s2 in rr_rea_sync_word carry the waiver;
   no blanket file-level disables exist, and any additional multi-bit synchronizer
   signal or entity trips RR-CDC-002 as unsuppressed.
4. The entire rtl/*.vhd closure has 0 unsuppressed RR-CDC-002 findings.
"""
from __future__ import annotations

import re
from pathlib import Path

from routertl_core.lint_checkers.rr_cdc_vhdl import check_rr_cdc_002_multibit_two_ff_sync
from routertl_core.lint_runners import _line_suppresses

REPO = Path(__file__).resolve().parents[1]
CDC_VHDL = REPO / "rtl" / "rr_rea_cdc.vhd"
REQS_YML = REPO / "requirements.yml"
SCOPED_XDC = REPO / "constraints" / "rr_rea_scoped.xdc"
SCOPED_SDC = REPO / "constraints" / "rr_rea_scoped.sdc"


def _unsuppressed_findings(path: Path, text: str | None = None) -> list[tuple[int, int, str]]:
    if text is None:
        text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    findings = []
    for line_no, col, msg in check_rr_cdc_002_multibit_two_ff_sync(path, text):
        line_text = lines[line_no - 1] if 0 < line_no <= len(lines) else ""
        if not _line_suppresses("RR-CDC-002", line_text):
            findings.append((line_no, col, msg))
    return findings


def test_rea_quasi_static_mcp_waiver_present_and_clean() -> None:
    """REA-P2.26 / REA-REQ-959: rr_rea_sync_word s1/s2 are quasi-static MCPs.
    They must carry inline suppression comments citing REA-REQ-959 and bounding
    constraints, and rr_rea_cdc.vhd must have 0 unsuppressed RR-CDC-002 findings."""
    text = CDC_VHDL.read_text(encoding="utf-8", errors="replace")
    has_waiver = (
        "rr-lint-disable-line RR-CDC-002" in text
        and "REA-REQ-959" in text
        and "rr_rea_scoped" in text
    )
    if not has_waiver:
        assert False, "REA quasi-static MCP waiver missing: required waiver pragma and rationale absent in rr_rea_cdc.vhd"

    # Evidence verification: cited requirement and constraint files must exist and match
    req_text = REQS_YML.read_text(encoding="utf-8", errors="replace")
    assert "REA-REQ-959" in req_text, "REA quasi-static MCP waiver missing: cited REA-REQ-959 absent from requirements.yml"
    assert re.search(r"quasi-\s*static", req_text), "REA quasi-static MCP waiver missing: REA-REQ-959 does not specify quasi-static"

    xdc_text = SCOPED_XDC.read_text(encoding="utf-8", errors="replace")
    assert "rr_rea_sync_word" in xdc_text, "REA quasi-static MCP waiver missing: rr_rea_sync_word not constrained in scoped.xdc"

    sdc_text = SCOPED_SDC.read_text(encoding="utf-8", errors="replace")
    assert "rr_rea_sync_word" in sdc_text, "REA quasi-static MCP waiver missing: rr_rea_sync_word not constrained in scoped.sdc"

    # Both s1 and s2 specifically must carry the line-level waiver
    lines = text.splitlines()
    s1_line = next((l for l in lines if re.search(r"^\s*signal\s+s1\s*:", l)), "")
    s2_line = next((l for l in lines if re.search(r"^\s*signal\s+s2\s*:", l)), "")
    assert _line_suppresses("RR-CDC-002", s1_line), "REA quasi-static MCP waiver missing: s1 line does not suppress RR-CDC-002"
    assert _line_suppresses("RR-CDC-002", s2_line), "REA quasi-static MCP waiver missing: s2 line does not suppress RR-CDC-002"

    unsuppressed = _unsuppressed_findings(CDC_VHDL, text)
    if unsuppressed:
        assert False, f"REA quasi-static MCP waiver missing: unsuppressed findings remain in rr_rea_cdc.vhd: {unsuppressed}"


def test_waiver_removal_detected_by_linter() -> None:
    """Removing the waiver from s1, s2, or both must produce unsuppressed findings."""
    text = CDC_VHDL.read_text(encoding="utf-8", errors="replace")

    # Stripping waiver from both s1 and s2
    stripped = re.sub(r"--\s*rr-lint-disable-line\s+RR-CDC-002[^\n]*", "", text)
    findings = _unsuppressed_findings(CDC_VHDL, stripped)
    flagged_signals = {m.split("'")[1] for _, _, m in findings if "'" in m}
    assert flagged_signals == {"s1", "s2"}, f"Expected s1 and s2 flagged without waiver, got {findings}"

    # Stripping waiver from s1 only
    lines = text.splitlines()
    s1_stripped_lines = [
        re.sub(r"--\s*rr-lint-disable-line.*", "", l) if re.search(r"^\s*signal\s+s1\s*:", l) else l
        for l in lines
    ]
    findings_s1 = _unsuppressed_findings(CDC_VHDL, "\n".join(s1_stripped_lines))
    flagged_s1 = {m.split("'")[1] for _, _, m in findings_s1 if "'" in m}
    assert flagged_s1 == {"s1"}, f"Expected only s1 flagged, got {findings_s1}"

    # Stripping waiver from s2 only
    s2_stripped_lines = [
        re.sub(r"--\s*rr-lint-disable-line.*", "", l) if re.search(r"^\s*signal\s+s2\s*:", l) else l
        for l in lines
    ]
    findings_s2 = _unsuppressed_findings(CDC_VHDL, "\n".join(s2_stripped_lines))
    flagged_s2 = {m.split("'")[1] for _, _, m in findings_s2 if "'" in m}
    assert flagged_s2 == {"s2"}, f"Expected only s2 flagged, got {findings_s2}"


def test_waiver_not_widened_to_other_signals_or_entities() -> None:
    """The waiver must not be widened: only s1/s2 in rr_rea_sync_word carry the waiver;
    no blanket file-level disables exist, and any other multi-bit 2-FF sync trips RR-CDC-002."""
    text = CDC_VHDL.read_text(encoding="utf-8", errors="replace")

    # 1. No blanket file-level disables
    assert not re.search(r"--\s*rr-lint-disable\b(?!\s*-line)", text), (
        "Widened waiver: blanket file-level rr-lint-disable found in rr_rea_cdc.vhd"
    )

    # 2. Only s1 and s2 carry RR-CDC-002 line suppressions in rr_rea_cdc.vhd
    waiver_lines = [
        line.strip() for line in text.splitlines()
        if _line_suppresses("RR-CDC-002", line)
    ]
    assert len(waiver_lines) == 2, f"Expected exactly 2 waiver lines, got {len(waiver_lines)}: {waiver_lines}"
    for line in waiver_lines:
        assert re.match(r"signal\s+s[12]\s*:", line), (
            f"Widened waiver: RR-CDC-002 suppressed on non-s1/s2 line: {line}"
        )

    # 3. No other RTL file carries an RR-CDC-002 waiver
    for vhd_path in sorted((REPO / "rtl").glob("*.vhd")):
        if vhd_path == CDC_VHDL:
            continue
        vhd_text = vhd_path.read_text(encoding="utf-8", errors="replace")
        assert "RR-CDC-002" not in vhd_text, (
            f"Widened waiver: unexpected RR-CDC-002 mention in {vhd_path.name}"
        )

    # 4. Adding a non-quasi-static multi-bit synchronizer signal (w1/w2) is detected
    s2_decl = next(l for l in text.splitlines() if l.lstrip().startswith("signal s2 "))
    text_with_extra_sig = (
        text.replace(s2_decl, s2_decl + "\n" + "    signal w1 : std_logic_vector(G_WIDTH - 1 downto 0) := (others => '0');\n"
                     + "    signal w2 : std_logic_vector(G_WIDTH - 1 downto 0) := (others => '0');", 1)
        .replace('    attribute ASYNC_REG of s2 : signal is "TRUE";\n',
                 '    attribute ASYNC_REG of s2 : signal is "TRUE";\n'
                 '    attribute ASYNC_REG of w1 : signal is "TRUE";\n'
                 '    attribute ASYNC_REG of w2 : signal is "TRUE";\n', 1)
        .replace("            s2 <= s1;\n",
                 "            s2 <= s1;\n            w1 <= din_i;\n            w2 <= w1;\n", 1)
    )
    findings_extra = _unsuppressed_findings(CDC_VHDL, text_with_extra_sig)
    extra_flagged = {m.split("'")[1] for _, _, m in findings_extra if "'" in m}
    assert "w1" in extra_flagged or "w2" in extra_flagged, (
        f"Non-quasi-static signal was not flagged: {findings_extra}"
    )


def test_whole_rtl_closure_clean_under_rr_cdc_002() -> None:
    """Every VHDL source file in rtl/ must have 0 unsuppressed RR-CDC-002 findings."""
    rtl_files = sorted((REPO / "rtl").glob("*.vhd"))
    assert rtl_files, "No RTL VHDL files found"

    all_findings = []
    for path in rtl_files:
        findings = _unsuppressed_findings(path)
        if findings:
            all_findings.append((path.name, findings))

    assert not all_findings, f"Unsuppressed RR-CDC-002 findings in RTL: {all_findings}"
