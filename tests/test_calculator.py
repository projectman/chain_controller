import pytest
from options_chain.models import OptionsChain, OptionLeg, OptionType, OptionSide
from options_chain.calculator import ChainCalculator


def test_long_call_single_leg():
    chain = OptionsChain(symbol="AAPL", name="Single Long Call")
    chain.add_leg(OptionLeg(
        strike=150.0,
        option_type=OptionType.CALL,
        side=OptionSide.BUY,
        quantity=1,
        entry_price=5.0
    ))

    # Net cost should be +$500 (Debit)
    assert chain.net_initial_cost == 500.0

    # Payoffs
    assert chain.payoff_at_expiration(140.0) == -500.0
    assert chain.payoff_at_expiration(150.0) == -500.0
    assert chain.payoff_at_expiration(155.0) == 0.0
    assert chain.payoff_at_expiration(170.0) == 1500.0

    # Breakeven point
    breakevens = ChainCalculator.find_breakeven_points(chain)
    assert len(breakevens) == 1
    assert pytest.approx(breakevens[0], 0.01) == 155.0

    # Max profit / loss
    max_p, max_l, p_unbounded, l_unbounded = ChainCalculator.calculate_max_profit_and_loss(chain)
    assert p_unbounded is True
    assert l_unbounded is False
    assert max_l == -500.0


def test_bull_call_spread():
    chain = OptionsChain(symbol="TSLA", name="Bull Call Spread")
    # Buy 150 Call @ 5.0, Sell 160 Call @ 2.0
    chain.add_leg(OptionLeg(strike=150.0, option_type=OptionType.CALL, side=OptionSide.BUY, quantity=1, entry_price=5.0))
    chain.add_leg(OptionLeg(strike=160.0, option_type=OptionType.CALL, side=OptionSide.SELL, quantity=1, entry_price=2.0))

    # Net Outlay: 500 - 200 = $300 Debit
    assert chain.net_initial_cost == 300.0

    # Payoffs
    assert chain.payoff_at_expiration(140.0) == -300.0
    assert chain.payoff_at_expiration(150.0) == -300.0
    assert chain.payoff_at_expiration(153.0) == 0.0
    assert chain.payoff_at_expiration(160.0) == 700.0
    assert chain.payoff_at_expiration(180.0) == 700.0

    # Breakeven
    breakevens = ChainCalculator.find_breakeven_points(chain)
    assert len(breakevens) == 1
    assert pytest.approx(breakevens[0], 0.01) == 153.0

    # Max profit and loss
    max_p, max_l, p_unbounded, l_unbounded = ChainCalculator.calculate_max_profit_and_loss(chain)
    assert p_unbounded is False
    assert l_unbounded is False
    assert pytest.approx(max_p, 0.01) == 700.0
    assert pytest.approx(max_l, 0.01) == -300.0


def test_iron_condor_net_credit():
    chain = OptionsChain(symbol="SPY", name="Iron Condor")
    # BUY 1 Put 140 @ 1.0 (+100)
    # SELL 1 Put 145 @ 2.5 (-250)
    # SELL 1 Call 160 @ 2.5 (-250)
    # BUY 1 Call 165 @ 1.0 (+100)
    chain.add_leg(OptionLeg(strike=140.0, option_type=OptionType.PUT, side=OptionSide.BUY, quantity=1, entry_price=1.0))
    chain.add_leg(OptionLeg(strike=145.0, option_type=OptionType.PUT, side=OptionSide.SELL, quantity=1, entry_price=2.5))
    chain.add_leg(OptionLeg(strike=160.0, option_type=OptionType.CALL, side=OptionSide.SELL, quantity=1, entry_price=2.5))
    chain.add_leg(OptionLeg(strike=165.0, option_type=OptionType.CALL, side=OptionSide.BUY, quantity=1, entry_price=1.0))

    # Net Cost: 100 - 250 - 250 + 100 = -$300 (Net Credit)
    assert chain.net_initial_cost == -300.0

    # Max profit inside [145, 160] = +$300
    assert chain.payoff_at_expiration(150.0) == 300.0

    # Breakevens: 142.0 and 163.0
    breakevens = ChainCalculator.find_breakeven_points(chain)
    assert len(breakevens) == 2
    assert pytest.approx(breakevens[0], 0.01) == 142.0
    assert pytest.approx(breakevens[1], 0.01) == 163.0

    # Max Loss: -$200 (Risk width 5 - 3 credit = $200 loss)
    max_p, max_l, p_unbounded, l_unbounded = ChainCalculator.calculate_max_profit_and_loss(chain)
    assert pytest.approx(max_p, 0.01) == 300.0
    assert pytest.approx(max_l, 0.01) == -200.0


def test_chain_analysis_summary():
    chain = OptionsChain(symbol="NVDA", name="NVDA Straddle")
    chain.add_leg(OptionLeg(strike=100.0, option_type=OptionType.CALL, side=OptionSide.BUY, quantity=1, entry_price=4.0))
    chain.add_leg(OptionLeg(strike=100.0, option_type=OptionType.PUT, side=OptionSide.BUY, quantity=1, entry_price=4.0))

    summary = ChainCalculator.analyze_chain(chain)
    assert summary["symbol"] == "NVDA"
    assert summary["net_initial_cost"] == 800.0
    assert summary["cost_type"] == "Net Debit"
    assert len(summary["breakeven_points"]) == 2
    assert 92.0 in summary["breakeven_points"]
    assert 108.0 in summary["breakeven_points"]
