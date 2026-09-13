from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional, Tuple
from .models import OptionType, OptionSide, OptionsChain
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
        If initial_date is specified, only returns positions with opened_date >= initial_date.
        """
        meta_list = storage.list_chains(include_deleted=False)
        qualifying: List[Dict[str, Any]] = []

        for meta in meta_list:
            chain = storage.get_chain(meta["id"])
            if not chain or not chain.opened_date:
                continue

            if initial_date and chain.opened_date < initial_date:
                continue

            # Check put legs
            put_legs = [l for l in chain.legs if l.option_type == OptionType.PUT]
            if not put_legs:
                continue

            open_puts = [l for l in put_legs if "OPEN" in (l.action or "")]
            short_opens = [l for l in open_puts if l.action == "SELL_TO_OPEN" or (not l.action and l.side == OptionSide.SELL)]
            long_opens = [l for l in open_puts if l.action == "BUY_TO_OPEN" or (not l.action and l.side == OptionSide.BUY)]

            strat_type = None
            strat_name = None
            risk = 0.0
            net_credit = 0.0
            strikes_label = ""
            exp_date = ""
            total_contracts = 0

            # 1. Short Put: short puts with no long opening put legs
            if short_opens and not long_opens:
                strat_type = "Short Put"
                strat_name = f"{chain.symbol} {chain.opened_date} Short Put"
                total_contracts = sum(s.quantity for s in short_opens)
                exp_date = short_opens[0].expiration_date or ""
                strikes_label = ", ".join(f"${s.strike:,.2f}" for s in short_opens)
                for s in short_opens:
                    mult = s.multiplier or 100.0
                    risk += s.strike * s.quantity * mult
                    net_credit += s.entry_price * s.quantity * mult

            # 2. Credit Short Puts Spread: short put and long put with same expiration date and short strike > long strike
            elif short_opens and long_opens:
                for s in short_opens:
                    for lo in long_opens:
                        if s.expiration_date == lo.expiration_date and s.strike > lo.strike:
                            strat_type = "Credit Short Puts Spread"
                            strat_name = f"{chain.symbol} {chain.opened_date} Credit Short Puts Spread"
                            qty = min(s.quantity, lo.quantity)
                            total_contracts = qty
                            mult = s.multiplier or 100.0
                            risk += (s.strike - lo.strike) * qty * mult
                            net_credit += (s.entry_price - lo.entry_price) * qty * mult
                            strikes_label = f"${s.strike:,.2f} / ${lo.strike:,.2f}"
                            exp_date = s.expiration_date or ""
                            break
                    if strat_type:
                        break

            if strat_type:
                # Realized profit for closed strategies
                realized_profit = 0.0
                if not chain.active and chain.closed_date:
                    # Initial cost is negative for credit received, positive for debit paid
                    realized_profit = -chain.net_initial_cost - chain.total_commissions_and_fees

                qualifying.append({
                    "id": chain.id,
                    "symbol": chain.symbol,
                    "name": strat_name,
                    "original_chain_name": chain.name,
                    "strategy_type": strat_type,
                    "active": chain.active,
                    "opened_date": chain.opened_date,
                    "closed_date": chain.closed_date,
                    "risk": risk,
                    "net_credit": net_credit,
                    "realized_profit": realized_profit,
                    "strikes": strikes_label,
                    "expiration_date": exp_date,
                    "contracts": total_contracts,
                    "legs_count": len(chain.legs),
                    "commissions_and_fees": chain.total_commissions_and_fees
                })

        # Sort by opened_date ascending
        qualifying.sort(key=lambda p: (p["opened_date"] or "", p["id"]))
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

            # Positions closed on or before curr_str
            closed_pos = [
                p for p in positions
                if not p["active"] and p["closed_date"] and p["closed_date"] <= curr_str
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

            curr += timedelta(days=1)

        # Summary KPIs
        active_count = sum(1 for p in positions if p["active"])
        closed_count = sum(1 for p in positions if not p["active"])
        current_risk = risk_series[-1] if risk_series else 0.0
        total_profit = profit_series[-1] if profit_series else 0.0
        current_roi = annualized_roi_series[-1] if annualized_roi_series else 0.0
        avg_risk_overall = (accum_daily_risk / len(dates_series)) if dates_series else 0.0

        return {
            "initial_date": initial_date,
            "end_date": end_dt.strftime("%Y-%m-%d"),
            "total_days": len(dates_series),
            "kpis": {
                "total_positions": len(positions),
                "active_positions": active_count,
                "closed_positions": closed_count,
                "current_risk": current_risk,
                "total_realized_profit": total_profit,
                "current_annualized_roi": current_roi,
                "average_risk": round(avg_risk_overall, 2)
            },
            "charts": {
                "labels": dates_series,
                "risk_series": risk_series,
                "profit_series": profit_series,
                "annualized_roi_series": annualized_roi_series
            }
        }
