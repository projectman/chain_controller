import pytest
from datetime import date
from options_chain.models import OptionsChain, OptionLeg, OptionType, OptionSide
from options_chain.storage import ChainStorage
from options_chain.short_puts_analyzer import ShortPutsAnalyzer
from web_app import create_app


def test_short_puts_analyzer_classification_and_metrics(tmp_path):
    db_file = str(tmp_path / "test_analyzer.db")
    storage = ChainStorage(db_path=db_file)

    # 1. Short Put: IBM July 14, closed on July 16
    c1 = OptionsChain(symbol="IBM", name="IBM 2026-07-14 Strategy", active=False, opened_date="2026-07-14", closed_date="2026-07-16")
    c1.add_leg(OptionLeg(strike=200.0, option_type=OptionType.PUT, side=OptionSide.SELL, quantity=1, entry_price=3.80, action="SELL_TO_OPEN", trade_date="2026-07-14", expiration_date="2026-07-24", occ_symbol="IBM260724P200"))
    c1.add_leg(OptionLeg(strike=200.0, option_type=OptionType.PUT, side=OptionSide.BUY, quantity=1, entry_price=1.80, action="BUY_TO_CLOSE", trade_date="2026-07-16", expiration_date="2026-07-24", occ_symbol="IBM260724P200"))
    storage.save_chain(c1)

    # 2. Credit Short Puts Spread: WDC Aug 24, closed on Aug 31
    # Short put 310, Long put 200, both expiring 2026-10-16
    c2 = OptionsChain(symbol="WDC", name="WDC 2026-08-24 Strategy", active=False, opened_date="2026-08-24", closed_date="2026-08-31")
    c2.add_leg(OptionLeg(strike=310.0, option_type=OptionType.PUT, side=OptionSide.SELL, quantity=1, entry_price=15.0, action="SELL_TO_OPEN", trade_date="2026-08-24", expiration_date="2026-10-16", occ_symbol="WDC261016P310"))
    c2.add_leg(OptionLeg(strike=200.0, option_type=OptionType.PUT, side=OptionSide.BUY, quantity=1, entry_price=2.0, action="BUY_TO_OPEN", trade_date="2026-08-24", expiration_date="2026-10-16", occ_symbol="WDC261016P200"))
    c2.add_leg(OptionLeg(strike=310.0, option_type=OptionType.PUT, side=OptionSide.BUY, quantity=1, entry_price=5.0, action="BUY_TO_CLOSE", trade_date="2026-08-31", expiration_date="2026-10-16", occ_symbol="WDC261016P310"))
    c2.add_leg(OptionLeg(strike=200.0, option_type=OptionType.PUT, side=OptionSide.SELL, quantity=1, entry_price=0.5, action="SELL_TO_CLOSE", trade_date="2026-08-31", expiration_date="2026-10-16", occ_symbol="WDC261016P200"))
    storage.save_chain(c2)

    # 3. Active Short Put: LOW Aug 27, still open
    c3 = OptionsChain(symbol="LOW", name="LOW 2026-08-27 Strategy", active=True, opened_date="2026-08-27")
    c3.add_leg(OptionLeg(strike=195.0, option_type=OptionType.PUT, side=OptionSide.SELL, quantity=1, entry_price=4.50, action="SELL_TO_OPEN", trade_date="2026-08-27", expiration_date="2026-10-16", occ_symbol="LOW261016P195"))
    storage.save_chain(c3)

    # 4. Long Call (non-qualifying, should be ignored)
    c4 = OptionsChain(symbol="AAPL", name="AAPL Call", active=True, opened_date="2026-08-20")
    c4.add_leg(OptionLeg(strike=220.0, option_type=OptionType.CALL, side=OptionSide.BUY, quantity=1, entry_price=5.0, action="BUY_TO_OPEN", trade_date="2026-08-20", expiration_date="2026-09-18"))
    storage.save_chain(c4)

    # 5. Diagonal Credit Spread: NKE Long Put Aug 21, Short Put Aug 26 (different strikes & different expirations)
    c5 = OptionsChain(symbol="NKE", name="NKE 2026-08-21 Strategy", active=True, opened_date="2026-08-21")
    c5.add_leg(OptionLeg(strike=25.0, option_type=OptionType.PUT, side=OptionSide.BUY, quantity=2, entry_price=1.00, action="BUY_TO_OPEN", trade_date="2026-08-21", expiration_date="2026-10-16", occ_symbol="NKE261016P25"))
    storage.save_chain(c5)

    c6 = OptionsChain(symbol="NKE", name="NKE 2026-08-26 Strategy", active=True, opened_date="2026-08-26")
    c6.add_leg(OptionLeg(strike=37.5, option_type=OptionType.PUT, side=OptionSide.SELL, quantity=2, entry_price=2.50, action="SELL_TO_OPEN", trade_date="2026-08-26", expiration_date="2026-11-20", occ_symbol="NKE261120P37.5"))
    storage.save_chain(c6)

    # Test qualifying positions
    positions = ShortPutsAnalyzer.find_qualifying_positions(storage, initial_date="2026-07-01")
    assert len(positions) == 4

    # Check classification and naming
    ibm_pos = next(p for p in positions if p["symbol"] == "IBM")
    assert ibm_pos["strategy_type"] == "Short Put"
    assert ibm_pos["name"] == "IBM 2026-07-14 Short Put"
    assert ibm_pos["risk"] == 200.0 * 1 * 100.0  # $20,000
    assert ibm_pos["realized_profit"] == pytest.approx((3.80 - 1.80) * 100.0)  # $200.00
    assert ibm_pos["active"] is False

    wdc_pos = next(p for p in positions if p["symbol"] == "WDC")
    assert wdc_pos["strategy_type"] == "Credit Short Puts Spread"
    assert wdc_pos["name"] == "WDC 2026-08-24 Credit Short Puts Spread"
    assert wdc_pos["risk"] == (310.0 - 200.0) * 1 * 100.0  # $11,000 spread width
    assert wdc_pos["realized_profit"] > 0
    assert wdc_pos["active"] is False

    low_pos = next(p for p in positions if p["symbol"] == "LOW")
    assert low_pos["strategy_type"] == "Short Put"
    assert low_pos["active"] is True

    nke_pos = next(p for p in positions if p["symbol"] == "NKE")
    assert nke_pos["strategy_type"] == "Diagonal Credit Spread"
    assert nke_pos["name"] == "NKE 2026-08-26 Diagonal Credit Spread"
    assert nke_pos["risk"] == (37.5 - 25.0) * 2 * 100.0  # $2,500 spread width
    assert nke_pos["active"] is True

    # Test daily metrics simulation
    metrics = ShortPutsAnalyzer.compute_daily_metrics(positions, initial_date="2026-07-14", end_date="2026-09-01")
    assert metrics["kpis"]["total_positions"] == 4
    assert metrics["kpis"]["active_positions"] == 2
    assert metrics["kpis"]["closed_positions"] == 2
    assert "total_premium_at_risk" in metrics["kpis"]
    assert "total_premium_received" in metrics["kpis"]
    assert metrics["kpis"]["total_premium_at_risk"] == 450.0 + 500.0  # LOW ($450) + NKE ($500)
    assert metrics["kpis"]["total_premium_received"] > 0
    assert len(metrics["charts"]["labels"]) > 40
    assert len(metrics["charts"]["risk_series"]) == len(metrics["charts"]["labels"])
    assert len(metrics["charts"]["profit_series"]) == len(metrics["charts"]["labels"])
    assert len(metrics["charts"]["annualized_roi_series"]) == len(metrics["charts"]["labels"])

    # On July 15, IBM was open, risk should be $20,000
    idx_july15 = metrics["charts"]["labels"].index("2026-07-15")
    assert metrics["charts"]["risk_series"][idx_july15] == 20000.0

    # On July 17, IBM was closed, profit should be $200
    idx_july17 = metrics["charts"]["labels"].index("2026-07-17")
    assert metrics["charts"]["profit_series"][idx_july17] == 200.0


