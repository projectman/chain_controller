import pytest
from options_chain.storage import ChainStorage
from options_chain.models import OptionsChain, OptionLeg, OptionType, OptionSide
from options_chain.calculator import ChainCalculator
from web_app import create_app


@pytest.fixture
def join_setup(tmp_path):
    db_file = str(tmp_path / "test_join.db")
    storage = ChainStorage(db_path=db_file)

    # 1. PFE Chain 1: Long Put (e.g. #149)
    c1 = OptionsChain(
        symbol="PFE",
        name="PFE 2026-09-21 Strategy",
        active=True,
        opened_date="2026-09-21"
    )
    c1.add_leg(OptionLeg(
        strike=25.0,
        option_type=OptionType.PUT,
        side=OptionSide.BUY,
        quantity=8,
        entry_price=3.50,
        action="BUY_TO_OPEN",
        trade_date="2026-09-21",
        expiration_date="2027-09-17",
        occ_symbol="PFE270917P25"
    ))
    id1 = storage.save_chain(c1)

    # 2. PFE Chain 2: Short Call (e.g. #157)
    c2 = OptionsChain(
        symbol="PFE",
        name="PFE 2026-09-24 Strategy",
        active=True,
        opened_date="2026-09-24"
    )
    c2.add_leg(OptionLeg(
        strike=30.0,
        option_type=OptionType.CALL,
        side=OptionSide.SELL,
        quantity=8,
        entry_price=1.20,
        action="SELL_TO_OPEN",
        trade_date="2026-09-24",
        expiration_date="2026-11-20",
        occ_symbol="PFE261120C30"
    ))
    id2 = storage.save_chain(c2)

    # 3. AAPL Chain: Different Symbol
    c3 = OptionsChain(
        symbol="AAPL",
        name="AAPL 2026-09-20 Strategy",
        active=True,
        opened_date="2026-09-20"
    )
    c3.add_leg(OptionLeg(
        strike=220.0,
        option_type=OptionType.CALL,
        side=OptionSide.BUY,
        quantity=1,
        entry_price=5.00,
        action="BUY_TO_OPEN",
        trade_date="2026-09-20",
        expiration_date="2026-10-16",
        occ_symbol="AAPL261016C220"
    ))
    id3 = storage.save_chain(c3)

    app = create_app(db_path=db_file, sources_dir="sources")
    app.config["TESTING"] = True
    client = app.test_client()

    return {
        "storage": storage,
        "app": app,
        "client": client,
        "id1": id1,
        "id2": id2,
        "id3": id3
    }


def test_join_chain_storage(join_setup):
    storage = join_setup["storage"]
    id1, id2, id3 = join_setup["id1"], join_setup["id2"], join_setup["id3"]

    # 1. Validation: Self join
    ok, msg = storage.join_chain(id2, id2)
    assert not ok
    assert "itself" in msg

    # 2. Validation: Mismatched symbol (AAPL into PFE)
    ok, msg = storage.join_chain(id3, id1)
    assert not ok
    assert "Symbol mismatch" in msg

    # 3. Validation: Non-existent parent
    ok, msg = storage.join_chain(id2, 9999)
    assert not ok
    assert "not found" in msg

    # 4. Successful Join: PFE id2 into id1
    ok, msg = storage.join_chain(child_id=id2, parent_id=id1)
    assert ok
    assert f"Position #{id2} successfully joined to Parent #{id1}." in msg

    # Verify parent loads combined legs
    parent_chain = storage.get_chain(id1, include_children=True)
    assert len(parent_chain.legs) == 2
    assert parent_chain.child_chain_ids == [id2]
    
    # Check that child leg has is_child = True and source_chain_id = id2
    child_leg = [l for l in parent_chain.legs if l.source_chain_id == id2][0]
    assert child_leg.is_child is True
    assert child_leg.strike == 30.0

    # Verify child chain knows its parent
    child_chain = storage.get_chain(id2)
    assert child_chain.parent_chain_id == id1

    # 5. Validation: Circular hierarchy (id1 cannot join id2)
    ok, msg = storage.join_chain(child_id=id1, parent_id=id2)
    assert not ok
    assert "circular" in msg.lower()

    # 6. Unjoin
    ok_unjoin, msg_unjoin = storage.unjoin_chain(child_id=id2)
    assert ok_unjoin
    parent_after = storage.get_chain(id1, include_children=True)
    assert len(parent_after.legs) == 1
    assert parent_after.child_chain_ids == []
    child_after = storage.get_chain(id2)
    assert child_after.parent_chain_id is None


def test_join_chain_api_endpoints(join_setup):
    client = join_setup["client"]
    id1, id2, id3 = join_setup["id1"], join_setup["id2"], join_setup["id3"]

    # 1. API Join: id2 -> id1
    res = client.post(f"/api/chains/{id2}/join", json={"parent_id": id1})
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True

    # Check /chains HTML has Child badge and Unjoin button
    chains_res = client.get("/chains")
    html = chains_res.get_data(as_text=True)
    assert f"Child of #{id1}" in html
    assert f"Parent (1 child: #{id2})" in html
    assert f"unjoinChain({id2}, {id1}" in html

    # 2. Check no double-counting in totals
    # c1 outlay: 8 * 3.50 * 100 = $2,800 debit
    # c2 outlay: -8 * 1.20 * 100 = -$960 credit
    # Net outlay for unified position: $1,840 debit
    # AAPL c3 outlay: 1 * 5.00 * 100 = $500 debit
    # Total for selection should be $1,840 + $500 = $2,340 (NOT adding c2 again!)
    assert "$2,340.00" in html

    # 3. API Unjoin: id2
    unjoin_res = client.post(f"/api/chains/{id2}/unjoin")
    assert unjoin_res.status_code == 200
    unjoin_data = unjoin_res.get_json()
    assert unjoin_data["success"] is True

    # Check /chains HTML after unjoin
    chains_after = client.get("/chains")
    html_after = chains_after.get_data(as_text=True)
    assert f"Child of #{id1}" not in html_after
    assert f"Parent (1 child: #{id2})" not in html_after
