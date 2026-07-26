import argparse
import sys
import os
from typing import Optional
from .models import OptionsChain, OptionLeg, OptionType, OptionSide
from .calculator import ChainCalculator
from .storage import ChainStorage


def format_currency(val: float) -> str:
    if val < 0:
        return f"-${abs(val):,.2f}"
    return f"${val:,.2f}"


def print_chain_summary(chain: OptionsChain):
    summary = ChainCalculator.analyze_chain(chain)
    print("\n" + "=" * 65)
    print(f"  OPTIONS CHAIN ANALYSIS: {summary['name']} ({summary['symbol']})")
    print("=" * 65)
    if chain.id:
        print(f"  Database ID      : {chain.id}")
    print(f"  Total Legs       : {summary['leg_count']}")
    if chain.shares > 0:
        print(f"  Stock Shares     : {chain.shares} @ {format_currency(chain.share_entry_price)}")
    print(f"  Net Outlay       : {format_currency(summary['net_initial_cost'])} ({summary['cost_type']})")
    print(f"  Current MTM PnL  : {format_currency(summary['current_unrealized_pnl'])}")
    print("-" * 65)
    print(f"  Breakeven Points : {', '.join([format_currency(b) for b in summary['breakeven_points']]) if summary['breakeven_points'] else 'None'}")
    print(f"  Max Profit       : {summary['max_profit'] if isinstance(summary['max_profit'], str) else format_currency(summary['max_profit'])}")
    print(f"  Max Loss         : {summary['max_loss'] if isinstance(summary['max_loss'], str) else format_currency(summary['max_loss'])}")
    print(f"  Risk/Reward      : {summary['risk_reward_ratio']}")
    print("=" * 65)

    if chain.legs:
        print("\n  POSITIONS & LEGS:")
        header = f"  {'Side':<6} {'Qty':<4} {'Type':<6} {'Strike':<9} {'Entry Price':<12} {'Current Price':<12} {'Initial Outlay':<14}"
        print(header)
        print("  " + "-" * (len(header) - 2))
        for leg in chain.legs:
            cur = format_currency(leg.current_price) if leg.current_price is not None else "-"
            outlay = format_currency(leg.initial_cost)
            print(f"  {leg.side.value:<6} {leg.quantity:<4} {leg.option_type.value:<6} ${leg.strike:<8.2f} {format_currency(leg.entry_price):<12} {cur:<12} {outlay:<14}")
        print("-" * 65 + "\n")


def print_payoff_table(chain: OptionsChain, points: int = 15):
    matrix = ChainCalculator.generate_payoff_matrix(chain, num_points=points)
    print("\n" + "=" * 45)
    print(f"  EXPIRATION PAYOFF MATRIX ({chain.symbol})")
    print("=" * 45)
    print(f"  {'Underlying Price':<18} | {'Total PnL':<15} | Status")
    print("  " + "-" * 41)
    for row in matrix:
        price = row['underlying_price']
        pnl = row['total_pnl']
        status = "PROFIT" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN")
        pnl_str = format_currency(pnl)
        print(f"  ${price:<17.2f} | {pnl_str:<15} | {status}")
    print("=" * 45 + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="options_chain_controller",
        description="Options Chain Controller: Integrated profitability & payoff calculator for multi-leg options strategies."
    )
    parser.add_argument("--db", default="options_chains.db", help="Path to SQLite database file")

    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Command: list
    subparsers.add_parser("list", help="List all saved options chains in database")

    # Command: create
    create_p = subparsers.add_parser("create", help="Create a new options chain")
    create_p.add_argument("--symbol", required=True, help="Underlying ticker symbol (e.g. AAPL)")
    create_p.add_argument("--name", help="Custom name for strategy (e.g. 'AAPL Bull Spread')")
    create_p.add_argument("--shares", type=int, default=0, help="Number of underlying stock shares held")
    create_p.add_argument("--share-price", type=float, default=0.0, help="Stock entry price")

    # Command: add-leg
    leg_p = subparsers.add_parser("add-leg", help="Add an option leg to a chain")
    leg_p.add_argument("--chain-id", type=int, help="ID of saved chain")
    leg_p.add_argument("--name", help="Name of saved chain")
    leg_p.add_argument("--side", choices=["BUY", "SELL"], required=True, help="BUY (Long) or SELL (Short)")
    leg_p.add_argument("--type", choices=["CALL", "PUT"], required=True, help="CALL or PUT")
    leg_p.add_argument("--strike", type=float, required=True, help="Option strike price")
    leg_p.add_argument("--price", type=float, required=True, help="Option entry price (premium)")
    leg_p.add_argument("--qty", type=int, default=1, help="Quantity of contracts")
    leg_p.add_argument("--current-price", type=float, help="Current option market price")
    leg_p.add_argument("--exp", help="Expiration date (YYYY-MM-DD)")

    # Command: analyze
    analyze_p = subparsers.add_parser("analyze", help="Analyze integrated profitability of a chain")
    analyze_p.add_argument("--chain-id", type=int, help="ID of saved chain")
    analyze_p.add_argument("--name", help="Name of saved chain")

    # Command: payoff
    payoff_p = subparsers.add_parser("payoff", help="Show payoff matrix table across price points")
    payoff_p.add_argument("--chain-id", type=int, help="ID of saved chain")
    payoff_p.add_argument("--name", help="Name of saved chain")
    payoff_p.add_argument("--points", type=int, default=15, help="Number of price evaluation points")

    # Command: export
    export_p = subparsers.add_parser("export", help="Export chain to CSV file")
    export_p.add_argument("--chain-id", type=int, help="ID of saved chain")
    export_p.add_argument("--name", help="Name of saved chain")
    export_p.add_argument("--out", required=True, help="Output CSV file path")

    # Command: import
    import_p = subparsers.add_parser("import", help="Import chain from CSV file into database")
    import_p.add_argument("--file", required=True, help="Path to CSV file")
    import_p.add_argument("--symbol", help="Override symbol")
    import_p.add_argument("--name", help="Override chain name")

    # Command: delete
    del_p = subparsers.add_parser("delete", help="Delete a chain from database")
    del_p.add_argument("--chain-id", type=int, required=True, help="ID of saved chain to delete")

    return parser


