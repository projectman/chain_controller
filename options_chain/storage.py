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
            if chain.id is not None:
                # Update existing chain header
                cursor.execute("""
                    UPDATE chains 
                    SET symbol = ?, name = ?, underlying_entry_price = ?, underlying_current_price = ?,
                        shares = ?, share_entry_price = ?, share_current_price = ?,
                        active = ?, opened_date = ?, closed_date = ?
                    WHERE id = ?
                """, (
                    chain.symbol, chain.name, chain.underlying_entry_price, chain.underlying_current_price,
                    chain.shares, chain.share_entry_price, chain.share_current_price,
                    active_val, chain.opened_date, chain.closed_date, chain.id
                ))
                chain_id = chain.id
                # Clear existing legs to re-insert updated legs
                cursor.execute("DELETE FROM legs WHERE chain_id = ?", (chain_id,))
            else:
                # Insert new chain header
                cursor.execute("""
                    INSERT INTO chains (symbol, name, underlying_entry_price, underlying_current_price, shares, share_entry_price, share_current_price, active, opened_date, closed_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    chain.symbol, chain.name, chain.underlying_entry_price, chain.underlying_current_price,
                    chain.shares, chain.share_entry_price, chain.share_current_price,
                    active_val, chain.opened_date, chain.closed_date
                ))
                chain_id = cursor.lastrowid
                chain.id = chain_id

            # Insert legs
            for leg in chain.legs:
                cursor.execute("""
                    INSERT INTO legs (chain_id, strike, option_type, side, quantity, entry_price, current_price, expiration_date, multiplier, action, trade_date, commission, fees, occ_symbol, tx_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    chain_id, leg.strike, leg.option_type.value, leg.side.value,
                    leg.quantity, leg.entry_price, leg.current_price, leg.expiration_date, leg.multiplier,
                    leg.action, leg.trade_date, leg.commission, leg.fees, leg.occ_symbol, leg.tx_hash
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
                closed_date=row['closed_date']
            )

            cursor.execute("SELECT * FROM legs WHERE chain_id = ?", (chain_id,))
            for leg_row in cursor.fetchall():
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
                    tx_hash=leg_row['tx_hash']
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

    def list_chains(self) -> List[Dict[str, Any]]:
        """Lists summary metadata of all saved options chains."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT c.id, c.symbol, c.name, c.active, c.opened_date, c.closed_date, c.created_at, COUNT(l.id) as leg_count
                FROM chains c
                LEFT JOIN legs l ON c.id = l.chain_id
                GROUP BY c.id
                ORDER BY c.created_at DESC
            """)
            return [dict(r) for r in cursor.fetchall()]

    def delete_chain(self, chain_id: int) -> bool:
        """Deletes a chain and its associated legs."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
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
