# Options Chain Controller (`chain_controller`)

`chain_controller` is a standalone Python application that calculates total cost outlays, integrated profitability, mark-to-market performance, breakeven points, maximum gain/risk, and aggregate payoff matrices across bought and sold option calls/puts for any given underlying asset symbol.

---

## Features
- **Data Storage**: Uses an **SQLite database (`options_chains.db`)** by default, with **CSV import/export capabilities**.
- **Multi-Leg Support**: Supports Calls, Puts, Long (BUY), Short (SELL), and underlying stock share positions.
- **Mathematical Payoff Engine**: Piecewise linear root-finding algorithm to compute exact breakeven points at expiration.
- **CLI Suite**: Command-line interface for creating, modifying, analyzing, exporting, and importing options strategies.

---

## CLI Usage (`main.py`)

### 1. Create a New Options Chain
```bash
python main.py create --symbol AAPL --name "AAPL Bull Call Spread"
```

### 2. Add Options Legs
```bash
# Add Long Call (BUY 1 Call $150 Strike @ $5.00)
python main.py add-leg --name "AAPL Bull Call Spread" --side BUY --type CALL --strike 150 --price 5.0 --qty 1

# Add Short Call (SELL 1 Call $160 Strike @ $2.00)
python main.py add-leg --name "AAPL Bull Call Spread" --side SELL --type CALL --strike 160 --price 2.0 --qty 1
```

### 3. Analyze Integrated Profitability
```bash
python main.py analyze --name "AAPL Bull Call Spread"
```

**Example Output:**
```text
=================================================================
  OPTIONS CHAIN ANALYSIS: AAPL Bull Call Spread (AAPL)
=================================================================
  Database ID      : 1
  Total Legs       : 2
  Net Outlay       : $300.00 (Net Debit)
  Current MTM PnL  : $0.00
-----------------------------------------------------------------
  Breakeven Points : $153.00
  Max Profit       : $700.00
  Max Loss         : -$300.00
  Risk/Reward      : 2.33
=================================================================

  POSITIONS & LEGS:
  Side   Qty  Type   Strike    Entry Price  Current Price Initial Outlay
  ----------------------------------------------------------------------
  BUY    1    CALL   $150.00   $5.00        $5.00        $500.00       
  SELL   1    CALL   $160.00   $2.00        $2.00        -$200.00      
-----------------------------------------------------------------
```

### 4. Display Expiration Payoff Matrix Table
```bash
python main.py payoff --name "AAPL Bull Call Spread" --points 15
```

### 5. Export / Import CSV
```bash
# Export chain to CSV
python main.py export --name "AAPL Bull Call Spread" --out aapl_spread.csv

# Import chain from CSV
python main.py import --file aapl_spread.csv --name "Imported Spread"
```

### 6. List Saved Chains
```bash
python main.py list
```

---

## Run Unit Tests
```bash
PYTHONPATH=. pytest tests/
```

---

## Project Structure
- [main.py](file:///Users/olegbushmelev/Projects/barchart_interaction/projects/chain_controller/main.py) - Main CLI entry point.
- [options_chain/](file:///Users/olegbushmelev/Projects/barchart_interaction/projects/chain_controller/options_chain/) - Core package containing data models, calculation engine, storage, and CLI logic.
  - [models.py](file:///Users/olegbushmelev/Projects/barchart_interaction/projects/chain_controller/options_chain/models.py) - Data classes (`OptionLeg`, `OptionsChain`).
  - [calculator.py](file:///Users/olegbushmelev/Projects/barchart_interaction/projects/chain_controller/options_chain/calculator.py) - Payoff matrix, breakeven root finder, net outlay engine.
  - [storage.py](file:///Users/olegbushmelev/Projects/barchart_interaction/projects/chain_controller/options_chain/storage.py) - SQLite database & CSV import/export.
  - [cli.py](file:///Users/olegbushmelev/Projects/barchart_interaction/projects/chain_controller/options_chain/cli.py) - CLI command implementation.
- [tests/](file:///Users/olegbushmelev/Projects/barchart_interaction/projects/chain_controller/tests/) - Comprehensive unit tests (`test_calculator.py`, `test_storage.py`).
- [requirements.txt](file:///Users/olegbushmelev/Projects/barchart_interaction/projects/chain_controller/requirements.txt) - Dependencies.