def main_cli(args=None):
    parser = build_parser()
    parsed = parser.parse_args(args)

    if not parsed.command:
        parser.print_help()
        return

    storage = ChainStorage(db_path=parsed.db)

    if parsed.command == "list":
        chains = storage.list_chains()
        if not chains:
            print("No saved options chains found in database.")
            return
        print("\n" + "=" * 65)
        print(f"  SAVED OPTIONS CHAINS ({parsed.db})")
        print("=" * 65)
        print(f"  {'ID':<5} {'Symbol':<8} {'Name':<30} {'Legs':<6} {'Created At'}")
        print("  " + "-" * 61)
        for c in chains:
            print(f"  {c['id']:<5} {c['symbol']:<8} {(c['name'] or ''):<30} {c['leg_count']:<6} {c['created_at']}")
        print("=" * 65 + "\n")

    elif parsed.command == "create":
        chain = OptionsChain(
            symbol=parsed.symbol.upper(),
            name=parsed.name or f"{parsed.symbol.upper()} Chain",
            shares=parsed.shares,
            share_entry_price=parsed.share_price
        )
        chain_id = storage.save_chain(chain)
        print(f"Created new options chain '{chain.name}' (ID: {chain_id}) for symbol {chain.symbol}.")

    elif parsed.command in ("add-leg", "analyze", "payoff", "export"):
        chain = None
        if hasattr(parsed, 'chain_id') and parsed.chain_id is not None:
            chain = storage.get_chain(parsed.chain_id)
        elif hasattr(parsed, 'name') and parsed.name:
            chain = storage.get_chain_by_name(parsed.name)

        if not chain:
            print("Error: Specify a valid --chain-id or --name of an existing chain.")
            sys.exit(1)

        if parsed.command == "add-leg":
            leg = OptionLeg(
                strike=parsed.strike,
                option_type=OptionType(parsed.type),
                side=OptionSide(parsed.side),
                quantity=parsed.qty,
                entry_price=parsed.price,
                current_price=parsed.current_price if parsed.current_price is not None else parsed.price,
                expiration_date=parsed.exp
            )
            chain.add_leg(leg)
            storage.save_chain(chain)
            print(f"Added {leg.side.value} {leg.quantity} {leg.option_type.value} ${leg.strike} @ ${leg.entry_price} to '{chain.name}' (ID: {chain.id}).")
            print_chain_summary(chain)

        elif parsed.command == "analyze":
            print_chain_summary(chain)

        elif parsed.command == "payoff":
            print_payoff_table(chain, points=parsed.points)

        elif parsed.command == "export":
            storage.export_to_csv(chain, parsed.out)
            print(f"Exported options chain '{chain.name}' to '{parsed.out}'.")

    elif parsed.command == "import":
        chain = storage.import_from_csv(parsed.file, symbol=parsed.symbol, name=parsed.name)
        chain_id = storage.save_chain(chain)
        print(f"Imported options chain '{chain.name}' (ID: {chain_id}) from '{parsed.file}'.")
        print_chain_summary(chain)

    elif parsed.command == "delete":
        success = storage.delete_chain(parsed.chain_id)
        if success:
            print(f"Successfully deleted options chain ID {parsed.chain_id}.")
        else:
            print(f"Chain ID {parsed.chain_id} not found.")


if __name__ == "__main__":
    main_cli()
