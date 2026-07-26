import csv
import glob
import os
import re
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, Set
from .models import OptionsChain, OptionLeg, OptionType, OptionSide, TradeAction
from .storage import ChainStorage


def parse_date_to_iso(date_str: str) -> str:
    """Converts dates like 'Jul-14-2026', '07/14/2026', or '2026-07-14' to ISO format 'YYYY-MM-DD'."""
    date_str = date_str.strip()
    for fmt in ("%b-%d-%Y", "%m/%d/%Y", "%Y-%m-%d", "%d-%b-%Y", "%b %d %Y"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            pass
    return date_str


def parse_occ_symbol(symbol_str: str) -> Dict[str, Any]:
    """
    Parses OCC option symbol string into ticker, expiration_date, option_type, and strike price.
    Examples:
    - 'IBM260724P200' -> symbol='IBM', expiration='2026-07-24', option_type=PUT, strike=200.0
    - 'IBM260724P00200000' -> symbol='IBM', expiration='2026-07-24', option_type=PUT, strike=200.0
    """
    clean_sym = symbol_str.strip()
    match = re.match(r"^([A-Za-z]+)\s*(\d{6})([CPcp])(\d+)$", clean_sym)
    if not match:
        raise ValueError(f"Invalid OCC option symbol format: {symbol_str}")

    ticker, yymmdd, opt_char, strike_digits = match.groups()

    # Parse expiration date
    dt = datetime.strptime(yymmdd, "%y%m%d")
    expiration_date = dt.strftime("%Y-%m-%d")

    # Option type
    option_type = OptionType.CALL if opt_char.upper() == 'C' else OptionType.PUT

    # Strike price
    if len(strike_digits) == 8:
        strike = float(strike_digits) / 1000.0
    else:
        strike = float(strike_digits)

    return {
        "symbol": ticker.upper(),
        "expiration_date": expiration_date,
        "option_type": option_type,
        "strike": strike,
        "occ_symbol": clean_sym
    }


class ActivityParser:
    """Parser for broker Activity report CSV files (e.g. Fidelity Activity*.csv)."""

    @classmethod
    def parse_csv_file(cls, filepath: str) -> List[OptionsChain]:
        """Parses a single Activity CSV file and returns constructed OptionsChain objects."""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Activity CSV file not found: {filepath}")

        rows = []
        with open(filepath, mode='r', encoding='utf-8-sig', errors='replace') as f:
            reader = csv.reader(f)
            header_found = False
            col_indices = {}

            for row in reader:
                if not row or len(row) < 3:
                    continue

                # Check for table header row
                first_cell = row[0].strip()
                if not header_found:
                    if first_cell.lower() == "date" and len(row) >= 5:
                        header_found = True
                        for i, cell in enumerate(row):
                            col_indices[cell.strip().lower()] = i
                        continue
                    else:
                        continue

                # Skip non-data rows
                if not first_cell or first_cell.lower() in ("totals", "disclosure", "the data and information"):
                    continue

                # Skip footer lines containing "totals" anywhere in the first few cells
                row_str = " ".join([c.strip().lower() for c in row[:3]])
                if "totals" in row_str or "disclosure" in row_str:
                    continue

                rows.append((row, col_indices))

        # Group option transactions by (underlying_symbol, expiration_date)
        groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}

        for row, idx_map in rows:
            date_idx = idx_map.get("date", 0)
            desc_idx = idx_map.get("activity description", 1)
            sym_idx = idx_map.get("symbol", 2)
            qty_idx = idx_map.get("quantity", 3)
            price_idx = idx_map.get("price", 4)

            if len(row) <= max(date_idx, desc_idx, sym_idx, qty_idx, price_idx):
                continue

            date_val = row[date_idx].strip()
            desc_val = row[desc_idx].strip().upper()
            sym_val = row[sym_idx].strip()
            qty_val = row[qty_idx].strip()
            price_val = row[price_idx].strip()

            comm_val = 0.0
            if "commission" in idx_map and idx_map["commission"] < len(row):
                c_str = row[idx_map["commission"]].strip().replace("$", "").replace(",", "")
                if c_str and c_str != "--":
                    try:
                        comm_val = float(c_str)
                    except ValueError:
                        pass

            fees_val = 0.0
            if "fees" in idx_map and idx_map["fees"] < len(row):
                f_str = row[idx_map["fees"]].strip().replace("$", "").replace(",", "")
                if f_str and f_str != "--":
                    try:
                        fees_val = float(f_str)
                    except ValueError:
                        pass

            # Determine transaction action
            action = None
            if "SOLD OPENING" in desc_val:
                action = TradeAction.SELL_TO_OPEN
                side = OptionSide.SELL
            elif "BOUGHT OPENING" in desc_val:
                action = TradeAction.BUY_TO_OPEN
                side = OptionSide.BUY
            elif "BOUGHT CLOSING" in desc_val:
                action = TradeAction.BUY_TO_CLOSE
                side = OptionSide.BUY
            elif "SOLD CLOSING" in desc_val:
                action = TradeAction.SELL_TO_CLOSE
                side = OptionSide.SELL

            if not action:
                continue  # Skip non-option transactions

            # Parse OCC Symbol
            try:
                occ_info = parse_occ_symbol(sym_val)
            except ValueError:
                continue

            trade_date = parse_date_to_iso(date_val)
            try:
                qty = abs(int(float(qty_val)))
                price = abs(float(price_val.replace("$", "").replace(",", "")))
            except ValueError:
                continue

            key = (occ_info["symbol"], occ_info["expiration_date"])
            if key not in groups:
                groups[key] = []

            groups[key].append({
                "trade_date": trade_date,
                "action": action,
                "side": side,
                "quantity": qty,
                "entry_price": price,
                "commission": comm_val,
                "fees": fees_val,
                "occ_info": occ_info
            })

        # Construct OptionsChain objects
        chains: List[OptionsChain] = []
        for (symbol, expiration), tx_list in groups.items():
            tx_list.sort(key=lambda x: x["trade_date"])

            opened_date = tx_list[0]["trade_date"]
            latest_trade_date = tx_list[-1]["trade_date"]

            net_contracts = 0
            legs = []

            for tx in tx_list:
                act = tx["action"]
                q = tx["quantity"]
                if act in (TradeAction.BUY_TO_OPEN, TradeAction.BUY_TO_CLOSE):
                    net_contracts += q
                else:  # SELL_TO_OPEN, SELL_TO_CLOSE
                    net_contracts -= q

                occ = tx["occ_info"]
                leg = OptionLeg(
                    strike=occ["strike"],
                    option_type=occ["option_type"],
                    side=tx["side"],
                    quantity=tx["quantity"],
                    entry_price=tx["entry_price"],
                    current_price=tx["entry_price"],
                    expiration_date=occ["expiration_date"],
                    action=act.value,
                    trade_date=tx["trade_date"],
                    commission=tx["commission"],
                    fees=tx["fees"],
                    occ_symbol=occ["occ_symbol"]
                )
                legs.append(leg)

            is_active = (net_contracts != 0)
            closed_date = None if is_active else latest_trade_date

            strategy_name = f"{symbol} {expiration} Chain"
            chain = OptionsChain(
                symbol=symbol,
                name=strategy_name,
                legs=legs,
                active=is_active,
                opened_date=opened_date,
                closed_date=closed_date
            )
            chains.append(chain)

        return chains

    @classmethod
    def import_sources_folder(cls, sources_dir: str = "sources", storage: Optional[ChainStorage] = None) -> Dict[str, Any]:
        """
        Scans sources directory for Activity*.csv files, deduplicates transactions via SHA-256 tx_hash,
        and updates SQLite storage.
        Returns dictionary with import statistics.
        """
        if not os.path.exists(sources_dir):
            return {"processed_files": 0, "new_legs": 0, "skipped_duplicates": 0, "chains": []}

        pattern = os.path.join(sources_dir, "Activity*.csv")
        files = glob.glob(pattern)

        all_parsed_chains: List[OptionsChain] = []
        for f in sorted(files):
            chains = cls.parse_csv_file(f)
            all_parsed_chains.extend(chains)

        if not storage:
            return {"processed_files": len(files), "new_legs": 0, "skipped_duplicates": 0, "chains": all_parsed_chains}

        existing_hashes: Set[str] = storage.get_existing_tx_hashes()
        new_legs_count = 0
        skipped_duplicates_count = 0
        saved_chains: List[OptionsChain] = []

        for parsed_chain in all_parsed_chains:
            # Check if target chain already exists in DB
            db_chain = storage.get_chain_by_name(parsed_chain.name)
            existing_legs = db_chain.legs if db_chain else []

            # Filter legs by tx_hash deduplication
            merged_legs_map: Dict[str, OptionLeg] = {}
            for leg in existing_legs:
                if leg.tx_hash:
                    merged_legs_map[leg.tx_hash] = leg

            for leg in parsed_chain.legs:
                if leg.tx_hash in existing_hashes or leg.tx_hash in merged_legs_map:
                    skipped_duplicates_count += 1
                else:
                    new_legs_count += 1
                    existing_hashes.add(leg.tx_hash)
                    merged_legs_map[leg.tx_hash] = leg

            # Re-evaluate chain active state and dates based on all merged unique legs
            merged_legs = list(merged_legs_map.values())
            if not merged_legs:
                continue

            merged_legs.sort(key=lambda x: x.trade_date or "")
            opened_date = merged_legs[0].trade_date
            latest_trade_date = merged_legs[-1].trade_date

            net_contracts = 0
            for leg in merged_legs:
                q = leg.quantity
                if leg.action in (TradeAction.BUY_TO_OPEN.value, TradeAction.BUY_TO_CLOSE.value):
                    net_contracts += q
                else:
                    net_contracts -= q

            is_active = (net_contracts != 0)
            closed_date = None if is_active else latest_trade_date

            target_chain = OptionsChain(
                id=db_chain.id if db_chain else None,
                symbol=parsed_chain.symbol,
                name=parsed_chain.name,
                legs=merged_legs,
                active=is_active,
                opened_date=opened_date,
                closed_date=closed_date
            )
            storage.save_chain(target_chain)
            saved_chains.append(target_chain)

        return {
            "processed_files": len(files),
            "new_legs": new_legs_count,
            "skipped_duplicates": skipped_duplicates_count,
            "chains": saved_chains
        }
