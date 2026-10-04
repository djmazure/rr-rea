# SPDX-FileCopyrightText: 2026 Daniel J. Mazure
# SPDX-License-Identifier: MIT
"""REA-P3.14 — the package source set holds the IP, never an experiment top.

1.11.0-1.11.2 declared rtl/rr_rea_dr_guard_discriminator_top.vhd (the
REA-P2.20 debug-only reference top, three rr_rea_intel cores and no design
logic) under synthesis.sources_per_vendor.altera. ``rr pkg add`` copied it
into every consumer's ip.lock synth_only_files, so a product build on
^1.11.2 compiled an entity nothing instantiates. It now lives in
examples/dr_guard_discriminator/, outside the package.

Effect: rr's own registry-install reader resolves the manifest to the
consumer's file lists, and none of them name the discriminator.
Guard: every entity the package declares is either instantiated by another
declared file or is one of the public entry points listed below with why. A
new top-level-only entity has to be added here deliberately.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
DISCRIMINATOR = "rr_rea_dr_guard_discriminator_top.vhd"

# Entities a consumer instantiates directly; nothing inside the package does.
PUBLIC_ENTRIES = {
    "rr_rea_top": "hdl.top_module, the vendor-neutral core",
    "rr_rea_xilinx7": "Xilinx 7-series BSCANE2 wrapper",
    "rr_rea_intel": "Intel/Altera sld_virtual_jtag wrapper",
    "rr_rea_microchip": "Microchip UJTAG wrapper",
    "rr_rea_jtag_microchip": "filename-convention alias of rr_rea_microchip",
    "rr_rea_axi4lite": "AXI4-Lite register front-end (REA-P3.931)",
    "rr_rea_trig_xbar": "multi-instance trigger crossbar (RTL-P3.266)",
}


def _manifest() -> dict:
    return yaml.safe_load((REPO / "ip.yml").read_text(encoding="utf-8"))


def _declared(manifest: dict) -> set[str]:
    files = set(manifest["hdl"]["files"]) | set(manifest["synthesis"]["sources"])
    for per_vendor in manifest["synthesis"]["sources_per_vendor"].values():
        files |= set(per_vendor)
    return files


def _vhdl(rel: str) -> str:
    return re.sub(r"--[^\n]*", "", (REPO / rel).read_text(encoding="utf-8"))


def test_consumer_file_lists_have_no_discriminator():
    """What a registry consumer's ip.lock records, via rr's own reader."""
    sys.path.insert(0, str(REPO.parent / "routertl"))
    try:
        from routertl_core.ip_yml import normalize_ip_yml
        from routertl_core.package_resolver import _collect_registry_aux_files
    except ImportError:
        pytest.skip("routertl checkout not beside rr-rea (../routertl)")
    synth_only, by_vendor, _scoped, declared_aux = _collect_registry_aux_files(
        normalize_ip_yml(_manifest()), REPO, ".", Path("libs/routertl/rea"))
    lists = [synth_only, declared_aux, *by_vendor.values()]
    assert by_vendor.get("altera"), "altera wrapper vanished: reader changed?"
    for files in lists:
        assert not [f for f in files if DISCRIMINATOR in f], files


def test_discriminator_is_not_declared_and_lives_in_examples():
    assert not [f for f in _declared(_manifest()) if DISCRIMINATOR in f]
    assert not (REPO / "rtl" / DISCRIMINATOR).exists()
    assert (REPO / "examples" / "dr_guard_discriminator" / DISCRIMINATOR).is_file()


def test_every_declared_entity_is_instantiated_or_a_public_entry():
    texts = {f: _vhdl(f) for f in sorted(_declared(_manifest())) if f.endswith(".vhd")}
    orphans = []
    for rel, text in texts.items():
        for ent in re.findall(r"^\s*entity\s+(\w+)\s+is\b", text, re.I | re.M):
            name = ent.lower()
            used = any(
                re.search(rf"\bentity\s+(\w+\.)?{name}\b|:\s*{name}\b", other, re.I)
                for o, other in texts.items() if o != rel)
            if not used and name not in PUBLIC_ENTRIES:
                orphans.append(f"{name} ({rel})")
    assert not orphans, (
        "declared but never instantiated, and not a public entry point: "
        f"{orphans}. A debug/experiment top belongs in examples/; a real "
        "consumer-facing entity goes in PUBLIC_ENTRIES with its reason.")
