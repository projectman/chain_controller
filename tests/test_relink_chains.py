import pytest
from options_chain.storage import ChainStorage
from options_chain.models import OptionsChain, OptionLeg, OptionType, OptionSide, TradeAction
from web_app import create_app


def test_relink_orphaned_close(tmp_path):
    db_file = str(tmp_path / "test_relink.db")
    storage = ChainStorage(db_path=db_file)

    # Chain 1: Target chain opened on 2026-09-01 with a short put
    c1 = OptionsChain(
        symbol="XYZ",
        name="XYZ 2026-09-01 Strategy",
        active=True,
        opened_date="2026-09-01"
    )
    c1.add_leg(OptionLeg(
        strike=100.0,
        option_type=OptionType.PUT,
        side=OptionSide.SELL,
        quantity=2,
        entry_price=4.50,
        action=TradeAction.SELL_TO_OPEN.value,
        trade_date="2026-09-01",
        occ_symbol="XYZ261016P00100000"
    ))
    id1 = storage.save_chain(c1)

    # Chain 2: Candidate chain opened on 2026-09-15 with a BUY_TO_CLOSE matching Chain 1
    c2 = OptionsChain(
        symbol="XYZ",
        name="XYZ 2026-09-15 Closing",
        active=False,
        opened_date="2026-09-15"
    )
    c2.add_leg(OptionLeg(
        strike=100.0,
        option_type=OptionType.PUT,
        side=OptionSide.BUY,
        quantity=2,
        entry_price=1.20,
        action=TradeAction.BUY_TO_CLOSE.value,
        trade_date="2026-09-15",
        occ_symbol="XYZ261016P00100000"
    ))
    id2 = storage.save_chain(c2)

    # Both chains initially exist separately
    assert storage.get_chain(id1) is not None
    assert storage.get_chain(id2) is not None

    # Run relink
    res = storage.relink_chains()
    assert res["stitched_legs"] == 1
    assert res["merged_chains"] == 1

    # Chain 2 should be deleted as all its legs moved to Chain 1
    assert storage.get_chain(id2) is None

    # Chain 1 should now have 2 legs, be closed, and have closed_date = "2026-09-15"
    c1_updated = storage.get_chain(id1)
    assert c1_updated is not None
    assert len(c1_updated.legs) == 2
    assert c1_updated.active is False
    assert c1_updated.closed_date == "2026-09-15"


def test_relink_rolling_legs(tmp_path):
    db_file = str(tmp_path / "test_relink_roll.db")
    storage = ChainStorage(db_path=db_file)

    # Chain 1: Target chain opened on 2026-09-01 with 90P
    c1 = OptionsChain(
        symbol="CSCO",
        name="CSCO 2026-09-01 Strategy",
        active=True,
        opened_date="2026-09-01"
    )
    c1.add_leg(OptionLeg(
        strike=90.0,
        option_type=OptionType.PUT,
        side=OptionSide.SELL,
        quantity=3,
        entry_price=2.00,
        action=TradeAction.SELL_TO_OPEN.value,
        trade_date="2026-09-01",
        occ_symbol="CSCO261016P00090000"
    ))
    id1 = storage.save_chain(c1)

    # Chain 2: Candidate chain opened on 2026-09-10 with roll (BTC 90P and STO 100P)
    c2 = OptionsChain(
        symbol="CSCO",
        name="CSCO 2026-09-10 Strategy",
        active=True,
        opened_date="2026-09-10"
    )
    c2.add_leg(OptionLeg(
        strike=90.0,
        option_type=OptionType.PUT,
        side=OptionSide.BUY,
        quantity=3,
        entry_price=0.50,
        action=TradeAction.BUY_TO_CLOSE.value,
        trade_date="2026-09-10",
        occ_symbol="CSCO261016P00090000"
    ))
    c2.add_leg(OptionLeg(
        strike=100.0,
        option_type=OptionType.PUT,
        side=OptionSide.SELL,
        quantity=3,
        entry_price=3.00,
        action=TradeAction.SELL_TO_OPEN.value,
        trade_date="2026-09-10",
        occ_symbol="CSCO261120P00100000"
    ))
    id2 = storage.save_chain(c2)

    # Run relink
    res = storage.relink_chains()
    assert res["stitched_legs"] == 2  # BTC 90P + STO 100P
    assert res["merged_chains"] == 1

    # Chain 2 merged into Chain 1
    assert storage.get_chain(id2) is None

    c1_updated = storage.get_chain(id1)
    assert c1_updated is not None
    assert len(c1_updated.legs) == 3
    assert c1_updated.active is True  # 100P is still open!
    assert c1_updated.closed_date is None


