"""
Coerenza tra inventory.md degli agenti e _CHAINS_SEED del runner.

Se qualcuno aggiunge una catena a `scraping/runner.py` ma dimentica
l'inventory (o viceversa), questi test bloccano il merge.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]   # repo root
INVENTORY = ROOT / "agents" / "openclaw" / "skills" / "chain-scout" / "inventory.md"
RUNNER = ROOT / "scraping" / "runner.py"


def _read_inventory_section(title: str) -> str:
    """Read inventory.md and return the block under a given section heading."""
    text = INVENTORY.read_text(encoding="utf-8")
    parts = re.split(r"^## ", text, flags=re.MULTILINE)
    for part in parts:
        if part.startswith(title) or part.startswith(title.lstrip("0123456789. ")):
            return part
    raise AssertionError(f"section '{title}' not found in inventory.md")


def _slugs_from_table(block: str, slug_col: int = 0) -> set[str]:
    """Extract slugs from a markdown table column (default: first column)."""
    slugs = set()
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if not cells or cells[0] in ("Slug", "") or cells[0].startswith("-") or cells[0].startswith(":"):
            continue
        slug = cells[slug_col]
        # Plain slug (lowercase, dashes), reject if it's a markdown header
        if re.fullmatch(r"[a-z0-9][a-z0-9\-]*", slug):
            slugs.add(slug)
    return slugs


def _slugs_from_chain_seed() -> set[str]:
    text = RUNNER.read_text(encoding="utf-8")
    # Match tuples like ("Esselunga", "esselunga", True, ...
    return set(re.findall(r'\(\s*"[^"]+",\s*"([a-z0-9][a-z0-9\-]*)"\s*,', text))


def _inactive_slugs() -> set[str]:
    text = RUNNER.read_text(encoding="utf-8")
    m = re.search(r"_INACTIVE_CHAINS\s*=\s*\[(.*?)\]", text, re.DOTALL)
    if not m:
        return set()
    return set(re.findall(r'"([a-z0-9\-]+)"', m.group(1)))


# ── Tests ───────────────────────────────────────────────────────────────────

def test_inventory_in_scope_matches_chain_seed() -> None:
    """Section 1 of inventory.md must list exactly the chains in _CHAINS_SEED."""
    section = _read_inventory_section("1. Catene IN SCOPE")
    inv_slugs = _slugs_from_table(section)
    seed_slugs = _slugs_from_chain_seed() - _inactive_slugs()

    missing_in_inventory = seed_slugs - inv_slugs
    missing_in_seed = inv_slugs - seed_slugs

    assert not missing_in_inventory, (
        f"chains in _CHAINS_SEED but not in inventory IN SCOPE: {sorted(missing_in_inventory)}"
    )
    assert not missing_in_seed, (
        f"chains in inventory IN SCOPE but not in _CHAINS_SEED: {sorted(missing_in_seed)}"
    )


def test_out_of_scope_chains_are_in_inactive() -> None:
    """Section 4 (FUORI scope) must include all _INACTIVE_CHAINS."""
    section = _read_inventory_section("4. Catene FUORI scope")
    out_slugs = _slugs_from_table(section)
    inactive = _inactive_slugs()
    missing = inactive - out_slugs
    assert not missing, (
        f"chains in _INACTIVE_CHAINS but undocumented in section 4: {sorted(missing)}"
    )


def test_no_duplicate_slugs_across_sections() -> None:
    """A slug must appear in exactly one of sections 1, 2, 3, 4."""
    sections = ["1. Catene IN SCOPE", "2. Candidate da analizzare",
                "3. Catene classificate", "4. Catene FUORI scope"]
    seen: dict[str, str] = {}
    for sec in sections:
        block = _read_inventory_section(sec)
        for slug in _slugs_from_table(block):
            if slug in seen:
                pytest.fail(f"slug '{slug}' appears in both '{seen[slug]}' and '{sec}'")
            seen[slug] = sec


def test_candidates_have_required_fields() -> None:
    """Every candidate row must have Slug, Nome, Homepage."""
    section = _read_inventory_section("2. Candidate da analizzare")
    lines = [
        line for line in section.splitlines()
        if line.strip().startswith("|") and not line.strip().startswith("|-")
    ]
    # Drop the header row
    rows = [line for line in lines if not re.search(r"\bSlug\b", line)]
    for row in rows:
        cells = [c.strip() for c in row.strip("|").split("|")]
        if len(cells) < 4:
            continue
        slug, name, _group, homepage = cells[:4]
        if not re.fullmatch(r"[a-z0-9][a-z0-9\-]*", slug):
            continue
        assert name, f"empty name for {slug}"
        assert homepage, f"empty homepage for {slug}"


def test_inventory_has_all_four_sections() -> None:
    """inventory.md must keep its canonical structure — agents rely on it."""
    text = INVENTORY.read_text(encoding="utf-8")
    for n, title in enumerate([
        "1. Catene IN SCOPE",
        "2. Candidate da analizzare",
        "3. Catene classificate",
        "4. Catene FUORI scope",
    ], start=1):
        assert f"## {title}" in text, f"section {n} ('{title}') missing"


def test_skill_files_exist() -> None:
    for skill in ("chain-scout", "chain-analyzer"):
        path = ROOT / "agents" / "openclaw" / "skills" / skill / "SKILL.md"
        assert path.exists(), f"missing SKILL.md for {skill}"
        content = path.read_text(encoding="utf-8")
        # Both skills must declare hard security constraints
        assert "Vincoli di sicurezza (HARD)" in content
        # Both must explicitly refuse to push to main
        assert "main" in content.lower()