def test_short_puts_web_endpoints(tmp_path):
    db_file = str(tmp_path / "test_web_analyzer.db")
    storage = ChainStorage(db_path=db_file)

    # Seed short put
    c = OptionsChain(symbol="TGT", name="TGT 2026-07-07 Strategy", active=True, opened_date="2026-07-07")
    c.add_leg(OptionLeg(strike=110.0, option_type=OptionType.PUT, side=OptionSide.SELL, quantity=1, entry_price=2.50, action="SELL_TO_OPEN", trade_date="2026-07-07", expiration_date="2026-08-21", occ_symbol="TGT260821P110"))
    storage.save_chain(c)

    app = create_app(db_path=db_file, sources_dir="sources")
    app.config["TESTING"] = True
    with app.test_client() as client:
        # 1. Page test (All)
        res = client.get("/short-puts?initial_date=2026-07-01&status=all")
        assert res.status_code == 200
        html = res.get_data(as_text=True)
        assert "Short Puts &amp; Credit Puts Analyzer" in html or "Short Puts" in html
        assert "TGT 2026-07-07 Short Put" in html
        assert "Active (1)" in html
        assert "Closed (0)" in html
        assert "riskChart" in html
        assert "profitChart" in html
        assert "roiChart" in html

        # 2. Active filter
        res_active = client.get("/short-puts?initial_date=2026-07-01&status=active")
        assert res_active.status_code == 200
        assert "TGT 2026-07-07 Short Put" in res_active.get_data(as_text=True)

        # 3. Closed filter (TGT is active, so should show 0 closed rows)
        res_closed = client.get("/short-puts?initial_date=2026-07-01&status=closed")
        assert res_closed.status_code == 200
        assert "No qualifying Short Put or Credit Put Spread positions found" in res_closed.get_data(as_text=True)

        # 4. API test
        api_res = client.get("/api/short-puts/data?initial_date=2026-07-01")
        assert api_res.status_code == 200
        data = api_res.get_json()
        assert "metrics" in data
        assert "positions" in data
        assert len(data["positions"]) == 1
        assert data["positions"][0]["name"] == "TGT 2026-07-07 Short Put"


