import os
import argparse
from typing import List, Dict, Any, Optional
from flask import Flask, render_template, request, jsonify, redirect, url_for

from options_chain.storage import ChainStorage
from options_chain.models import OptionsChain, OptionLeg
from options_chain.calculator import ChainCalculator
from options_chain.activity_parser import ActivityParser


def create_app(db_path: str = "options_chains.db", sources_dir: str = "sources") -> Flask:
    app = Flask(__name__, template_folder="templates")
    storage = ChainStorage(db_path=db_path)

    def format_leg(leg: OptionLeg) -> Dict[str, Any]:
        return {
            "id": leg.id,
            "action": leg.action,
            "side": leg.side.value if leg.side else "",
            "type": leg.option_type.value if leg.option_type else "",
            "strike": leg.strike,
            "quantity": leg.quantity,
            "entry_price": leg.entry_price,
            "trade_date": leg.trade_date,
            "expiration_date": leg.expiration_date,
            "occ_symbol": leg.occ_symbol,
            "outlay": leg.initial_cost
        }

    def format_chain(chain: OptionsChain) -> Dict[str, Any]:
        summary = ChainCalculator.analyze_chain(chain)
        formatted_legs = [format_leg(l) for l in chain.legs]
        return {
            "id": chain.id,
            "symbol": chain.symbol,
            "name": chain.name,
            "active": bool(chain.active),
            "opened_date": chain.opened_date,
            "closed_date": chain.closed_date,
            "deleted": bool(getattr(chain, "deleted", False)),
            "legs": formatted_legs,
            "net_outlay": summary["net_initial_cost"],
            "cost_type": summary["cost_type"],
            "commissions_and_fees": chain.total_commissions_and_fees,
            "breakeven_points": summary["breakeven_points"],
            "max_profit": summary["max_profit"] if isinstance(summary["max_profit"], str) else f"${summary['max_profit']:,.2f}",
            "max_loss": summary["max_loss"] if isinstance(summary["max_loss"], str) else f"${summary['max_loss']:,.2f}",
            "risk_reward": summary["risk_reward_ratio"]
        }

    @app.route("/")
    def index():
        return redirect(url_for("import_page"))

    @app.route("/import")
    def import_page():
        # Read latest chains from sources directory
        res = ActivityParser.import_sources_folder(sources_dir=sources_dir, storage=None)
        raw_chains = res.get("chains", [])

        # Collect all unique trade dates across all legs
        all_dates = set()
        for c in raw_chains:
            for l in c.legs:
                if l.trade_date:
                    all_dates.add(l.trade_date)

        sorted_dates = sorted(all_dates, reverse=True)
        selected_date = request.args.get("date")
        if not selected_date or selected_date not in all_dates:
            selected_date = sorted_dates[0] if sorted_dates else "N/A"

        # Filter chains that have transactions on selected_date
        filtered_chains = []
        for c in raw_chains:
            legs_on_date = [l for l in c.legs if l.trade_date == selected_date]
            if legs_on_date:
                filtered_chains.append(c)

        formatted_chains = [format_chain(c) for c in filtered_chains]

        # Calculate summary totals for selected date
        total_legs_count = sum(len(c["legs"]) for c in formatted_chains)
        total_net_outlay = sum(c["net_outlay"] for c in formatted_chains)

        return render_template(
            "import_report.html",
            active_page="import",
            selected_date=selected_date,
            available_dates=sorted_dates,
            chains=formatted_chains,
            total_legs_count=total_legs_count,
            total_net_outlay=total_net_outlay
        )

    @app.route("/api/run-import", methods=["POST"])
    def api_run_import():
        try:
            res = ActivityParser.import_sources_folder(sources_dir=sources_dir, storage=storage)
            return jsonify({
                "success": True,
                "processed_files": res["processed_files"],
                "new_legs": res["new_legs"],
                "skipped_duplicates": res["skipped_duplicates"],
                "chains_updated": len(res["chains"])
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/chains/<int:chain_id>/delete", methods=["POST"])
    def api_delete_chain(chain_id: int):
        try:
            success = storage.soft_delete_chain(chain_id)
            if success:
                return jsonify({"success": True, "message": f"Position #{chain_id} moved to Deleted."})
            return jsonify({"success": False, "error": "Chain not found"}), 404
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/chains/<int:chain_id>/revert", methods=["POST"])
    def api_revert_chain(chain_id: int):
        try:
            success = storage.revert_chain(chain_id)
            if success:
                chain = storage.get_chain(chain_id)
                status_label = "Active" if chain and chain.active else "Closed"
                return jsonify({
                    "success": True, 
                    "message": f"Position #{chain_id} reverted successfully to {status_label}."
                })
            return jsonify({"success": False, "error": "Chain not found"}), 404
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/chains")
    def chains_page():
        status_filter = request.args.get("status", "all").lower()
        search_query = request.args.get("q", "").strip().upper()

        # Compute counts for all selector tabs
        count_all = len(storage.list_chains(status="all"))
        count_active = len(storage.list_chains(status="active"))
        count_closed = len(storage.list_chains(status="closed"))
        count_deleted = len(storage.list_chains(status="deleted"))

        # Fetch chains based on selected status filter
        matching_meta = storage.list_chains(status=status_filter if status_filter in ("active", "closed", "deleted") else "all")
        all_chains: List[OptionsChain] = []
        for meta in matching_meta:
            loaded = storage.get_chain(meta["id"])
            if loaded:
                all_chains.append(loaded)

        # Apply search query filter if provided
        filtered = all_chains
        if search_query:
            filtered = [
                c for c in filtered 
                if search_query in c.symbol.upper() or (c.name and search_query in c.name.upper())
            ]

        formatted_chains = [format_chain(c) for c in filtered]

        return render_template(
            "chains_list.html",
            active_page="chains",
            current_status=status_filter,
            search=search_query,
            counts={
                "all": count_all, 
                "active": count_active, 
                "closed": count_closed,
                "deleted": count_deleted
            },
            chains=formatted_chains
        )

    return app


def main():
    parser = argparse.ArgumentParser(description="Options Chain Controller Web Application")
    parser.add_argument("--host", default="127.0.0.1", help="Host address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5001, help="Port number (default: 5001)")
    parser.add_argument("--db", default="options_chains.db", help="Path to SQLite database")
    parser.add_argument("--dir", default="sources", help="Path to sources directory")
    args = parser.parse_args()

    app = create_app(db_path=args.db, sources_dir=args.dir)
    print(f"\n=======================================================")
    print(f"  Options Chain Controller Web Dashboard Running")
    print(f"  URL: http://{args.host}:{args.port}")
    print(f"=======================================================\n")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()