def test_relink_protected_chains_not_touched(tmp_path):
    db_file = str(tmp_path / "test_relink_protected.db")
    storage = ChainStorage(db_path=db_file)

    # Chain 1: Parent
    c1 = OptionsChain(
        symbol="PFE",
        name="PFE Parent Strategy",
        active=True,
        opened_date="2026-08-01"
    )
    c1.add_leg(OptionLeg(
        strike=30.0,
        option_type=OptionType.PUT,
        side=OptionSide.SELL,
        quantity=1,
        entry_price=1.00,
        action=TradeAction.SELL_TO_OPEN.value,
        trade_date="2026-08-01",
        occ_symbol="PFE260918P00030000"
    ))
    id1 = storage.save_chain(c1)

    # Chain 2: Child (manually joined to Parent #1)
    c2 = OptionsChain(
        symbol="PFE",
        name="PFE Child Strategy",
        active=True,
        opened_date="2026-08-15"
    )
    c2.add_leg(OptionLeg(
        strike=32.0,
        option_type=OptionType.PUT,
        side=OptionSide.SELL,
        quantity=1,
        entry_price=1.20,
        action=TradeAction.SELL_TO_OPEN.value,
        trade_date="2026-08-15",
        occ_symbol="PFE261016P00032000"
    ))
    id2 = storage.save_chain(c2)

    # Join c2 to c1
    ok, msg = storage.join_chain(child_id=id2, parent_id=id1)
    assert ok

    # Chain 3: A third chain with BUY_TO_CLOSE 30P
    c3 = OptionsChain(
        symbol="PFE",
        name="PFE Separate Chain",
        active=False,
        opened_date="2026-09-01"
    )
    c3.add_leg(OptionLeg(
        strike=30.0,
        option_type=OptionType.PUT,
        side=OptionSide.BUY,
        quantity=1,
        entry_price=0.20,
        action=TradeAction.BUY_TO_CLOSE.value,
        trade_date="2026-09-01",
        occ_symbol="PFE260918P00030000"
    ))
    id3 = storage.save_chain(c3)

    # Run relink
    res = storage.relink_chains()
    assert res["protected_chains_count"] == 2

    # Because Chain 1 is a protected parent and Chain 2 is a protected child,
    # Chain 3 must NOT be merged into either of them!
    assert storage.get_chain(id1) is not None
    assert storage.get_chain(id2) is not None
    assert storage.get_chain(id3) is not None
    # Check that c2 is still joined to c1
    c2_chk = storage.get_chain(id2)
    assert c2_chk.parent_chain_id == id1


def test_api_rebuild_chains(tmp_path):
    db_file = str(tmp_path / "test_api_rebuild.db")
    storage = ChainStorage(db_path=db_file)

    c1 = OptionsChain(
        symbol="TEST",
        name="TEST 2026-09-01 Strategy",
        active=True,
        opened_date="2026-09-01"
    )
    c1.add_leg(OptionLeg(
        strike=50.0,
        option_type=OptionType.CALL,
        side=OptionSide.BUY,
        quantity=1,
        entry_price=2.00,
        action=TradeAction.BUY_TO_OPEN.value,
        trade_date="2026-09-01",
        occ_symbol="TEST261016C00050000"
    ))
    id1 = storage.save_chain(c1)

    c2 = OptionsChain(
        symbol="TEST",
        name="TEST 2026-09-10 Closing",
        active=False,
        opened_date="2026-09-10"
    )
    c2.add_leg(OptionLeg(
        strike=50.0,
        option_type=OptionType.CALL,
        side=OptionSide.SELL,
        quantity=1,
        entry_price=3.00,
        action=TradeAction.SELL_TO_CLOSE.value,
        trade_date="2026-09-10",
        occ_symbol="TEST261016C00050000"
    ))
    id2 = storage.save_chain(c2)

    app = create_app(db_path=db_file)
    client = app.test_client()

    res = client.post("/api/chains/rebuild")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert data["stitched_legs"] == 1
    assert data["merged_chains"] == 1
    assert "stitched 1 legs" in data["message"]