def test_already_open_positions_included_on_initial_date(tmp_path):
    """Verifies that positions opened prior to initial_date but still open on initial_date
    have their risk counted on Day 1 of the evaluation period, and are not 0 risk."""
    db_file = str(tmp_path / "test_initial_date_risk.db")
    storage = ChainStorage(db_path=db_file)

    # Position 1: Opened on Aug 01, closed on Aug 10 (already closed BEFORE evaluation window)
    c1 = OptionsChain(symbol="CLOSED_EARLY", name="CLOSED_EARLY 2026-08-01 Strategy", active=False, opened_date="2026-08-01", closed_date="2026-08-10")
    c1.add_leg(OptionLeg(strike=100.0, option_type=OptionType.PUT, side=OptionSide.SELL, quantity=1, entry_price=2.0, action="SELL_TO_OPEN", trade_date="2026-08-01", expiration_date="2026-09-18"))
    c1.add_leg(OptionLeg(strike=100.0, option_type=OptionType.PUT, side=OptionSide.BUY, quantity=1, entry_price=1.0, action="BUY_TO_CLOSE", trade_date="2026-08-10", expiration_date="2026-09-18"))
    storage.save_chain(c1)

    # Position 2: Opened on Aug 05, STILL ACTIVE during evaluation window
    c2 = OptionsChain(symbol="OPEN_PREV", name="OPEN_PREV 2026-08-05 Strategy", active=True, opened_date="2026-08-05")
    c2.add_leg(OptionLeg(strike=200.0, option_type=OptionType.PUT, side=OptionSide.SELL, quantity=1, entry_price=5.0, action="SELL_TO_OPEN", trade_date="2026-08-05", expiration_date="2026-10-16"))
    storage.save_chain(c2)

    # Position 3: Opened on Aug 15, closed on Aug 25 (closed DURING evaluation window)
    c3 = OptionsChain(symbol="CLOSED_DURING", name="CLOSED_DURING 2026-08-15 Strategy", active=False, opened_date="2026-08-15", closed_date="2026-08-25")
    c3.add_leg(OptionLeg(strike=150.0, option_type=OptionType.PUT, side=OptionSide.SELL, quantity=1, entry_price=3.0, action="SELL_TO_OPEN", trade_date="2026-08-15", expiration_date="2026-09-18"))
    c3.add_leg(OptionLeg(strike=150.0, option_type=OptionType.PUT, side=OptionSide.BUY, quantity=1, entry_price=1.0, action="BUY_TO_CLOSE", trade_date="2026-08-25", expiration_date="2026-09-18"))
    storage.save_chain(c3)

    # Evaluate for 30D window starting on Aug 20
    eval_date = "2026-08-20"
    positions = ShortPutsAnalyzer.find_qualifying_positions(storage, initial_date=eval_date)

    # CLOSED_EARLY (closed Aug 10) must be excluded
    # OPEN_PREV and CLOSED_DURING must be included
    syms = [p["symbol"] for p in positions]
    assert "CLOSED_EARLY" not in syms
    assert "OPEN_PREV" in syms
    assert "CLOSED_DURING" in syms

    metrics = ShortPutsAnalyzer.compute_daily_metrics(positions, initial_date=eval_date, end_date="2026-08-31")

    # On Day 1 (Aug 20), BOTH OPEN_PREV ($20k) and CLOSED_DURING ($15k) are open!
    # Day 1 risk MUST be $35,000, NOT $0!
    assert metrics["charts"]["labels"][0] == "2026-08-20"
    day1_risk = metrics["charts"]["risk_series"][0]
    assert day1_risk == 35000.0  # 200*100 + 150*100

    # On Aug 26 (after CLOSED_DURING closed on Aug 25), risk drops to $20k
    idx_aug26 = metrics["charts"]["labels"].index("2026-08-26")
    assert metrics["charts"]["risk_series"][idx_aug26] == 20000.0

    # Realized profit on Day 1 is $0, and becomes $200 on Aug 25 when CLOSED_DURING closes
    assert metrics["charts"]["profit_series"][0] == 0.0
    assert metrics["charts"]["profit_series"][idx_aug26] == 200.0
