import math
from typing import List, Tuple, Dict, Any, Optional
from .models import OptionsChain, OptionType, OptionSide


class ChainCalculator:
    """Calculator engine for options chains profitability and payoff metrics."""

    @staticmethod
    def get_price_bounds(chain: OptionsChain, min_price: Optional[float] = None, max_price: Optional[float] = None) -> Tuple[float, float]:
        """Determines a reasonable price range for analyzing the options chain."""
        strikes = [leg.strike for leg in chain.legs]
        if chain.underlying_entry_price:
            strikes.append(chain.underlying_entry_price)
        if chain.underlying_current_price:
            strikes.append(chain.underlying_current_price)

        if not strikes:
            low = 0.0
            high = 100.0
        else:
            min_s = min(strikes)
            max_s = max(strikes)
            low = min_s * 0.5 if min_s > 0 else 0.0
            high = max_s * 1.5 if max_s > 0 else 100.0

        if min_price is not None:
            low = min_price
        if max_price is not None:
            high = max_price

        return max(0.0, low), max(low + 1.0, high)

    @classmethod
    def find_critical_points(cls, chain: OptionsChain, min_price: float, max_price: float) -> List[float]:
        """Collects all strike prices and boundary points where payoff slope can change."""
        points = {0.0, min_price, max_price}
        for leg in chain.legs:
            if min_price <= leg.strike <= max_price:
                points.add(leg.strike)
            if leg.strike * 0.8 >= min_price:
                points.add(leg.strike * 0.8)
            if leg.strike * 1.2 <= max_price:
                points.add(leg.strike * 1.2)

        if chain.underlying_entry_price and min_price <= chain.underlying_entry_price <= max_price:
            points.add(chain.underlying_entry_price)

        return sorted(list(points))

    @classmethod
    def find_breakeven_points(cls, chain: OptionsChain, min_price: Optional[float] = None, max_price: Optional[float] = None) -> List[float]:
        """
        Calculates exact breakeven underlying stock prices at expiration where Total PnL == 0.
        Uses exact linear root finding across piecewise linear segments between option strikes.
        """
        low, high = cls.get_price_bounds(chain, min_price, max_price)
        strikes = sorted(list({leg.strike for leg in chain.legs if leg.strike > 0}))
        
        # Segment boundaries include 0, all strikes, low, high, and points far enough right
        boundary_points = sorted(list(set([0.0, low, high] + strikes + [high * 2.0])))
        
        breakevens = []
        for i in range(len(boundary_points) - 1):
            p1 = boundary_points[i]
            p2 = boundary_points[i + 1]
            if p1 == p2:
                continue

            y1 = chain.payoff_at_expiration(p1)
            y2 = chain.payoff_at_expiration(p2)

            # Check if exact zero at p1
            if abs(y1) < 1e-6:
                if not breakevens or abs(breakevens[-1] - p1) > 1e-4:
                    breakevens.append(round(p1, 4))

            # Check for zero crossing in interval (p1, p2)
            if (y1 < 0 and y2 > 0) or (y1 > 0 and y2 < 0):
                # Linear interpolation: y = y1 + (x - p1) * (y2 - y1) / (p2 - p1) = 0
                root = p1 - y1 * (p2 - p1) / (y2 - y1)
                if not breakevens or abs(breakevens[-1] - root) > 1e-4:
                    breakevens.append(round(root, 4))

        # Also test p2 for the last segment
        last_p = boundary_points[-1]
        last_y = chain.payoff_at_expiration(last_p)
        if abs(last_y) < 1e-6:
            if not breakevens or abs(breakevens[-1] - last_p) > 1e-4:
                breakevens.append(round(last_p, 4))

        return breakevens

    @classmethod
    def calculate_max_profit_and_loss(cls, chain: OptionsChain, min_price: Optional[float] = None, max_price: Optional[float] = None) -> Tuple[Optional[float], Optional[float], bool, bool]:
        """
        Calculates maximum potential profit and loss at expiration.
        Returns: (max_profit, max_loss, is_profit_unbounded, is_loss_unbounded)
        """
        low, high = cls.get_price_bounds(chain, min_price, max_price)
        critical_points = cls.find_critical_points(chain, low, high)

        payoffs = [chain.payoff_at_expiration(p) for p in critical_points]
        
        # Check asymptotic slopes as underlying price S_T -> infinity
        far_p1 = max(critical_points) + 1000.0
        far_p2 = far_p1 + 1000.0
        slope_inf = (chain.payoff_at_expiration(far_p2) - chain.payoff_at_expiration(far_p1)) / 1000.0

        is_profit_unbounded = slope_inf > 1e-4
        is_loss_unbounded = slope_inf < -1e-4

        max_profit = None if is_profit_unbounded else max(payoffs)
        max_loss = None if is_loss_unbounded else min(payoffs)

        return max_profit, max_loss, is_profit_unbounded, is_loss_unbounded

    @classmethod
    def generate_payoff_matrix(cls, chain: OptionsChain, min_price: Optional[float] = None, max_price: Optional[float] = None, num_points: int = 51) -> List[Dict[str, float]]:
        """
        Generates a payoff table mapping underlying prices to total integrated chain PnL.
        """
        low, high = cls.get_price_bounds(chain, min_price, max_price)
        step = (high - low) / (num_points - 1) if num_points > 1 else 1.0

        matrix = []
        for i in range(num_points):
            price = round(low + i * step, 2)
            pnl = round(chain.payoff_at_expiration(price), 2)
            matrix.append({
                "underlying_price": price,
                "total_pnl": pnl
            })
        return matrix

    @classmethod
    def analyze_chain(cls, chain: OptionsChain) -> Dict[str, Any]:
        """Comprehensive analysis report for an options chain."""
        net_cost = chain.net_initial_cost
        mtm_pnl = chain.current_unrealized_pnl
        breakevens = cls.find_breakeven_points(chain)
        max_p, max_l, profit_unbounded, loss_unbounded = cls.calculate_max_profit_and_loss(chain)

        cost_type = "Net Debit" if net_cost > 0 else ("Net Credit" if net_cost < 0 else "Zero Cost")

        return {
            "symbol": chain.symbol,
            "name": chain.name or f"{chain.symbol} Options Chain",
            "leg_count": len(chain.legs),
            "net_initial_cost": net_cost,
            "cost_type": cost_type,
            "abs_net_cost": abs(net_cost),
            "current_unrealized_pnl": mtm_pnl,
            "breakeven_points": breakevens,
            "max_profit": "Unbounded (+∞)" if profit_unbounded else round(max_p, 2),
            "max_loss": "Unbounded (-∞)" if loss_unbounded else round(max_l, 2),
            "risk_reward_ratio": (
                "N/A" if (profit_unbounded or loss_unbounded or max_l == 0 or max_l is None or max_p is None)
                else round(abs(max_p / max_l), 2)
            )
        }
