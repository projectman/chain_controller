import os
import argparse
from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional
from flask import Flask, render_template, request, jsonify, redirect, url_for, Response

from options_chain.storage import ChainStorage
from options_chain.models import OptionsChain, OptionLeg
from options_chain.calculator import ChainCalculator
from options_chain.activity_parser import ActivityParser
from options_chain.short_puts_analyzer import ShortPutsAnalyzer
from options_chain.margin_calculator import MarginCalculator, compute_totals


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
            "outlay": leg.initial_cost,
            "source_chain_id": getattr(leg, "source_chain_id", None),
            "is_child": getattr(leg, "is_child", False)
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
            "parent_chain_id": getattr(chain, "parent_chain_id", None),
            "child_chain_ids": getattr(chain, "child_chain_ids", []),
            "legs": formatted_legs,
            "net_outlay": summary["net_initial_cost"],
            "cost_type": summary["cost_type"],
            "commissions_and_fees": chain.total_commissions_and_fees,
            "breakeven_points": summary["breakeven_points"],
            "max_profit": summary["max_profit"] if isinstance(summary["max_profit"], str) else f"${summary['max_profit']:,.2f}",
            "raw_max_profit": summary["max_profit"],
            "realized_profit": -summary["net_initial_cost"] if not chain.active else 0.0,
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

        import_logs = storage.list_import_logs(limit=20)

        return render_template(
            "import_report.html",
            active_page="import",
            selected_date=selected_date,
            available_dates=sorted_dates,
            chains=formatted_chains,
            total_legs_count=total_legs_count,
            total_net_outlay=total_net_outlay,
            import_logs=import_logs
        )

    @app.route("/api/run-import", methods=["POST"])
    def api_run_import():
        try:
            res = ActivityParser.import_sources_folder(sources_dir=sources_dir, storage=storage)
            margin = {"new_positions": 0, "skipped_duplicates": 0, "warnings": []}
            try:
                margin = MarginCalculator.import_sources_folder(sources_dir=sources_dir, storage=storage)
            except Exception as margin_err:
                margin["warnings"].append(f"Margin import failed: {margin_err}")
            relink_data = res.get("relink") or {}
            return jsonify({
                "success": True,
                "processed_files": res["processed_files"],
                "new_legs": res["new_legs"],
                "skipped_duplicates": res["skipped_duplicates"],
                "chains_updated": len(res["chains"]),
                "relink_stitched_legs": relink_data.get("stitched_legs", 0),
                "relink_merged_chains": relink_data.get("merged_chains", 0),
                "margin_new": margin["new_positions"],
                "margin_skipped": margin["skipped_duplicates"],
                "margin_warnings": margin["warnings"]
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/import-logs/<int:log_id>", methods=["GET"])
    def api_get_import_log(log_id: int):
        log = storage.get_import_log(log_id)
        if not log:
            return jsonify({"success": False, "error": "Import log not found"}), 404
        return jsonify({"success": True, "log": log})

    @app.route("/api/import-logs/<int:log_id>/download", methods=["GET"])
    def api_download_import_log(log_id: int):
        log = storage.get_import_log(log_id)
        if not log or not log.get("raw_content"):
            return jsonify({"success": False, "error": "Import log or file content not found"}), 404
        fname = log.get("filename", f"import_{log_id}.csv")
        return Response(
            log["raw_content"],
            mimetype="text/csv",
            headers={"Content-Disposition": f"attachment; filename={fname}"}
        )


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

    @app.route("/api/legs/<int:leg_id>/delete", methods=["POST"])
    def api_delete_leg(leg_id: int):
        try:
            success = storage.soft_delete_leg(leg_id)
            if success:
                return jsonify({"success": True, "message": f"Leg #{leg_id} moved to Deleted."})
            return jsonify({"success": False, "error": "Leg not found"}), 404
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/legs/<int:leg_id>/revert", methods=["POST"])
    def api_revert_leg(leg_id: int):
        try:
            success = storage.revert_leg(leg_id)
            if success:
                return jsonify({"success": True, "message": f"Leg #{leg_id} reverted successfully."})
            return jsonify({"success": False, "error": "Leg not found"}), 404
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/chains/<int:chain_id>/join", methods=["POST"])
    def api_join_chain(chain_id: int):
        try:
            data = request.get_json(silent=True) or request.form
            parent_id_raw = data.get("parent_id")
            if parent_id_raw is None:
                return jsonify({"success": False, "error": "Parent chain ID is required."}), 400
            try:
                parent_id = int(parent_id_raw)
            except ValueError:
                return jsonify({"success": False, "error": "Invalid parent chain ID."}), 400

            success, msg = storage.join_chain(child_id=chain_id, parent_id=parent_id)
            if success:
                return jsonify({"success": True, "message": msg})
            return jsonify({"success": False, "error": msg}), 400
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/chains/<int:chain_id>/unjoin", methods=["POST"])
    def api_unjoin_chain(chain_id: int):
        try:
            success, msg = storage.unjoin_chain(child_id=chain_id)
            if success:
                return jsonify({"success": True, "message": msg})
            return jsonify({"success": False, "error": msg}), 400
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/api/chains/rebuild", methods=["POST"])
    def api_rebuild_chains():
        try:
            result = storage.relink_chains()
            stitched = result.get("stitched_legs", 0)
            merged = result.get("merged_chains", 0)
            protected = result.get("protected_chains_count", 0)
            if stitched > 0 or merged > 0:
                msg = f"Auto-heal complete: stitched {stitched} legs across {merged} chains ({protected} manually linked positions preserved)."
            else:
                msg = f"Chains are already fully consolidated ({protected} manually linked positions preserved)."
            return jsonify({
                "success": True,
                "message": msg,
                "stitched_legs": stitched,
                "merged_chains": merged,
                "protected_chains": protected,
                "details": result.get("details", [])
            })
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

    @app.route("/chains")
    def chains_page():
        view_mode = request.args.get("view", "chains").lower()
        if view_mode not in ("chains", "simple"):
            view_mode = "chains"

        status_filter = request.args.get("status", "all").lower()
        search_query = request.args.get("q", "").strip().upper()
        date_preset = request.args.get("date_preset", "all").lower().strip()
        if date_preset in ("1", "1m", "1-month"):
            date_preset = "1m"
        elif date_preset in ("3", "3m", "3-months"):
            date_preset = "3m"
        elif date_preset in ("6", "6m", "6-months"):
            date_preset = "6m"
        elif date_preset in ("12", "12m", "12-months"):
            date_preset = "12m"
        elif date_preset == "ytd":
            date_preset = "ytd"
        else:
            date_preset = "all"

        today = date.today()
        start_date: Optional[str] = None
        if date_preset == "1m":
            start_date = (today - timedelta(days=30)).strftime("%Y-%m-%d")
        elif date_preset == "3m":
            start_date = (today - timedelta(days=90)).strftime("%Y-%m-%d")
        elif date_preset == "6m":
            start_date = (today - timedelta(days=180)).strftime("%Y-%m-%d")
        elif date_preset == "12m":
            start_date = (today - timedelta(days=365)).strftime("%Y-%m-%d")
        elif date_preset == "ytd":
            start_date = date(today.year, 1, 1).strftime("%Y-%m-%d")

        custom_start = request.args.get("initial_date") or request.args.get("start_date")
        if custom_start:
            start_date = custom_start

        if view_mode == "simple":
            positions, counts = storage.list_simple_positions(status=status_filter, search=search_query)
            if start_date:
                positions = [p for p in positions if p.get("trade_date") and p.get("trade_date") >= start_date]
            simple_total_outlay = sum(p["outlay"] for p in positions)
            return render_template(
                "chains_list.html",
                active_page="chains",
                view_mode="simple",
                current_status=status_filter,
                search=search_query,
                date_preset=date_preset,
                counts=counts,
                positions=positions,
                total_outlay=simple_total_outlay,
                total_outlay_formatted=f"-${abs(simple_total_outlay):,.2f}" if simple_total_outlay < 0 else f"${simple_total_outlay:,.2f}"
            )

        # Chains view mode:
        def filter_meta_by_date(meta_list):
            if not start_date:
                return meta_list
            return [m for m in meta_list if m.get("opened_date") and m.get("opened_date") >= start_date]

        count_all = len(filter_meta_by_date(storage.list_chains(status="all")))
        count_active = len(filter_meta_by_date(storage.list_chains(status="active")))
        count_closed = len(filter_meta_by_date(storage.list_chains(status="closed")))
        count_deleted = len(filter_meta_by_date(storage.list_chains(status="deleted")))

        matching_meta = storage.list_chains(status=status_filter if status_filter in ("active", "closed", "deleted") else "all")
        if start_date:
            matching_meta = [m for m in matching_meta if m.get("opened_date") and m.get("opened_date") >= start_date]

        all_chains: List[OptionsChain] = []
        for meta in matching_meta:
            loaded = storage.get_chain(meta["id"])
            if loaded:
                all_chains.append(loaded)

        filtered = all_chains
        if search_query:
            filtered = [
                c for c in filtered 
                if search_query in c.symbol.upper() or (c.name and search_query in c.name.upper())
            ]

        formatted_chains = [format_chain(c) for c in filtered]

        # Calculate summary totals for selected chains (exclude child chains to prevent double-counting)
        root_chains = [c for c in formatted_chains if not c.get("parent_chain_id")]
        total_net_outlay = sum(c["net_outlay"] for c in root_chains)
        total_realized_profit = sum(c["realized_profit"] for c in root_chains if not c["active"])
        total_active_max_profit = sum(
            c["raw_max_profit"] for c in root_chains 
            if c["active"] and isinstance(c["raw_max_profit"], (int, float))
        )
        unbounded_active_count = sum(
            1 for c in root_chains 
            if c["active"] and not isinstance(c["raw_max_profit"], (int, float))
        )
        active_count = sum(1 for c in root_chains if c["active"])
        closed_count = sum(1 for c in root_chains if not c["active"])

        totals = {
            "net_outlay": total_net_outlay,
            "net_outlay_formatted": f"-${abs(total_net_outlay):,.2f}" if total_net_outlay < 0 else f"${total_net_outlay:,.2f}",
            "realized_profit": total_realized_profit,
            "realized_profit_formatted": f"+${total_realized_profit:,.2f}" if total_realized_profit >= 0 else f"-${abs(total_realized_profit):,.2f}",
            "active_max_profit": total_active_max_profit,
            "active_max_profit_formatted": f"${total_active_max_profit:,.2f}",
            "unbounded_count": unbounded_active_count,
            "active_count": active_count,
            "closed_count": closed_count,
        }

        return render_template(
            "chains_list.html",
            active_page="chains",
            view_mode="chains",
            current_status=status_filter,
            search=search_query,
            date_preset=date_preset,
            counts={
                "all": count_all, 
                "active": count_active, 
                "closed": count_closed,
                "deleted": count_deleted
            },
            chains=formatted_chains,
            totals=totals
        )

    @app.route("/margin-calculator")
    def margin_calculator_page():
        dates = storage.list_margin_dates()
        selected_date = request.args.get("date")
        if selected_date not in dates:
            selected_date = dates[0] if dates else None
        search = request.args.get("q", "").strip().upper()
        positions = storage.list_margin_positions(selected_date) if selected_date else []
        if search:
            positions = [p for p in positions if search in p["symbol"]]
        totals = compute_totals(positions)
        return render_template(
            "margin_calculator.html",
            active_page="margin_calculator",
            positions=positions,
            dates=dates,
            selected_date=selected_date,
            search=search,
            **totals
        )

    @app.route("/short-puts")
    def short_puts_page():
        earliest_date = ShortPutsAnalyzer.get_earliest_qualifying_date(storage)
        initial_date = request.args.get("initial_date", earliest_date)
        if not initial_date:
            initial_date = earliest_date

        status_filter = request.args.get("status", "all").lower()
        symbol_filter = request.args.get("symbol", "").upper().strip()
        if symbol_filter == "ALL":
            symbol_filter = ""

        all_positions = ShortPutsAnalyzer.find_qualifying_positions(storage, initial_date=initial_date)
        metrics = ShortPutsAnalyzer.compute_daily_metrics(all_positions, initial_date=initial_date)

        # Count occurrences per symbol across all qualifying positions
        symbol_counts: Dict[str, int] = {}
        for p in all_positions:
            sym = p["symbol"]
            symbol_counts[sym] = symbol_counts.get(sym, 0) + 1
        available_symbols = sorted(symbol_counts.keys())

        # Positions scoped to selected symbol (if any) for status counts and display
        symbol_scoped_positions = [p for p in all_positions if p["symbol"] == symbol_filter] if symbol_filter else all_positions

        count_all = len(symbol_scoped_positions)
        count_active = sum(1 for p in symbol_scoped_positions if p["active"])
        count_closed = sum(1 for p in symbol_scoped_positions if not p["active"])

        if status_filter == "active":
            displayed_positions = [p for p in symbol_scoped_positions if p["active"]]
        elif status_filter == "closed":
            displayed_positions = [p for p in symbol_scoped_positions if not p["active"]]
        else:
            status_filter = "all"
            displayed_positions = symbol_scoped_positions

        return render_template(
            "short_puts_analyzer.html",
            active_page="short_puts",
            initial_date=initial_date,
            earliest_date=earliest_date,
            current_status=status_filter,
            selected_symbol=symbol_filter,
            available_symbols=available_symbols,
            symbol_counts=symbol_counts,
            counts={
                "all": count_all,
                "active": count_active,
                "closed": count_closed
            },
            positions=displayed_positions,
            metrics=metrics
        )

    @app.route("/api/short-puts/data")
    def api_short_puts_data():
        earliest_date = ShortPutsAnalyzer.get_earliest_qualifying_date(storage)
        initial_date = request.args.get("initial_date", earliest_date)
        if not initial_date:
            initial_date = earliest_date

        symbol_filter = request.args.get("symbol", "").upper().strip()
        if symbol_filter == "ALL":
            symbol_filter = ""

        positions = ShortPutsAnalyzer.find_qualifying_positions(storage, initial_date=initial_date)
        if symbol_filter:
            positions = [p for p in positions if p["symbol"] == symbol_filter]

        metrics = ShortPutsAnalyzer.compute_daily_metrics(positions, initial_date=initial_date)
        return jsonify({
            "positions": positions,
            "metrics": metrics
        })

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
