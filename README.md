# Options Chain Controller (`chain_controller`)

`chain_controller` is a standalone Python application that calculates total cost outlays, integrated profitability, mark-to-market performance, breakeven points, maximum gain/risk, and aggregate payoff matrices across bought and sold option calls/puts for any given underlying asset symbol.

---

## Features
- **Broker Activity Report Parser**: Automatically scans and parses Fidelity/broker activity reports (`sources/Activity*.csv`) containing `SELL_TO_OPEN`, `BUY_TO_OPEN`, `BUY_TO_CLOSE`, and `SELL_TO_CLOSE` trades.
- **Active / Closed Lifecycle Tracking**: Automatically marks chains as `active` (`True`) or `closed` (`False`) based on net contract positions, tracking `opened_date` and `closed_date`.
- **Data Storage**: Uses an **SQLite database (`options_chains.db`)** by default, with **CSV import/export capabilities**. See [DATABASE_README.md](DATABASE_README.md) for full database schema details.
- **Mathematical Payoff Engine**: Piecewise linear root-finding algorithm to compute exact breakeven points at expiration.
- **CLI Suite**: Command-line interface for creating, modifying, analyzing, exporting, importing, and parsing broker transaction reports.

---

## CLI Usage (`main.py`)

### 1. Import Broker Activity Reports (`sources/Activity*.csv`)
```bash
python main.py import-sources
```
**Example Output:**
```text
Successfully imported/updated 1 options chain(s) from 'sources':
 - [CLOSED] IBM 2026-07-24 Chain (2 legs) | Opened: 2026-07-14 | Closed: 2026-07-16
```

### 2. List Active & Closed Chains
```bash
python main.py list
```
**Example Output:**
```text
=====================================================================================
  SAVED OPTIONS CHAINS (options_chains.db)
=====================================================================================
  ID    Status     Symbol   Name                         Opened       Closed       Legs
  ---------------------------------------------------------------------------------
  3     [CLOSED] IBM      IBM 2026-07-24 Chain         2026-07-14   2026-07-16   2
  2     [ACTIVE] AAPL     AAPL Spread (Imported)       -            Open         2
  1     [ACTIVE] AAPL     AAPL Bull Call Spread        -            Open         2
=====================================================================================
```

### 3. Analyze Chain Performance & Realized PnL
```bash
python main.py analyze --chain-id 3
```

**Example Output:**
```text
=================================================================
  OPTIONS CHAIN ANALYSIS: IBM 2026-07-24 Chain (IBM)
=================================================================
  Database ID      : 3
  Status           : [CLOSED]
  Opened Date      : 2026-07-14
  Closed Date      : 2026-07-16
  Total Legs       : 2
  Net Outlay       : -$198.68 (Net Credit)
  Commissions/Fees : $1.32
  Current MTM PnL  : -$1.32
-----------------------------------------------------------------
  Breakeven Points : None
  Max Profit       : $198.68
  Max Loss         : $198.68
  Risk/Reward      : 1.0
=================================================================

  POSITIONS & LEGS:
  Side   Qty  Type   Strike    Entry      Action          Trade Date   Initial Outlay
  -----------------------------------------------------------------------------------
  SELL   1    PUT    $200.00   $3.80      SELL_TO_OPEN    2026-07-14   -$379.34      
  BUY    1    PUT    $200.00   $1.80      BUY_TO_CLOSE    2026-07-16   $180.66       
-----------------------------------------------------------------
```

### 4. Create & Add Legs Manually
```bash
python main.py create --symbol AAPL --name "AAPL Bull Call Spread"
python main.py add-leg --name "AAPL Bull Call Spread" --side BUY --type CALL --strike 150 --price 5.0 --qty 1
python main.py add-leg --name "AAPL Bull Call Spread" --side SELL --type CALL --strike 160 --price 2.0 --qty 1
```

### 5. Display Expiration Payoff Matrix Table
```bash
python main.py payoff --chain-id 3 --points 15
```

---

## Run Unit Tests
```bash
PYTHONPATH=. pytest tests/
```

---

## Documentation Links & Project Structure
- [DATABASE_README.md](DATABASE_README.md) - SQLite database schema, tables (`chains`, `legs`), and ER diagram.
- [main.py](main.py) - Main CLI entry point.
- [options_chain/](options_chain/) - Core package containing data models, calculation engine, storage, activity parser, and CLI logic.
  - [activity_parser.py](options_chain/activity_parser.py) - Fidelity `Activity*.csv` parser and OCC symbol decoder.
  - [models.py](options_chain/models.py) - Data classes (`OptionLeg`, `OptionsChain`).
  - [calculator.py](options_chain/calculator.py) - Payoff matrix, breakeven root finder, net outlay engine.
  - [storage.py](options_chain/storage.py) - SQLite database & CSV import/export.
  - [cli.py](options_chain/cli.py) - CLI command implementation.
- [tests/](tests/) - Comprehensive unit tests (`test_activity_parser.py`, `test_calculator.py`, `test_storage.py`).
- [requirements.txt](requirements.txt) - Dependencies.
