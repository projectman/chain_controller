import os
import pytest
from options_chain.activity_parser import parse_occ_symbol, ActivityParser
from options_chain.models import OptionType, OptionSide, TradeAction
from options_chain.storage import ChainStorage


def test_parse_occ_symbol():
    # Compact OCC format
    res1 = parse_occ_symbol("IBM260724P200")
    assert res1["symbol"] == "IBM"
    assert res1["expiration_date"] == "2026-07-24"
    assert res1["option_type"] == OptionType.PUT
    assert res1["strike"] == 200.0

    # Standard 8-digit OCC format
    res2 = parse_occ_symbol("AAPL260821C00150000")
    assert res2["symbol"] == "AAPL"
    assert res2["expiration_date"] == "2026-08-21"
    assert res2["option_type"] == OptionType.CALL
    assert res2["strike"] == 150.0


def test_parse_fidelity_activity_csv():
    sources_dir = os.path.join(os.path.dirname(__file__), "..", "sources")
    sample_file = os.path.join(sources_dir, "Activity_Traditional_IRA_*0663.csv")
    import glob
    matching_files = glob.glob(sample_file)

    if not matching_files:
        pytest.skip("Sample Fidelity CSV file not found in sources/")

    chains = ActivityParser.parse_csv_file(matching_files[0])
    assert len(chains) == 1

    ibm_chain = chains[0]
    assert ibm_chain.symbol == "IBM"
    assert ibm_chain.name == "IBM 2026-07-14 Strategy"
    assert ibm_chain.opened_date == "2026-07-14"
    assert ibm_chain.closed_date == "2026-07-16"
    assert ibm_chain.active is False
    assert len(ibm_chain.legs) == 2

    # Leg 1: SELL_TO_OPEN 1 PUT 200 @ 3.80
    leg1 = ibm_chain.legs[0]
    assert leg1.side == OptionSide.SELL
    assert leg1.action == TradeAction.SELL_TO_OPEN.value
    assert leg1.strike == 200.0
    assert leg1.entry_price == 3.80
    assert leg1.commission == 0.65
    assert leg1.fees == 0.01
    assert leg1.tx_hash is not None

    # Leg 2: BUY_TO_CLOSE 1 PUT 200 @ 1.80
    leg2 = ibm_chain.legs[1]
    assert leg2.side == OptionSide.BUY
    assert leg2.action == TradeAction.BUY_TO_CLOSE.value
    assert leg2.strike == 200.0
    assert leg2.entry_price == 1.80
    assert leg2.commission == 0.65
    assert leg2.fees == 0.01
    assert leg2.tx_hash is not None

    # Net Outlay: -380 (credit) + 0.66 + 180 (debit) + 0.66 = -198.68 (Net Credit received / profit)
    assert pytest.approx(ibm_chain.net_initial_cost, 0.01) == -198.68


def test_import_sources_to_sqlite(tmp_path):
    db_file = str(tmp_path / "test_sources.db")
    storage = ChainStorage(db_path=db_file)
    sources_dir = os.path.join(os.path.dirname(__file__), "..", "sources")

    res = ActivityParser.import_sources_folder(sources_dir=sources_dir, storage=storage)
    assert res["processed_files"] >= 1
    assert res["new_legs"] >= 2

    saved = storage.get_chain_by_name("IBM 2026-07-14 Strategy")
    assert saved is not None
    assert saved.symbol == "IBM"
    assert saved.active is False
    assert saved.opened_date == "2026-07-14"
    assert saved.closed_date == "2026-07-16"


def test_import_deduplication(tmp_path):
    db_file = str(tmp_path / "test_dedup.db")
    storage = ChainStorage(db_path=db_file)
    sources_dir = os.path.join(os.path.dirname(__file__), "..", "sources")

    # Run 1: Should import all new legs from sources
    res1 = ActivityParser.import_sources_folder(sources_dir=sources_dir, storage=storage)
    initial_legs = res1["new_legs"]
    assert initial_legs >= 2
    assert res1["skipped_duplicates"] == 0

    # Run 2: Re-import same folder. Should skip all legs as duplicates!
    res2 = ActivityParser.import_sources_folder(sources_dir=sources_dir, storage=storage)
    assert res2["new_legs"] == 0
    assert res2["skipped_duplicates"] == initial_legs

    # Verify legs count in DB for IBM chain is still exactly 2
    chain = storage.get_chain_by_name("IBM 2026-07-14 Strategy")
    assert chain is not None
    assert len(chain.legs) == 2


def test_same_day_underlying_grouping_and_position_interaction(tmp_path):
    """Verifies that opening trades on the same day for an underlying group together,
    and closing trades across dates match and close the active chain."""
    db_file = str(tmp_path / "test_interaction.db")
    storage = ChainStorage(db_path=db_file)
    sources_dir = os.path.join(os.path.dirname(__file__), "..", "sources")

    res = ActivityParser.import_sources_folder(sources_dir=sources_dir, storage=storage)

    # 1. WFC: 2 opening legs on 2026-08-27 with different expirations (2027-09-17 and 2026-10-16)
    wfc_chain = storage.get_chain_by_name("WFC 2026-08-27 Strategy")
    assert wfc_chain is not None
    assert wfc_chain.symbol == "WFC"
    assert len(wfc_chain.legs) == 2
    assert wfc_chain.active is True
    assert wfc_chain.opened_date == "2026-08-27"
    assert wfc_chain.closed_date is None

    # 2. WDC: 2 opening legs on 2026-08-24, closed with 2 closing legs on 2026-08-31
    wdc_chain = storage.get_chain_by_name("WDC 2026-08-24 Strategy")
    assert wdc_chain is not None
    assert wdc_chain.symbol == "WDC"
    assert len(wdc_chain.legs) == 4
    assert wdc_chain.active is False
    assert wdc_chain.opened_date == "2026-08-24"
    assert wdc_chain.closed_date == "2026-08-31"

    # 3. QQQ: 3 opening legs on 2026-08-31 (butterfly spread)
    qqq_chain = storage.get_chain_by_name("QQQ 2026-08-31 Strategy")
    assert qqq_chain is not None
    assert qqq_chain.symbol == "QQQ"
    assert len(qqq_chain.legs) == 3
    assert qqq_chain.active is True
    assert qqq_chain.opened_date == "2026-08-31"
