# Options Chain Controller (`chain_controller`)

`chain_controller` is a standalone Python application that calculates total cost outlays, integrated profitability, mark-to-market performance, breakeven points, maximum gain/risk, and aggregate payoff matrices across bought and sold option calls/puts for any given underlying asset symbol.

---

## Features
- **Same-Day Underlying Strategy Grouping**: Automatically groups all opening trades (`BUY_TO_OPEN` and `SELL_TO_OPEN`) executed on the same trade date for the same underlying ticker into the same multi-leg strategy chain (e.g. Vertical Spreads, Iron Condors, Butterflies, Calendar/Diagonal spreads).
- **Active Position Closing Interaction**: When new `Activity*.csv` reports contain closing trades (`BUY_TO_CLOSE` or `SELL_TO_CLOSE`), the parser searches for currently active chains holding open opposite contracts for that OCC option symbol, appends the closing leg, and automatically marks the chain as `[CLOSED]` when all contracts reach net zero.
- **Broker Activity Report Parser & SHA-256 Deduplication**: Automatically scans and parses Fidelity/broker activity reports (`sources/Activity*.csv`). Prevents duplicate data entry using deterministic `tx_hash` SHA-256 fingerprints.
- **Active / Closed Lifecycle Tracking**: Automatically marks chains as `active` (`True`) or `closed` (`False`) based on net contract positions, tracking `opened_date` and `closed_date`.
- **Data Storage**: Uses an **SQLite database (`options_chains.db`)** with a unique index on transaction hashes (`idx_legs_tx_hash`), with **CSV import/export capabilities**. See [DATABASE_README.md](DATABASE_README.md) for full database schema details.
- **Mathematical Payoff Engine**: Piecewise linear root-finding algorithm to compute exact breakeven points at expiration.
- **CLI Suite**: Command-line interface for creating, modifying, analyzing, exporting, importing, and previewing broker transaction reports.

---

## CLI Usage (`main.py`)

### 1. Preview Raw Activity Files Directly (`sources/Activity*.csv`)
```bash
# Preview all strategy chains directly without modifying database
python main.py analyze-source

# Filter by underlying symbol
python main.py analyze-source --symbol WDC
```

### 2. Import Broker Activity Reports (`sources/Activity*.csv`)
```bash
python main.py import-sources
```
**Example Output:**
```text
=================================================================
  BROKER ACTIVITY REPORT IMPORT SUMMARY
=================================================================
  Files Processed     : 3
  New Legs Imported   : 0
  Skipped Duplicates  : 51
  Chains Updated      : 36
-----------------------------------------------------------------
  - [ACTIVE] QQQ 2026-08-31 Strategy   (3 legs) | Opened: 2026-08-31 | Closed: Open
  - [ACTIVE] WFC 2026-08-27 Strategy   (2 legs) | Opened: 2026-08-27 | Closed: Open
  - [CLOSED] WDC 2026-08-24 Strategy   (4 legs) | Opened: 2026-08-24 | Closed: 2026-08-31
  - [CLOSED] IBM 2026-07-14 Strategy   (2 legs) | Opened: 2026-07-14 | Closed: 2026-07-16
  - [CLOSED] TGT 2026-07-07 Strategy   (2 legs) | Opened: 2026-07-07 | Closed: 2026-07-15
=================================================================
```

### 3. List Active & Closed Chains
```bash
python main.py list
```

### 4. Analyze Strategy Performance & Realized PnL
```bash
# Analyze by name or by database ID
python main.py analyze --name "WDC 2026-08-24 Strategy"
python main.py analyze --name "QQQ 2026-08-31 Strategy"
```

**Example Output (Closed Put Spread):**
```text
=================================================================
  OPTIONS CHAIN ANALYSIS: WDC 2026-08-24 Strategy (WDC)
=================================================================
  Database ID      : 7
  Status           : [CLOSED]
  Opened Date      : 2026-08-24
  Closed Date      : 2026-08-31
  Total Legs       : 4
  Net Outlay       : -$249.34 (Net Credit)
  Commissions/Fees : $2.66
  Current MTM PnL  : -$2.66
-----------------------------------------------------------------
  Breakeven Points : None
  Max Profit       : $249.34
  Max Loss         : $249.34
  Risk/Reward      : 1.0
=================================================================

  POSITIONS & LEGS:
  Side   Qty  Type   Strike    Entry      Action          Trade Date   Initial Outlay
  -----------------------------------------------------------------------------------
  BUY    1    PUT    $200.00   $0.50      BUY_TO_OPEN     2026-08-24   $50.66        
  SELL   1    PUT    $310.00   $5.52      SELL_TO_OPEN    2026-08-24   -$551.32      
  SELL   1    PUT    $200.00   $0.45      SELL_TO_CLOSE   2026-08-31   -$44.34       
  BUY    1    PUT    $310.00   $2.95      BUY_TO_CLOSE    2026-08-31   $295.66       
-----------------------------------------------------------------
```

### 5. Display Expiration Payoff Matrix Table
```bash
python main.py payoff --name "QQQ 2026-08-31 Strategy" --points 15
```

---

## Run Unit Tests
```bash
PYTHONPATH=. pytest tests/
```

---

## Documentation Links & Project Structure
- [DATABASE_README.md](DATABASE_README.md) - SQLite database schema, unique index `idx_legs_tx_hash`, tables (`chains`, `legs`), and ER diagram.
- [main.py](main.py) - Main CLI entry point.
- [options_chain/](options_chain/) - Core package containing data models, calculation engine, storage, activity parser, and CLI logic.
  - [activity_parser.py](options_chain/activity_parser.py) - Same-day grouping, active position interaction, Fidelity CSV parsing, and SHA-256 deduplication.
  - [models.py](options_chain/models.py) - Data classes (`OptionLeg`, `OptionsChain`).
  - [calculator.py](options_chain/calculator.py) - Payoff matrix, breakeven root finder, net outlay engine.
  - [storage.py](options_chain/storage.py) - SQLite database & CSV import/export.
  - [cli.py](options_chain/cli.py) - CLI command implementation.
- [tests/](tests/) - Comprehensive unit tests (`test_activity_parser.py`, `test_calculator.py`, `test_storage.py`).
- [requirements.txt](requirements.txt) - Dependencies.
