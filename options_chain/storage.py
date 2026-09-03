import sqlite3
import csv
import os
from typing import List, Optional, Dict, Any, Set
from .models import OptionsChain, OptionLeg, OptionType, OptionSide


class ChainStorage:
    """SQLite Database & CSV Storage Manager for Options Chains."""

    def __init__(self, db_path: str = "options_chains.db"):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initializes database schema if tables do not exist and runs column migrations."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chains (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    name TEXT,
                    underlying_entry_price REAL,
                    underlying_current_price REAL,
                    shares INTEGER DEFAULT 0,
                    share_entry_price REAL DEFAULT 0.0,
                    share_current_price REAL DEFAULT 0.0,
                    active INTEGER DEFAULT 1,
                    opened_date TEXT,
                    closed_date TEXT,
                    deleted INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS legs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chain_id INTEGER NOT NULL,
                    strike REAL NOT NULL,
                    option_type TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    current_price REAL,
                    expiration_date TEXT,
                    multiplier REAL DEFAULT 100.0,
                    action TEXT,
                    trade_date TEXT,
                    commission REAL DEFAULT 0.0,
                    fees REAL DEFAULT 0.0,
                    occ_symbol TEXT,
                    tx_hash TEXT,
                    deleted INTEGER DEFAULT 0,
                    FOREIGN KEY (chain_id) REFERENCES chains (id) ON DELETE CASCADE
                );
            """)

            # Auto-migrate existing database tables if missing columns
            cursor.execute("PRAGMA table_info(chains)")
            chain_cols = {row['name'] for row in cursor.fetchall()}
            if 'active' not in chain_cols:
                cursor.execute("ALTER TABLE chains ADD COLUMN active INTEGER DEFAULT 1")
            if 'opened_date' not in chain_cols:
                cursor.execute("ALTER TABLE chains ADD COLUMN opened_date TEXT")
            if 'closed_date' not in chain_cols:
                cursor.execute("ALTER TABLE chains ADD COLUMN closed_date TEXT")
            if 'deleted' not in chain_cols:
                cursor.execute("ALTER TABLE chains ADD COLUMN deleted INTEGER DEFAULT 0")

            cursor.execute("PRAGMA table_info(legs)")
            leg_cols = {row['name'] for row in cursor.fetchall()}
            if 'action' not in leg_cols:
                cursor.execute("ALTER TABLE legs ADD COLUMN action TEXT")
            if 'trade_date' not in leg_cols:
                cursor.execute("ALTER TABLE legs ADD COLUMN trade_date TEXT")
            if 'commission' not in leg_cols:
                cursor.execute("ALTER TABLE legs ADD COLUMN commission REAL DEFAULT 0.0")
            if 'fees' not in leg_cols:
                cursor.execute("ALTER TABLE legs ADD COLUMN fees REAL DEFAULT 0.0")
            if 'occ_symbol' not in leg_cols:
                cursor.execute("ALTER TABLE legs ADD COLUMN occ_symbol TEXT")
            if 'tx_hash' not in leg_cols:
                cursor.execute("ALTER TABLE legs ADD COLUMN tx_hash TEXT")
            if 'deleted' not in leg_cols:
                cursor.execute("ALTER TABLE legs ADD COLUMN deleted INTEGER DEFAULT 0")

            # Unique index for deduplicating imported transaction hashes
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_legs_tx_hash 
                ON legs(tx_hash) WHERE tx_hash IS NOT NULL;
            """)

            conn.commit()

    def get_existing_tx_hashes(self) -> Set[str]:
        """Returns a set of all transaction hashes currently saved in SQLite database."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT tx_hash FROM legs WHERE tx_hash IS NOT NULL")
            return {row['tx_hash'] for row in cursor.fetchall()}

    def save_chain(self, chain: OptionsChain) -> int:
        """Saves or updates an options chain in SQLite."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            active_val = 1 if chain.active else 0
            deleted_val = 1 if getattr(chain, 'deleted', False) else 0

            # If chain.id is None, check if any of its legs are already associated with an existing chain
            if chain.id is None:
                for leg in chain.legs:
                    if leg.tx_hash:
                        cursor.execute("SELECT chain_id FROM legs WHERE tx_hash = ?", (leg.tx_hash,))
                        r = cursor.fetchone()
                        if r:
                            chain.id = r['chain_id']
                            break

            if chain.id is not None:
                # Update existing chain header
                cursor.execute("""
                    UPDATE chains 
                    SET symbol = ?, name = ?, underlying_entry_price = ?, underlying_current_price = ?,
                        shares = ?, share_entry_price = ?, share_current_price = ?,
                        active = ?, opened_date = ?, closed_date = ?, deleted = ?
                    WHERE id = ?
                """, (
                    chain.symbol, chain.name, chain.underlying_entry_price, chain.underlying_current_price,
                    chain.shares, chain.share_entry_price, chain.share_current_price,
                    active_val, chain.opened_date, chain.closed_date, deleted_val, chain.id
                ))
                chain_id = chain.id
                # Clear existing legs to re-insert updated legs
                cursor.execute("DELETE FROM legs WHERE chain_id = ?", (chain_id,))
            else:
                # Insert new chain header
                cursor.execute("""
                    INSERT INTO chains (symbol, name, underlying_entry_price, underlying_current_price, shares, share_entry_price, share_current_price, active, opened_date, closed_date, deleted)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    chain.symbol, chain.name, chain.underlying_entry_price, chain.underlying_current_price,
                    chain.shares, chain.share_entry_price, chain.share_current_price,
                    active_val, chain.opened_date, chain.closed_date, deleted_val
                ))
                chain_id = cursor.lastrowid
                chain.id = chain_id

            # Insert legs
            for leg in chain.legs:
                leg_deleted = 1 if getattr(leg, 'deleted', False) else 0
                cursor.execute("""
                    INSERT OR REPLACE INTO legs (chain_id, strike, option_type, side, quantity, entry_price, current_price, expiration_date, multiplier, action, trade_date, commission, fees, occ_symbol, tx_hash, deleted)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    chain_id, leg.strike, leg.option_type.value, leg.side.value,
                    leg.quantity, leg.entry_price, leg.current_price, leg.expiration_date, leg.multiplier,
                    leg.action, leg.trade_date, leg.commission, leg.fees, leg.occ_symbol, leg.tx_hash, leg_deleted
                ))
                leg.id = cursor.lastrowid

            conn.commit()
            return chain_id

    def get_chain(self, chain_id: int) -> Optional[OptionsChain]:
        """Retrieves a chain by ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM chains WHERE id = ?", (chain_id,))
            row = cursor.fetchone()
            if not row:
                return None

            active_bool = bool(row['active']) if row['active'] is not None else True
            deleted_bool = bool(row['deleted']) if 'deleted' in row.keys() and row['deleted'] is not None else False

            chain = OptionsChain(
                id=row['id'],
                symbol=row['symbol'],
                name=row['name'],
                underlying_entry_price=row['underlying_entry_price'],
                underlying_current_price=row['underlying_current_price'],
                shares=row['shares'],
                share_entry_price=row['share_entry_price'],
                share_current_price=row['share_current_price'],
                active=active_bool,
                opened_date=row['opened_date'],
                closed_date=row['closed_date'],
                deleted=deleted_bool
            )

            cursor.execute("SELECT * FROM legs WHERE chain_id = ?", (chain_id,))
            for leg_row in cursor.fetchall():
                leg_deleted = bool(leg_row['deleted']) if 'deleted' in leg_row.keys() and leg_row['deleted'] is not None else False
                leg = OptionLeg(
                    id=leg_row['id'],
                    strike=leg_row['strike'],
                    option_type=OptionType(leg_row['option_type']),
                    side=OptionSide(leg_row['side']),
                    quantity=leg_row['quantity'],
                    entry_price=leg_row['entry_price'],
                    current_price=leg_row['current_price'],
                    expiration_date=leg_row['expiration_date'],
                    multiplier=leg_row['multiplier'],
                    action=leg_row['action'],
                    trade_date=leg_row['trade_date'],
                    commission=leg_row['commission'] or 0.0,
                    fees=leg_row['fees'] or 0.0,
                    occ_symbol=leg_row['occ_symbol'],
                    tx_hash=leg_row['tx_hash'],
                    deleted=leg_deleted
                )
                chain.add_leg(leg)

            return chain

    def get_chain_by_name(self, name: str) -> Optional[OptionsChain]:
        """Retrieves a chain by its exact name."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM chains WHERE name = ?", (name,))
            row = cursor.fetchone()
            if row:
                return self.get_chain(row['id'])
            return None

    def get_active_chains_by_symbol(self, symbol: str) -> List[OptionsChain]:
        """Retrieves all currently active, non-deleted chains for a given underlying symbol."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM chains WHERE UPPER(symbol) = UPPER(?) AND active = 1 AND (deleted = 0 OR deleted IS NULL) ORDER BY opened_date ASC", (symbol,))
            chain_ids = [row['id'] for row in cursor.fetchall()]
            return [self.get_chain(cid) for cid in chain_ids if cid]

    def find_active_chain_for_closing_leg(self, symbol: str, occ_symbol: str, action: str) -> Optional[OptionsChain]:
        """
        Finds an active chain that has an opposite open position for the given occ_symbol.
        - If action is BUY_TO_CLOSE: looks for chain with net short contracts in occ_symbol.
        - If action is SELL_TO_CLOSE: looks for chain with net long contracts in occ_symbol.
        """
        active_chains = self.get_active_chains_by_symbol(symbol)
        for chain in active_chains:
            net_for_occ = 0
            has_occ = False
            for leg in chain.legs:
                if leg.occ_symbol == occ_symbol:
                    has_occ = True
                    if leg.action in ("BUY_TO_OPEN", "BUY_TO_CLOSE") or (not leg.action and leg.side.value == "BUY"):
                        net_for_occ += leg.quantity
                    else:
                        net_for_occ -= leg.quantity

            if action == "BUY_TO_CLOSE" and net_for_occ < 0:
                return chain
            elif action == "SELL_TO_CLOSE" and net_for_occ > 0:
                return chain
            elif has_occ:
                return chain
        return None

    def list_chains(self, include_deleted: bool = False, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Lists summary metadata of saved options chains.
        Supports status filtering: 'all' (non-deleted), 'active', 'closed', 'deleted'.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            where_clauses = []

            if status == "deleted":
                where_clauses.append("c.deleted = 1")
            elif status == "active":
                where_clauses.append("(c.deleted = 0 OR c.deleted IS NULL) AND c.active = 1")
            elif status == "closed":
                where_clauses.append("(c.deleted = 0 OR c.deleted IS NULL) AND c.active = 0")
            elif not include_deleted:
                where_clauses.append("(c.deleted = 0 OR c.deleted IS NULL)")

            where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

            query = f"""
                SELECT c.id, c.symbol, c.name, c.active, c.opened_date, c.closed_date, c.deleted, c.created_at, COUNT(l.id) as leg_count
                FROM chains c
                LEFT JOIN legs l ON c.id = l.chain_id
                {where_sql}
                GROUP BY c.id
                ORDER BY c.created_at DESC
            """
            cursor.execute(query)
            return [dict(r) for r in cursor.fetchall()]

    def soft_delete_chain(self, chain_id: int) -> bool:
        """Marks a chain as deleted (soft delete) without dropping table records."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE chains SET deleted = 1 WHERE id = ?", (chain_id,))
            conn.commit()
            return cursor.rowcount > 0

    def revert_chain(self, chain_id: int) -> bool:
        """
        Reverts a deleted chain (sets deleted = 0).
        Automatically recalculates whether the position is Active or Closed
        based on whether any open contracts remain.
        """
        chain = self.get_chain(chain_id)
        if not chain:
            return False

        # Re-evaluate remaining open contracts
        from .activity_parser import ActivityParser
        is_active = ActivityParser.is_chain_active(chain)

        latest_closing_date = None
        if not is_active:
            close_dates = [l.trade_date for l in chain.legs if l.trade_date and "CLOSE" in (l.action or "")]
            latest_closing_date = max(close_dates) if close_dates else chain.closed_date

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE chains 
                SET deleted = 0, active = ?, closed_date = ?
                WHERE id = ?
            """, (1 if is_active else 0, None if is_active else latest_closing_date, chain_id))
            conn.commit()
            return cursor.rowcount > 0

    def soft_delete_leg(self, leg_id: int) -> bool:
        """Marks an individual leg as deleted (soft delete)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE legs SET deleted = 1 WHERE id = ?", (leg_id,))
            conn.commit()
            return cursor.rowcount > 0

    def revert_leg(self, leg_id: int) -> bool:
        """Reverts a deleted leg (sets deleted = 0)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE legs SET deleted = 0 WHERE id = ?", (leg_id,))
            conn.commit()
            return cursor.rowcount > 0

    def list_simple_positions(
        self, 
        status: str = "all", 
        search: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
        """
        Retrieves a flat list of individual option legs/positions with parent chain metadata.
        Returns (filtered_positions, counts_dict).
        """
        from .activity_parser import ActivityParser

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    l.id, l.chain_id, l.strike, l.option_type, l.side, l.quantity,
                    l.entry_price, l.current_price, l.expiration_date, l.multiplier, l.action,
                    l.trade_date, l.commission, l.fees, l.occ_symbol, l.deleted as leg_deleted,
                    c.symbol, c.name as chain_name, c.active as chain_active, c.deleted as chain_deleted
                FROM legs l
                JOIN chains c ON l.chain_id = c.id
                ORDER BY l.trade_date DESC, l.id DESC
            """)
            raw_rows = cursor.fetchall()

        # Cache chains to check remaining contract balances
        chains_cache: Dict[int, Optional[OptionsChain]] = {}

        positions: List[Dict[str, Any]] = []
        counts = {"all": 0, "active": 0, "closed": 0, "deleted": 0}

        for row in raw_rows:
            leg_id = row['id']
            chain_id = row['chain_id']
            leg_deleted = bool(row['leg_deleted'])
            chain_deleted = bool(row['chain_deleted'])
            is_deleted = leg_deleted or chain_deleted

            # Calculate leg outlay (positive for debit/cost, negative for credit/proceeds)
            mult = row['multiplier'] or 100.0
            side_str = (row['side'] or 'BUY').upper()
            qty = row['quantity'] or 0
            price = row['entry_price'] or 0.0
            fees = (row['commission'] or 0.0) + (row['fees'] or 0.0)
            
            if side_str == "BUY":
                outlay = (price * qty * mult) + fees
            else:
                outlay = -(price * qty * mult) + fees

            # Determine leg status
            if is_deleted:
                leg_status = "DELETED"
            elif "CLOSE" in (row['action'] or ""):
                leg_status = "CLOSED"
            elif not row['chain_active']:
                leg_status = "CLOSED"
            else:
                if chain_id not in chains_cache:
                    chains_cache[chain_id] = self.get_chain(chain_id)
                ch = chains_cache[chain_id]
                if ch and row['occ_symbol']:
                    rem_l, rem_s = ActivityParser.get_open_contract_balance(ch, row['occ_symbol'])
                    leg_status = "ACTIVE" if (rem_l > 0 or rem_s > 0) else "CLOSED"
                else:
                    leg_status = "ACTIVE" if ("OPEN" in (row['action'] or "") or not row['action']) else "CLOSED"

            # Increment status counts
            if is_deleted:
                counts["deleted"] += 1
            else:
                counts["all"] += 1
                if leg_status == "ACTIVE":
                    counts["active"] += 1
                else:
                    counts["closed"] += 1

            pos = {
                "id": leg_id,
                "chain_id": chain_id,
                "symbol": row['symbol'],
                "chain_name": row['chain_name'],
                "strike": row['strike'],
                "option_type": row['option_type'],
                "side": side_str,
                "quantity": qty,
                "entry_price": price,
                "current_price": row['current_price'],
                "expiration_date": row['expiration_date'],
                "multiplier": mult,
                "action": row['action'],
                "trade_date": row['trade_date'],
                "commissions_and_fees": fees,
                "occ_symbol": row['occ_symbol'],
                "outlay": outlay,
                "status": leg_status,
                "deleted": is_deleted
            }
            positions.append(pos)

        # Apply status filtering
        if status == "active":
            filtered = [p for p in positions if not p["deleted"] and p["status"] == "ACTIVE"]
        elif status == "closed":
            filtered = [p for p in positions if not p["deleted"] and p["status"] == "CLOSED"]
        elif status == "deleted":
            filtered = [p for p in positions if p["deleted"]]
        else:
            filtered = [p for p in positions if not p["deleted"]]

        # Apply search query filtering
        if search:
            q = search.strip().upper()
            filtered = [
                p for p in filtered
                if q in (p["symbol"] or "").upper() or q in (p["chain_name"] or "").upper() or q in (p["occ_symbol"] or "").upper()
            ]

        return filtered, counts

    def delete_chain(self, chain_id: int) -> bool:
        """Deletes a chain and its associated legs (hard delete)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM legs WHERE chain_id = ?", (chain_id,))
            cursor.execute("DELETE FROM chains WHERE id = ?", (chain_id,))
            conn.commit()
            return cursor.rowcount > 0

    def delete_leg(self, leg_id: int) -> bool:
        """Deletes a single leg by leg ID."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM legs WHERE id = ?", (leg_id,))
            conn.commit()
            return cursor.rowcount > 0

    @staticmethod
    def export_to_csv(chain: OptionsChain, filepath: str) -> None:
        """Exports an options chain to a CSV file."""
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            # Metadata header lines
            writer.writerow(["# METADATA", "SYMBOL", chain.symbol, "NAME", chain.name or "", "ACTIVE", 1 if chain.active else 0, "OPENED_DATE", chain.opened_date or "", "CLOSED_DATE", chain.closed_date or "", "SHARES", chain.shares, "SHARE_ENTRY", chain.share_entry_price])
            # Leg columns
            writer.writerow(["side", "quantity", "option_type", "strike", "entry_price", "current_price", "expiration_date", "multiplier", "action", "trade_date", "commission", "fees", "occ_symbol", "tx_hash"])
            for leg in chain.legs:
                writer.writerow([
                    leg.side.value,
                    leg.quantity,
                    leg.option_type.value,
                    leg.strike,
                    leg.entry_price,
                    leg.current_price if leg.current_price is not None else leg.entry_price,
                    leg.expiration_date or "",
                    leg.multiplier,
                    leg.action or "",
                    leg.trade_date or "",
                    leg.commission,
                    leg.fees,
                    leg.occ_symbol or "",
                    leg.tx_hash or ""
                ])

    @staticmethod
    def import_from_csv(filepath: str, symbol: Optional[str] = None, name: Optional[str] = None) -> OptionsChain:
        """Imports an options chain from a CSV file."""
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"CSV file not found: {filepath}")

        chain_symbol = symbol or "UNKNOWN"
        chain_name = name or os.path.basename(filepath).replace(".csv", "")
        shares = 0
        share_entry = 0.0
        active = True
        opened_date = None
        closed_date = None

        legs = []
        with open(filepath, mode='r', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                if not row or len(row) == 0:
                    continue
                if row[0] == "# METADATA":
                    for idx in range(1, len(row) - 1, 2):
                        key = row[idx].upper()
                        val = row[idx+1]
                        if key == "SYMBOL" and val:
                            chain_symbol = symbol or val
                        elif key == "NAME" and val:
                            chain_name = name or val
                        elif key == "ACTIVE":
                            active = bool(int(val)) if val else True
                        elif key == "OPENED_DATE":
                            opened_date = val if val else None
                        elif key == "CLOSED_DATE":
                            closed_date = val if val else None
                        elif key == "SHARES":
                            shares = int(val) if val else 0
                        elif key == "SHARE_ENTRY":
                            share_entry = float(val) if val else 0.0
                    continue

                if row[0].lower() == "side":
                    continue  # Table header row

                # Parse leg row
                if len(row) >= 5:
                    side_str = row[0].strip().upper()
                    qty = int(row[1])
                    opt_type_str = row[2].strip().upper()
                    strike = float(row[3])
                    entry_price = float(row[4])
                    cur_price = float(row[5]) if len(row) > 5 and row[5].strip() else entry_price
                    exp_date = row[6].strip() if len(row) > 6 and row[6].strip() else None
                    mult = float(row[7]) if len(row) > 7 and row[7].strip() else 100.0
                    action_str = row[8].strip() if len(row) > 8 and row[8].strip() else None
                    t_date = row[9].strip() if len(row) > 9 and row[9].strip() else None
                    comm = float(row[10]) if len(row) > 10 and row[10].strip() else 0.0
                    fee_val = float(row[11]) if len(row) > 11 and row[11].strip() else 0.0
                    occ_sym = row[12].strip() if len(row) > 12 and row[12].strip() else None
                    tx_h = row[13].strip() if len(row) > 13 and row[13].strip() else None

                    leg = OptionLeg(
                        strike=strike,
                        option_type=OptionType(opt_type_str),
                        side=OptionSide(side_str),
                        quantity=qty,
                        entry_price=entry_price,
                        current_price=cur_price,
                        expiration_date=exp_date,
                        multiplier=mult,
                        action=action_str,
                        trade_date=t_date,
                        commission=comm,
                        fees=fee_val,
                        occ_symbol=occ_sym,
                        tx_hash=tx_h
                    )
                    legs.append(leg)

        return OptionsChain(
            symbol=chain_symbol,
            name=chain_name,
            legs=legs,
            shares=shares,
            share_entry_price=share_entry,
            active=active,
            opened_date=opened_date,
            closed_date=closed_date
        )
