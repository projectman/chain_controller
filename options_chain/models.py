from enum import Enum
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


class OptionType(str, Enum):
    CALL = "CALL"
    PUT = "PUT"


class OptionSide(str, Enum):
    BUY = "BUY"    # Long position (Debit)
    SELL = "SELL"  # Short position (Credit)


@dataclass
class OptionLeg:
    strike: float
    option_type: OptionType
    side: OptionSide
    quantity: int
    entry_price: float
    current_price: Optional[float] = None
    expiration_date: Optional[str] = None
    multiplier: float = 100.0
    id: Optional[int] = None

    def __post_init__(self):
        if self.current_price is None:
            self.current_price = self.entry_price
        if isinstance(self.option_type, str):
            self.option_type = OptionType(self.option_type.upper())
        if isinstance(self.side, str):
            self.side = OptionSide(self.side.upper())

    @property
    def side_factor(self) -> int:
        """+1 for BUY (Long), -1 for SELL (Short)"""
        return 1 if self.side == OptionSide.BUY else -1

    @property
    def initial_cost(self) -> float:
        """
        Net initial outlay for this leg:
        - Long (BUY): Positive value representing cash paid out (Debit).
        - Short (SELL): Negative value representing cash received (Credit).
        """
        return self.side_factor * self.quantity * self.entry_price * self.multiplier

    @property
    def current_value(self) -> float:
        """
        Current liquidation value for this leg:
        - Long (BUY): Cash received if closed now (+).
        - Short (SELL): Cash required to buy back now (-).
        """
        cur = self.current_price if self.current_price is not None else self.entry_price
        return self.side_factor * self.quantity * cur * self.multiplier

    @property
    def unrealized_pnl(self) -> float:
        """
        Unrealized profit/loss:
        - Long: (Current Price - Entry Price) * Quantity * Multiplier
        - Short: (Entry Price - Current Price) * Quantity * Multiplier
        """
        cur = self.current_price if self.current_price is not None else self.entry_price
        return self.side_factor * (cur - self.entry_price) * self.quantity * self.multiplier

    def payoff_at_expiration(self, underlying_price: float) -> float:
        """
        Calculates net payoff (PnL) for this specific leg at a given underlying price at expiration.
        """
        if self.option_type == OptionType.CALL:
            intrinsic = max(underlying_price - self.strike, 0.0)
        else:  # PUT
            intrinsic = max(self.strike - underlying_price, 0.0)

        # For long: intrinsic * quantity * multiplier - initial_cost
        # For short: -intrinsic * quantity * multiplier + credit received
        terminal_value = self.side_factor * self.quantity * intrinsic * self.multiplier
        return terminal_value - self.initial_cost


@dataclass
class OptionsChain:
    symbol: str
    name: Optional[str] = None
    legs: List[OptionLeg] = field(default_factory=list)
    underlying_entry_price: Optional[float] = None
    underlying_current_price: Optional[float] = None
    shares: int = 0
    share_entry_price: float = 0.0
    share_current_price: Optional[float] = None
    id: Optional[int] = None

    def add_leg(self, leg: OptionLeg) -> None:
        self.legs.append(leg)

    @property
    def net_initial_cost(self) -> float:
        """
        Total initial cost of the options chain + stock position.
        Positive = Net Debit (Out-of-pocket cost).
        Negative = Net Credit (Net cash received).
        """
        options_cost = sum(leg.initial_cost for leg in self.legs)
        stock_cost = self.shares * self.share_entry_price
        return options_cost + stock_cost

    @property
    def current_unrealized_pnl(self) -> float:
        """Total current mark-to-market unrealized profit/loss across all legs and shares."""
        options_pnl = sum(leg.unrealized_pnl for leg in self.legs)
        stock_cur = self.share_current_price if self.share_current_price is not None else self.share_entry_price
        stock_pnl = self.shares * (stock_cur - self.share_entry_price)
        return options_pnl + stock_pnl

    def payoff_at_expiration(self, underlying_price: float) -> float:
        """Integrated net PnL across all legs and shares at expiration for a given underlying price."""
        options_payoff = sum(leg.payoff_at_expiration(underlying_price) for leg in self.legs)
        stock_payoff = self.shares * (underlying_price - self.share_entry_price)
        return options_payoff + stock_payoff
