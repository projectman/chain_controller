import csv
import glob
import io
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from .activity_parser import parse_date_to_iso

SUMMARY_GLOB = "Fidelity_Investment_Option_Summary_X73981319_*.csv"
_FILE_DATE_RE = re.compile(r"_(\w{3}-\d{1,2}-\d{4})\.csv$", re.IGNORECASE)
_CONTRACT_RE = re.compile(
    r"^\s*(?P<sym>[A-Za-z.]+)\s+(?P<mon>[A-Za-z]{3})\s+(?P<day>\d{1,2})\s+(?P<year>\d{4})\s+\$(?P<strike>[\d,]+(?:\.\d+)?)\s+PUT\s*$",
    re.IGNORECASE,
)


def parse_money(value: str) -> float:
    """Parses '-$1,234.50', '$6306.00', '+12.5' into float. Returns 0.0 for '--' or blanks."""
    s = (value or "").strip().replace("$", "").replace(",", "").replace("+", "")
    if not s or s == "--":
        return 0.0
    return float(s)


def parse_file_date(filepath: str) -> Optional[str]:
    """Extracts ISO date from a filename like '..._Oct-06-2026.csv'."""
    m = _FILE_DATE_RE.search(os.path.basename(filepath))
    if not m:
        return None
    iso = parse_date_to_iso(m.group(1))
    return iso if re.match(r"^\d{4}-\d{2}-\d{2}$", iso) else None


def parse_contract(text: str) -> Optional[Dict[str, Any]]:
    """Parses 'AA OCT 16 2026 $40 PUT' into symbol, ISO expiration and strike."""
    m = _CONTRACT_RE.match(text or "")
    if not m:
        return None
    try:
        exp = datetime.strptime(f"{m['mon'].title()}-{m['day']}-{m['year']}", "%b-%d-%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None
    return {
        "symbol": m["sym"].upper(),
        "expiration": exp,
        "strike": float(m["strike"].replace(",", "")),
    }


def compute_metrics(quantity: float, strike: float, cost_basis: float, margin: float) -> Dict[str, float]:
    """Max Risk = |Qty| * Strike * 100 - |Cost basis|; ratio = Margin / Max Risk."""
    max_risk = abs(quantity) * strike * 100.0 - abs(cost_basis)
    ratio = (margin / max_risk) if max_risk > 0 else 0.0
    return {"max_risk": max_risk, "margin_ratio": ratio}


def compute_totals(positions: List[Dict[str, Any]]) -> Dict[str, float]:
    total_risk = sum(p["max_risk"] for p in positions)
    total_margin = sum(p["margin"] for p in positions)
    return {
        "total_max_risk": total_risk,
        "total_margin": total_margin,
        "total_ratio": (total_margin / total_risk) if total_risk > 0 else 0.0,
    }


class MarginCalculator:
    """Reads Fidelity Option Summary files and records Naked Put positions."""

    @classmethod
    def extract_naked_puts(cls, filepath: str) -> List[Dict[str, Any]]:
        """Parses Naked Put rows only; rows of the same contract inside one file are merged."""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Option summary file not found: {filepath}")

        with open(filepath, "r", encoding="utf-8-sig", errors="replace") as f:
            text = f.read()
        # Fidelity exports use bare '\r' line endings
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        merged: Dict[tuple, Dict[str, Any]] = {}
        order: List[tuple] = []
        cols: Dict[str, int] = {}
        for row in csv.reader(io.StringIO(text)):
            if not row:
                continue
            if not cols:
                if row[0].strip().lower() == "symbol":
                    cols = {c.strip().lower(): i for i, c in enumerate(row)}
                continue

            def cell(name: str) -> str:
                i = cols.get(name)
                return row[i] if i is not None and i < len(row) else ""

            if cell("strategy").strip().lower() != "naked put":
                continue
            contract = parse_contract(cell("expiration & strike"))
            if not contract:
                continue
            try:
                qty = parse_money(cell("quantity"))
                cost = parse_money(cell("cost basis*"))
                mval = parse_money(cell("market value"))
                margin = parse_money(cell("margin requirements"))
            except ValueError:
                continue

            key = (contract["symbol"], contract["expiration"], contract["strike"])
            if key in merged:
                p = merged[key]
                p["quantity"] += qty
                p["cost_basis"] += cost
                p["market_value"] += mval
                p["margin"] += margin
                p["part_count"] += 1
            else:
                merged[key] = {**contract, "quantity": qty, "cost_basis": cost,
                               "market_value": mval, "margin": margin, "part_count": 1}
                order.append(key)

        positions = []
        for key in order:
            p = merged[key]
            p.update(compute_metrics(p["quantity"], p["strike"], p["cost_basis"], p["margin"]))
            positions.append(p)
        return positions

    @classmethod
    def import_sources_folder(cls, sources_dir: str = "sources", storage=None) -> Dict[str, Any]:
        """Imports every matching summary file; duplicates (same contract + file date) are skipped."""
        result = {"processed_files": 0, "new_positions": 0, "skipped_duplicates": 0, "warnings": []}
        if not os.path.isdir(sources_dir):
            return result
        for path in sorted(glob.glob(os.path.join(sources_dir, SUMMARY_GLOB))):
            file_date = parse_file_date(path)
            if not file_date:
                result["warnings"].append(f"No date in filename: {os.path.basename(path)}")
                continue
            positions = cls.extract_naked_puts(path)
            result["processed_files"] += 1
            if storage:
                new, skipped = storage.save_margin_positions(positions, file_date, os.path.basename(path))
            else:
                new, skipped = len(positions), 0
            result["new_positions"] += new
            result["skipped_duplicates"] += skipped
        return result
