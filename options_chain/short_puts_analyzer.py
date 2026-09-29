from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional, Tuple
from .models import OptionType, OptionSide, OptionsChain, OptionLeg
from .storage import ChainStorage


class ShortPutsAnalyzer:
    """
    Analyzes Short Put and Credit Short Puts Spread strategies over time,
    calculating daily risk exposure, cumulative realized profit, and annualized return on risk.
    """

    @classmethod
    def find_qualifying_positions(
        cls, 
        storage: ChainStorage, 
        initial_date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Identifies all Short Put and Credit Short Puts Spread strategies from SQLite storage.
        Decomposes chains into individual positions so that closed/rolled trades are properly
        separated from active trades, accounting for realized profits, holding periods,
        and accurate risk exposure.
        """
        meta_list = storage.list_chains(include_deleted=False)
        all_chains = [storage.get_chain(meta["id"]) for meta in meta_list]
        all_chains = [c for c in all_chains if c and c.opened_date]

        # Index active long put legs across all chains by symbol to support cross-chain pairing
        all_active_long_puts: Dict[str, List[Tuple[OptionsChain, OptionLeg]]] = {}
        for c in all_chains:
            if not c.active:
                continue
            for l in c.legs:
                if l.option_type == OptionType.PUT and (l.action == "BUY_TO_OPEN" or (not l.action and l.side == OptionSide.BUY)):
                    all_active_long_puts.setdefault(c.symbol, []).append((c, l))

        qualifying: List[Dict[str, Any]] = []

        for chain in all_chains:
            put_legs = [l for l in chain.legs if l.option_type == OptionType.PUT and not l.deleted]
            if not put_legs:
                continue

            short_opens = [l for l in put_legs if l.action == "SELL_TO_OPEN" or (not l.action and l.side == OptionSide.SELL)]
            long_opens = [l for l in put_legs if l.action == "BUY_TO_OPEN" or (not l.action and l.side == OptionSide.BUY)]
            short_closes = [l for l in put_legs if l.action == "BUY_TO_CLOSE"]
            long_closes = [l for l in put_legs if l.action == "SELL_TO_CLOSE"]

            if not short_opens:
                continue

            s_open_pool = [[l, l.quantity] for l in short_opens]
            l_open_pool = [[l, l.quantity] for l in long_opens]
            s_close_pool = [[l, l.quantity] for l in short_closes]
            l_close_pool = [[l, l.quantity] for l in long_closes]

            s_open_pool.sort(key=lambda item: (item[0].trade_date or "", item[0].strike))
            l_open_pool.sort(key=lambda item: (item[0].trade_date or "", item[0].strike))

            # 1. Vertical Credit Put Spreads (Same expiration date, short strike > long strike)
            for s_item in s_open_pool:
                s_leg, s_rem = s_item
                if s_rem <= 0:
                    continue
                for l_item in l_open_pool:
                    l_leg, l_rem = l_item
                    if l_rem <= 0:
                        continue
                    if s_leg.expiration_date == l_leg.expiration_date and s_leg.strike > l_leg.strike:
                        spread_qty = min(s_rem, l_rem)
                        s_item[1] -= spread_qty
                        l_item[1] -= spread_qty

                        mult = s_leg.multiplier or 100.0
                        risk = (s_leg.strike - l_leg.strike) * spread_qty * mult
                        net_credit = (s_leg.entry_price - l_leg.entry_price) * spread_qty * mult
                        strikes_label = f"${s_leg.strike:,.2f} / ${l_leg.strike:,.2f}"
                        opened_date = s_leg.trade_date or chain.opened_date

                        matched_s_close = next((c for c in s_close_pool if c[1] >= spread_qty and c[0].expiration_date == s_leg.expiration_date and c[0].strike == s_leg.strike), None)
                        matched_l_close = next((c for c in l_close_pool if c[1] >= spread_qty and c[0].expiration_date == s_leg.expiration_date and c[0].strike == l_leg.strike), None)

                        is_closed = False
                        closed_date = None
                        realized_profit = 0.0

                        if matched_s_close and matched_l_close:
                            is_closed = True
                            matched_s_close[1] -= spread_qty
                            matched_l_close[1] -= spread_qty
                            closed_date = max(matched_s_close[0].trade_date or "", matched_l_close[0].trade_date or "")
                            close_cost = (matched_s_close[0].entry_price - matched_l_close[0].entry_price) * spread_qty * mult
                            close_fees = (matched_s_close[0].commission + matched_s_close[0].fees) * (spread_qty / matched_s_close[0].quantity) +                                          (matched_l_close[0].commission + matched_l_close[0].fees) * (spread_qty / matched_l_close[0].quantity)
                            open_fees = (s_leg.commission + s_leg.fees) * (spread_qty / s_leg.quantity) +                                         (l_leg.commission + l_leg.fees) * (spread_qty / l_leg.quantity)
                            realized_profit = net_credit - close_cost - (open_fees + close_fees)
                        elif matched_s_close:
                            is_closed = True
                            matched_s_close[1] -= spread_qty
                            closed_date = matched_s_close[0].trade_date
                            close_cost = matched_s_close[0].entry_price * spread_qty * mult
                            close_fees = (matched_s_close[0].commission + matched_s_close[0].fees) * (spread_qty / matched_s_close[0].quantity)
                            open_fees = (s_leg.commission + s_leg.fees) * (spread_qty / s_leg.quantity) +                                         (l_leg.commission + l_leg.fees) * (spread_qty / l_leg.quantity)
                            realized_profit = net_credit - close_cost - (open_fees + close_fees)
                        elif not chain.active and chain.closed_date:
                            is_closed = True
                            closed_date = chain.closed_date
                            realized_profit = -chain.net_initial_cost - chain.total_commissions_and_fees

                        holding_days = None
                        annualized_roi = None
                        if is_closed and closed_date and opened_date:
                            try:
                                d_open = datetime.strptime(opened_date, "%Y-%m-%d").date()
                                d_close = datetime.strptime(closed_date, "%Y-%m-%d").date()
                                holding_days = max(1, (d_close - d_open).days)
                                if risk > 0:
                                    annualized_roi = round(365.0 * (realized_profit / risk) / holding_days * 100.0, 2)
                            except ValueError:
                                pass

                        qualifying.append({
                            "id": chain.id,
                            "symbol": chain.symbol,
                            "name": f"{chain.symbol} {opened_date} Credit Short Puts Spread",
                            "original_chain_name": chain.name,
                            "strategy_type": "Credit Short Puts Spread",
                            "active": not is_closed,
                            "opened_date": opened_date,
                            "closed_date": closed_date if is_closed else None,
                            "holding_days": holding_days,
                            "annualized_profit_pct": annualized_roi,
                            "risk": risk,
                            "net_credit": net_credit,
                            "realized_profit": round(realized_profit, 2) if is_closed else 0.0,
                            "strikes": strikes_label,
                            "expiration_date": s_leg.expiration_date or "",
                            "contracts": spread_qty,
                            "legs_count": len(chain.legs),
                            "commissions_and_fees": chain.total_commissions_and_fees
                        })

            # 2. Diagonal Credit Spreads (Different strikes and different expiration dates)
            for s_item in s_open_pool:
                s_leg, s_rem = s_item
                if s_rem <= 0:
                    continue
                diag_candidates = [
                    l_item for l_item in l_open_pool 
                    if l_item[1] > 0 and (l_item[0].expiration_date != s_leg.expiration_date or l_item[0].strike != s_leg.strike)
                ]
                cross_cand = None
                if not diag_candidates and chain.active and chain.symbol in all_active_long_puts:
                    cross_candidates = [
                        l for ch, l in all_active_long_puts[chain.symbol]
                        if ch.id != chain.id and l.expiration_date != s_leg.expiration_date and l.strike != s_leg.strike
                    ]
                    if cross_candidates:
                        cross_cand = cross_candidates[0]

                if diag_candidates or cross_cand:
                    l_leg = diag_candidates[0][0] if diag_candidates else cross_cand
                    qty = min(s_rem, diag_candidates[0][1]) if diag_candidates else min(s_rem, cross_cand.quantity)
                    s_item[1] -= qty
                    if diag_candidates:
                        diag_candidates[0][1] -= qty

                    mult = s_leg.multiplier or 100.0
                    risk = (s_leg.strike - l_leg.strike) * qty * mult if s_leg.strike > l_leg.strike else 0.0
                    net_credit = s_leg.entry_price * qty * mult
                    strikes_label = f"${s_leg.strike:,.2f} / ${l_leg.strike:,.2f}"
                    s_exp = s_leg.expiration_date or "-"
                    l_exp = l_leg.expiration_date or "-"
                    exp_date = f"{s_exp} / {l_exp}"
                    opened_date = s_leg.trade_date or chain.opened_date

                    matched_s_close = next((c for c in s_close_pool if c[1] >= qty and c[0].expiration_date == s_leg.expiration_date and c[0].strike == s_leg.strike), None)
                    is_closed = False
                    closed_date = None
                    realized_profit = 0.0
                    if matched_s_close:
                        is_closed = True
                        matched_s_close[1] -= qty
                        closed_date = matched_s_close[0].trade_date
                        close_cost = matched_s_close[0].entry_price * qty * mult
                        open_fees = (s_leg.commission + s_leg.fees) * (qty / s_leg.quantity)
                        close_fees = (matched_s_close[0].commission + matched_s_close[0].fees) * (qty / matched_s_close[0].quantity)
                        realized_profit = net_credit - close_cost - (open_fees + close_fees)
                    elif not chain.active and chain.closed_date:
                        is_closed = True
                        closed_date = chain.closed_date
                        realized_profit = -chain.net_initial_cost - chain.total_commissions_and_fees

                    holding_days = None
                    annualized_roi = None
                    if is_closed and closed_date and opened_date:
                        try:
                            d_open = datetime.strptime(opened_date, "%Y-%m-%d").date()
                            d_close = datetime.strptime(closed_date, "%Y-%m-%d").date()
                            holding_days = max(1, (d_close - d_open).days)
                            if risk > 0:
                                annualized_roi = round(365.0 * (realized_profit / risk) / holding_days * 100.0, 2)
                        except ValueError:
                            pass

                    qualifying.append({
                        "id": chain.id,
                        "symbol": chain.symbol,
                        "name": f"{chain.symbol} {opened_date} Diagonal Credit Spread",
                        "original_chain_name": chain.name,
                        "strategy_type": "Diagonal Credit Spread",
                        "active": not is_closed,
                        "opened_date": opened_date,
                        "closed_date": closed_date if is_closed else None,
                        "holding_days": holding_days,
                        "annualized_profit_pct": annualized_roi,
                        "risk": risk,
                        "net_credit": net_credit,
                        "realized_profit": round(realized_profit, 2) if is_closed else 0.0,
                        "strikes": strikes_label,
                        "expiration_date": exp_date,
                        "contracts": qty,
                        "legs_count": len(chain.legs),
                        "commissions_and_fees": chain.total_commissions_and_fees
                    })

            # 3. Pure Short Puts (Remaining unhedged short opens)
            for s_item in s_open_pool:
                s_leg, s_rem = s_item
                if s_rem <= 0:
                    continue

                mult = s_leg.multiplier or 100.0
                opened_date = s_leg.trade_date or chain.opened_date

                matching_closes = [
                    c for c in s_close_pool 
                    if c[1] > 0 and c[0].expiration_date == s_leg.expiration_date and c[0].strike == s_leg.strike
                ]

                for c_item in matching_closes:
                    if s_item[1] <= 0:
                        break
                    c_leg, c_rem = c_item
                    if c_rem <= 0:
                        continue
                    close_qty = min(s_item[1], c_rem)
                    s_item[1] -= close_qty
                    c_item[1] -= close_qty

                    closed_date = c_leg.trade_date or chain.closed_date
                    risk = s_leg.strike * close_qty * mult
                    net_credit = s_leg.entry_price * close_qty * mult
                    open_fees = (s_leg.commission + s_leg.fees) * (close_qty / s_leg.quantity)
                    close_fees = (c_leg.commission + c_leg.fees) * (close_qty / c_leg.quantity)
                    realized_profit = (s_leg.entry_price - c_leg.entry_price) * close_qty * mult - (open_fees + close_fees)

                    holding_days = None
                    annualized_roi = None
                    if closed_date and opened_date:
                        try:
                            d_open = datetime.strptime(opened_date, "%Y-%m-%d").date()
                            d_close = datetime.strptime(closed_date, "%Y-%m-%d").date()
                            holding_days = max(1, (d_close - d_open).days)
                            if risk > 0:
                                annualized_roi = round(365.0 * (realized_profit / risk) / holding_days * 100.0, 2)
                        except ValueError:
                            pass

                    qualifying.append({
                        "id": chain.id,
                        "symbol": chain.symbol,
                        "name": f"{chain.symbol} {opened_date} Short Put",
                        "original_chain_name": chain.name,
                        "strategy_type": "Short Put",
                        "active": False,
                        "opened_date": opened_date,
                        "closed_date": closed_date,
                        "holding_days": holding_days,
                        "annualized_profit_pct": annualized_roi,
                        "risk": risk,
                        "net_credit": net_credit,
                        "realized_profit": round(realized_profit, 2),
                        "strikes": f"${s_leg.strike:,.2f}",
                        "expiration_date": s_leg.expiration_date or "",
                        "contracts": close_qty,
                        "legs_count": len(chain.legs),
                        "commissions_and_fees": chain.total_commissions_and_fees
                    })

                rem_active_qty = s_item[1]
                if rem_active_qty > 0:
                    risk = s_leg.strike * rem_active_qty * mult
                    net_credit = s_leg.entry_price * rem_active_qty * mult

                    is_closed = not chain.active and bool(chain.closed_date)
                    closed_date = chain.closed_date if is_closed else None
                    realized_profit = 0.0
                    holding_days = None
                    annualized_roi = None

                    if is_closed and closed_date and opened_date:
                        realized_profit = net_credit - (s_leg.commission + s_leg.fees) * (rem_active_qty / s_leg.quantity)
                        try:
                            d_open = datetime.strptime(opened_date, "%Y-%m-%d").date()
                            d_close = datetime.strptime(closed_date, "%Y-%m-%d").date()
                            holding_days = max(1, (d_close - d_open).days)
                            if risk > 0:
                                annualized_roi = round(365.0 * (realized_profit / risk) / holding_days * 100.0, 2)
                        except ValueError:
                            pass

                    qualifying.append({
                        "id": chain.id,
                        "symbol": chain.symbol,
                        "name": f"{chain.symbol} {opened_date} Short Put",
                        "original_chain_name": chain.name,
                        "strategy_type": "Short Put",
                        "active": not is_closed,
                        "opened_date": opened_date,
                        "closed_date": closed_date,
                        "holding_days": holding_days,
                        "annualized_profit_pct": annualized_roi,
                        "risk": risk,
                        "net_credit": net_credit,
                        "realized_profit": round(realized_profit, 2) if is_closed else 0.0,
                        "strikes": f"${s_leg.strike:,.2f}",
                        "expiration_date": s_leg.expiration_date or "",
                        "contracts": rem_active_qty,
                        "legs_count": len(chain.legs),
                        "commissions_and_fees": chain.total_commissions_and_fees
                    })

        # Filter by initial_date at position level:
        # Include if active, or if closed on or after initial_date
        if initial_date:
            qualifying = [
                p for p in qualifying
                if p["active"] or (p["closed_date"] and p["closed_date"] >= initial_date)
            ]

        # Sort by opened_date ascending
        qualifying.sort(key=lambda p: (p["opened_date"] or "", p["id"], 0 if not p["active"] else 1))
        return qualifying

    @classmethod
    def get_earliest_qualifying_date(cls, storage: ChainStorage) -> str:
        """Finds the earliest opened_date among all qualifying short put strategies."""
        positions = cls.find_qualifying_positions(storage, initial_date=None)
        if positions:
            return positions[0]["opened_date"]
        return "2026-07-01"

    @classmethod
    def compute_daily_metrics(
        cls,
        positions: List[Dict[str, Any]],
        initial_date: str,
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Simulates daily metrics from initial_date to end_date:
        - Total risk of active positions on each date
        - Integrated (cumulative) profit of closed positions on each date
        - Relative profit by year (Annualized Return on Average Daily Risk)
        """
        try:
            start_dt = datetime.strptime(initial_date, "%Y-%m-%d").date()
        except ValueError:
            start_dt = date(2026, 7, 1)

        if end_date:
            try:
                end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
            except ValueError:
                end_dt = date.today()
        else:
            # Pick latest between today and max date in positions
            all_dates = [datetime.strptime(p["opened_date"], "%Y-%m-%d").date() for p in positions if p.get("opened_date")]
            all_dates += [datetime.strptime(p["closed_date"], "%Y-%m-%d").date() for p in positions if p.get("closed_date")]
            max_pos_dt = max(all_dates) if all_dates else date.today()
            end_dt = max(date.today(), max_pos_dt)

        if start_dt > end_dt:
            start_dt = end_dt

        dates_series: List[str] = []
        risk_series: List[float] = []
        profit_series: List[float] = []
        annualized_roi_series: List[float] = []
        avg_closed_roi_series: List[float] = []

        curr = start_dt
        accum_daily_risk = 0.0

        while curr <= end_dt:
            curr_str = curr.strftime("%Y-%m-%d")
            dates_series.append(curr_str)

            # Positions open on curr_str
            open_pos = [
                p for p in positions
                if p["opened_date"] <= curr_str and (p["active"] or (p["closed_date"] and p["closed_date"] > curr_str))
            ]
            day_risk = sum(p["risk"] for p in open_pos)
            risk_series.append(round(day_risk, 2))

            # Positions closed on or before curr_str (and closed within this evaluation window)
            closed_pos = [
                p for p in positions
                if not p["active"] and p["closed_date"] and initial_date <= p["closed_date"] <= curr_str
            ]
            integrated_profit = sum(p["realized_profit"] for p in closed_pos)
            profit_series.append(round(integrated_profit, 2))

            # Annualized Return formula
            d = (curr - start_dt).days
            accum_daily_risk += day_risk
            avg_risk = accum_daily_risk / (d + 1)

            annualized_roi = 0.0
            if d > 0 and avg_risk > 0:
                annualized_roi = (integrated_profit / avg_risk) / d * 365.0 * 100.0
            annualized_roi_series.append(round(annualized_roi, 2))

            # Running average of realized annual relative profits for closed positions
            valid_closed_rois = [p["annualized_profit_pct"] for p in closed_pos if p.get("annualized_profit_pct") is not None]
            avg_closed_roi = round(sum(valid_closed_rois) / len(valid_closed_rois), 2) if valid_closed_rois else 0.0
            avg_closed_roi_series.append(avg_closed_roi)

            curr += timedelta(days=1)

        # Summary KPIs
        active_count = sum(1 for p in positions if p["active"])
        closed_count = sum(1 for p in positions if not p["active"])
        current_risk = risk_series[-1] if risk_series else 0.0
        total_profit = profit_series[-1] if profit_series else 0.0
        current_roi = annualized_roi_series[-1] if annualized_roi_series else 0.0
        avg_risk_overall = (accum_daily_risk / len(dates_series)) if dates_series else 0.0

        total_premium_received = sum(p["net_credit"] for p in positions)
        total_premium_at_risk = sum(p["net_credit"] for p in positions if p["active"])
        total_risk_all = sum(p["risk"] for p in positions)

        current_credit_to_risk_pct = round((total_premium_at_risk / current_risk * 100.0), 2) if current_risk > 0 else 0.0
        total_credit_to_risk_pct = round((total_premium_received / total_risk_all * 100.0), 2) if total_risk_all > 0 else 0.0

        all_closed_rois = [p["annualized_profit_pct"] for p in positions if not p["active"] and p.get("annualized_profit_pct") is not None]
        overall_avg_closed_roi = round(sum(all_closed_rois) / len(all_closed_rois), 2) if all_closed_rois else 0.0

        return {
            "initial_date": initial_date,
            "end_date": end_dt.strftime("%Y-%m-%d"),
            "total_days": len(dates_series),
            "kpis": {
                "total_positions": len(positions),
                "active_positions": active_count,
                "closed_positions": closed_count,
                "current_risk": current_risk,
                "total_premium_at_risk": round(total_premium_at_risk, 2),
                "total_premium_received": round(total_premium_received, 2),
                "current_credit_to_risk_pct": current_credit_to_risk_pct,
                "total_credit_to_risk_pct": total_credit_to_risk_pct,
                "total_realized_profit": total_profit,
                "current_annualized_roi": current_roi,
                "overall_avg_closed_roi": overall_avg_closed_roi,
                "average_risk": round(avg_risk_overall, 2)
            },
            "charts": {
                "labels": dates_series,
                "risk_series": risk_series,
                "profit_series": profit_series,
                "annualized_roi_series": annualized_roi_series,
                "avg_closed_roi_series": avg_closed_roi_series
            }
        }
