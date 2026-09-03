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
    match = re.match(r"^([A-Za-z]+)\s*(\d{6})([CPcp])(\d+(?:\.\d+)?)$", clean_sym)
    if not match:
        raise ValueError(f"Invalid OCC option symbol format: {symbol_str}")

    ticker, yymmdd, opt_char, strike_digits = match.groups()

    # Parse expiration date
    dt = datetime.strptime(yymmdd, "%y%m%d")
    expiration_date = dt.strftime("%Y-%m-%d")

    # Option type
    option_type = OptionType.CALL if opt_char.upper() == 'C' else OptionType.PUT

    # Strike price
    if len(strike_digits) == 8 and '.' not in strike_digits:
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
    """Parser for broker Activity report CSV files with same-day grouping & position closing interaction."""

    @classmethod
    def extract_transactions_from_csv(cls, filepath: str) -> List[OptionLeg]:
        """Parses a single Activity CSV file and extracts raw OptionLeg transaction records."""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"Activity CSV file not found: {filepath}")

        legs = []
        with open(filepath, mode='r', encoding='utf-8-sig', errors='replace') as f:
            reader = csv.reader(f)
            header_found = False
            col_indices = {}

            for row in reader:
                if not row or len(row) < 3:
                    continue

                first_cell = row[0].strip()
                if not header_found:
                    if first_cell.lower() == "date" and len(row) >= 5:
                        header_found = True
                        for i, cell in enumerate(row):
                            col_indices[cell.strip().lower()] = i
                        continue
                    else:
                        continue

                # Skip non-data rows and footers
                if not first_cell or first_cell.lower() in ("totals", "disclosure", "the data and information"):
                    continue

                row_str = " ".join([c.strip().lower() for c in row[:3]])
                if "totals" in row_str or "disclosure" in row_str:
                    continue

                date_idx = col_indices.get("date", 0)
                desc_idx = col_indices.get("activity description", 1)
                sym_idx = col_indices.get("symbol", 2)
                qty_idx = col_indices.get("quantity", 3)
                price_idx = col_indices.get("price", 4)

                if len(row) <= max(date_idx, desc_idx, sym_idx, qty_idx, price_idx):
                    continue

                date_val = row[date_idx].strip()
                desc_val = row[desc_idx].strip().upper()
                sym_val = row[sym_idx].strip()
                qty_val = row[qty_idx].strip()
                price_val = row[price_idx].strip()

                comm_val = 0.0
                if "commission" in col_indices and col_indices["commission"] < len(row):
                    c_str = row[col_indices["commission"]].strip().replace("$", "").replace(",", "")
                    if c_str and c_str != "--":
                        try:
                            comm_val = float(c_str)
                        except ValueError:
                            pass

                fees_val = 0.0
                if "fees" in col_indices and col_indices["fees"] < len(row):
                    f_str = row[col_indices["fees"]].strip().replace("$", "").replace(",", "")
                    if f_str and f_str != "--":
                        try:
                            fees_val = float(f_str)
                        except ValueError:
                            pass

                # Classify transaction action
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
                    continue  # Skip non-option trades

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

                leg = OptionLeg(
                    strike=occ_info["strike"],
                    option_type=occ_info["option_type"],
                    side=side,
                    quantity=qty,
                    entry_price=price,
                    current_price=price,
                    expiration_date=occ_info["expiration_date"],
                    action=action.value,
                    trade_date=trade_date,
                    commission=comm_val,
                    fees=fees_val,
                    occ_symbol=occ_info["occ_symbol"]
                )
                legs.append(leg)

        return legs

    @staticmethod
    def get_open_contract_balance(chain: OptionsChain, occ_symbol: str) -> Tuple[int, int]:
        """Calculates (open_long_contracts, open_short_contracts) remaining for a specific contract."""
        open_long = sum(
            l.quantity for l in chain.legs 
            if l.occ_symbol == occ_symbol and l.action in (TradeAction.BUY_TO_OPEN.value, None)
        )
        closed_long = sum(
            l.quantity for l in chain.legs 
            if l.occ_symbol == occ_symbol and l.action == TradeAction.SELL_TO_CLOSE.value
        )

        open_short = sum(
            l.quantity for l in chain.legs 
            if l.occ_symbol == occ_symbol and l.action in (TradeAction.SELL_TO_OPEN.value, None)
        )
        closed_short = sum(
            l.quantity for l in chain.legs 
            if l.occ_symbol == occ_symbol and l.action == TradeAction.BUY_TO_CLOSE.value
        )

        rem_long = max(0, open_long - closed_long)
        rem_short = max(0, open_short - closed_short)
        return rem_long, rem_short

    @classmethod
    def is_chain_active(cls, chain: OptionsChain) -> bool:
        """Determines if a chain has any remaining open contracts."""
        occ_symbols = {l.occ_symbol for l in chain.legs if l.occ_symbol}
        if not occ_symbols:
            return True
        for occ in occ_symbols:
            rem_long, rem_short = cls.get_open_contract_balance(chain, occ)
            if rem_long > 0 or rem_short > 0:
                return True
        return False

    @classmethod
    def build_chains_from_legs(
        cls, 
        legs: List[OptionLeg], 
        existing_chains: Optional[List[OptionsChain]] = None
    ) -> List[OptionsChain]:
        """
        Groups legs by same-day underlying for opening trades and rolls,
        and matches closing trades to active chains.
        """
        # Deduplicate legs by tx_hash
        unique_legs_map: Dict[str, OptionLeg] = {}
        for leg in legs:
            if leg.tx_hash:
                unique_legs_map[leg.tx_hash] = leg

        sorted_legs = sorted(
            unique_legs_map.values(),
            key=lambda l: (l.trade_date or "", 0 if "CLOSE" in (l.action or "") else 1)
        )

        from collections import defaultdict
        date_symbol_legs = defaultdict(lambda: defaultdict(list))
        for l in sorted_legs:
            sym = parse_occ_symbol(l.occ_symbol)["symbol"]
            date_symbol_legs[l.trade_date or ""][sym].append(l)

        chains_pool: List[OptionsChain] = []
        if existing_chains:
            chains_pool.extend(existing_chains)

        for dt in sorted(date_symbol_legs.keys()):
            for sym, d_legs in date_symbol_legs[dt].items():
                # Filter out legs already present in chains_pool
                new_d_legs = []
                for l in d_legs:
                    already_present = any(
                        any(el.tx_hash == l.tx_hash for el in c.legs if el.tx_hash)
                        for c in chains_pool
                    )
                    if not already_present:
                        new_d_legs.append(l)

                if not new_d_legs:
                    continue

                open_legs = [l for l in new_d_legs if "OPEN" in (l.action or "")]
                close_legs = [l for l in new_d_legs if "CLOSE" in (l.action or "")]

                # Check if closing legs match an existing active chain in chains_pool
                target_chain = None
                for cl in close_legs:
                    for c in reversed(chains_pool):
                        if c.symbol == sym and c.active:
                            rem_l, rem_s = cls.get_open_contract_balance(c, cl.occ_symbol)
                            if cl.action == TradeAction.BUY_TO_CLOSE.value and rem_s > 0:
                                target_chain = c
                                break
                            elif cl.action == TradeAction.SELL_TO_CLOSE.value and rem_l > 0:
                                target_chain = c
                                break
                    if target_chain:
                        break

                if target_chain:
                    # Add closing legs to matched active chain
                    for l in close_legs:
                        target_chain.add_leg(l)
                    # If there are also opening legs on this same roll date, add them to this continuing chain!
                    for l in open_legs:
                        target_chain.add_leg(l)
                else:
                    if open_legs:
                        # Same-day opening and closing trades exist -> unified into same strategy chain!
                        name = f"{sym} {dt} Strategy"
                        existing_same_name = next((c for c in chains_pool if c.name == name and c.active), None)
                        if existing_same_name:
                            target_chain = existing_same_name
                        else:
                            target_chain = OptionsChain(symbol=sym, name=name, active=True, opened_date=dt)
                            chains_pool.append(target_chain)

                        for l in close_legs:
                            target_chain.add_leg(l)
                        for l in open_legs:
                            target_chain.add_leg(l)
                    else:
                        # Only closing legs without opening trades -> standalone closing chain
                        name = f"{sym} {dt} Closing"
                        existing_close_name = next((c for c in chains_pool if c.name == name), None)
                        if existing_close_name:
                            target_chain = existing_close_name
                        else:
                            target_chain = OptionsChain(symbol=sym, name=name, active=False, opened_date=dt, closed_date=dt)
                            chains_pool.append(target_chain)

                        for l in close_legs:
                            target_chain.add_leg(l)

        # Final pass: sort legs within each chain by date and re-evaluate active/closed status
        for c in chains_pool:
            c.legs.sort(key=lambda l: (l.trade_date or "", 0 if "OPEN" in (l.action or "") else 1))
            if c.legs:
                open_dates = [l.trade_date for l in c.legs if l.trade_date and "OPEN" in (l.action or "")]
                c.opened_date = min(open_dates) if open_dates else c.legs[0].trade_date
                if not cls.is_chain_active(c):
                    c.active = False
                    close_dates = [l.trade_date for l in c.legs if l.trade_date and "CLOSE" in (l.action or "")]
                    c.closed_date = max(close_dates) if close_dates else c.legs[-1].trade_date
                else:
                    c.active = True
                    c.closed_date = None

        return chains_pool

    @classmethod
    def parse_csv_file(cls, filepath: str) -> List[OptionsChain]:
        """Parses a single Activity CSV file and returns constructed OptionsChain objects."""
        legs = cls.extract_transactions_from_csv(filepath)
        return cls.build_chains_from_legs(legs)

    @classmethod
    def import_sources_folder(
        cls, 
        sources_dir: str = "sources", 
        storage: Optional[ChainStorage] = None
    ) -> Dict[str, Any]:
        """
        Scans sources directory for Activity*.csv files, deduplicates transactions via SHA-256 tx_hash,
        groups same-day opening trades by underlying, matches closing trades to active chains,
        and updates SQLite storage.
        """
        if not os.path.exists(sources_dir):
            return {"processed_files": 0, "new_legs": 0, "skipped_duplicates": 0, "chains": []}

        pattern = os.path.join(sources_dir, "Activity*.csv")
        files = sorted(glob.glob(pattern))
        if not files:
            return {"processed_files": 0, "new_legs": 0, "skipped_duplicates": 0, "chains": []}

        all_legs: List[OptionLeg] = []
        for f in files:
            all_legs.extend(cls.extract_transactions_from_csv(f))

        existing_hashes: Set[str] = set()
        existing_chains: List[OptionsChain] = []
        if storage:
            existing_hashes = storage.get_existing_tx_hashes()
            # Load active chains from database
            for meta in storage.list_chains():
                if meta.get("active", 1):
                    loaded = storage.get_chain(meta["id"])
                    if loaded:
                        existing_chains.append(loaded)

        # Count new legs vs duplicates
        new_legs_count = sum(1 for l in all_legs if l.tx_hash and l.tx_hash not in existing_hashes)
        skipped_duplicates_count = sum(1 for l in all_legs if l.tx_hash and l.tx_hash in existing_hashes)

        # Build and match chains
        resolved_chains = cls.build_chains_from_legs(all_legs, existing_chains=existing_chains)

        if storage:
            for chain in resolved_chains:
                existing_db = storage.get_chain_by_name(chain.name)
                if existing_db:
                    chain.id = existing_db.id
                storage.save_chain(chain)

        return {
            "processed_files": len(files),
            "new_legs": new_legs_count,
            "skipped_duplicates": skipped_duplicates_count,
            "chains": resolved_chains
        }
