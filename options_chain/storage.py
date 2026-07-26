import sqlite3
import csv
import os
from typing import List, Optional, Dict, Any
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
        """Initializes database schema if tables do not exist."""
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
                    FOREIGN KEY (chain_id) REFERENCES chains (id) ON DELETE CASCADE
                );
            """)
            conn.commit()

    def save_chain(self, chain: OptionsChain) -> int:
        """Saves or updates an options chain in SQLite."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if chain.id is not None:
                # Update existing chain header
                cursor.execute("""
                    UPDATE chains 
                    SET symbol = ?, name = ?, underlying_entry_price = ?, underlying_current_price = ?,
                        shares = ?, share_entry_price = ?, share_current_price = ?
                    WHERE id = ?
                """, (
                    chain.symbol, chain.name, chain.underlying_entry_price, chain.underlying_current_price,
                    chain.shares, chain.share_entry_price, chain.share_current_price, chain.id
                ))
                chain_id = chain.id
                # Clear existing legs to re-insert updated legs
                cursor.execute("DELETE FROM legs WHERE chain_id = ?", (chain_id,))
            else:
                # Insert new chain header
                cursor.execute("""
                    INSERT INTO chains (symbol, name, underlying_entry_price, underlying_current_price, shares, share_entry_price, share_current_price)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    chain.symbol, chain.name, chain.underlying_entry_price, chain.underlying_current_price,
                    chain.shares, chain.share_entry_price, chain.share_current_price
                ))
                chain_id = cursor.lastrowid
                chain.id = chain_id

            # Insert legs
            for leg in chain.legs:
                cursor.execute("""
                    INSERT INTO legs (chain_id, strike, option_type, side, quantity, entry_price, current_price, expiration_date, multiplier)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    chain_id, leg.strike, leg.option_type.value, leg.side.value,
                    leg.quantity, leg.entry_price, leg.current_price, leg.expiration_date, leg.multiplier
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

            chain = OptionsChain(
                id=row['id'],
                symbol=row['symbol'],
                name=row['name'],
                underlying_entry_price=row['underlying_entry_price'],
                underlying_current_price=row['underlying_current_price'],
                shares=row['shares'],
                share_entry_price=row['share_entry_price'],
                share_current_price=row['share_current_price']
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
                    multiplier=leg_row['multiplier']
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
                SELECT c.id, c.symbol, c.name, c.created_at, COUNT(l.id) as leg_count
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

    @staticmethod
    def export_to_csv(chain: OptionsChain, filepath: str) -> None:
        """Exports an options chain to a CSV file."""
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        with open(filepath, mode='w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            # Metadata header lines
            writer.writerow(["# METADATA", "SYMBOL", chain.symbol, "NAME", chain.name or "", "SHARES", chain.shares, "SHARE_ENTRY", chain.share_entry_price])
            # Leg columns
            writer.writerow(["side", "quantity", "option_type", "strike", "entry_price", "current_price", "expiration_date", "multiplier"])
            for leg in chain.legs:
                writer.writerow([
                    leg.side.value,
                    leg.quantity,
                    leg.option_type.value,
                    leg.strike,
                    leg.entry_price,
                    leg.current_price if leg.current_price is not None else leg.entry_price,
                    leg.expiration_date or "",
                    leg.multiplier
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

        legs = []
        with open(filepath, mode='r', encoding='utf-8') as f:
            reader = csv.reader(f)
            for row in reader:
                if not row or len(row) == 0:
                    continue
                if row[0] == "# METADATA":
                    if len(row) >= 3 and row[1] == "SYMBOL" and row[2]:
                        chain_symbol = symbol or row[2]
                    if len(row) >= 5 and row[3] == "NAME" and row[4]:
                        chain_name = name or row[4]
                    if len(row) >= 7 and row[5] == "SHARES" and row[6]:
                        shares = int(row[6])
                    if len(row) >= 9 and row[7] == "SHARE_ENTRY" and row[8]:
                        share_entry = float(row[8])
                    continue
                if row[0].lower() == "side":
                    continue  # Table header row

                # Parse leg row: side, quantity, option_type, strike, entry_price, current_price, expiration_date, multiplier
                if len(row) >= 5:
                    side_str = row[0].strip().upper()
                    qty = int(row[1])
                    opt_type_str = row[2].strip().upper()
                    strike = float(row[3])
                    entry_price = float(row[4])
                    cur_price = float(row[5]) if len(row) > 5 and row[5].strip() else entry_price
                    exp_date = row[6].strip() if len(row) > 6 and row[6].strip() else None
                    mult = float(row[7]) if len(row) > 7 and row[7].strip() else 100.0

                    leg = OptionLeg(
                        strike=strike,
                        option_type=OptionType(opt_type_str),
                        side=OptionSide(side_str),
                        quantity=qty,
                        entry_price=entry_price,
                        current_price=cur_price,
                        expiration_date=exp_date,
                        multiplier=mult
                    )
                    legs.append(leg)

        return OptionsChain(
            symbol=chain_symbol,
            name=chain_name,
            legs=legs,
            shares=shares,
            share_entry_price=share_entry
        )
