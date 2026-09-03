import pytest
from web_app import create_app
from options_chain.storage import ChainStorage
from options_chain.models import OptionsChain, OptionLeg, OptionType, OptionSide


@pytest.fixture
def client(tmp_path):
    db_file = str(tmp_path / "test_web.db")
    storage = ChainStorage(db_path=db_file)

    # Seed 1 active chain and 1 closed chain
    c1 = OptionsChain(
        symbol="AAPL",
        name="AAPL 2026-08-31 Strategy",
        active=True,
        opened_date="2026-08-31"
    )
    c1.add_leg(OptionLeg(
        strike=150.0,
        option_type=OptionType.CALL,
        side=OptionSide.BUY,
        quantity=1,
        entry_price=5.0,
        action="BUY_TO_OPEN",
        trade_date="2026-08-31"
    ))
    storage.save_chain(c1)

    c2 = OptionsChain(
        symbol="IBM",
        name="IBM 2026-07-14 Strategy",
        active=False,
        opened_date="2026-07-14",
        closed_date="2026-07-16"
    )
    c2.add_leg(OptionLeg(
        strike=200.0,
        option_type=OptionType.PUT,
        side=OptionSide.SELL,
        quantity=1,
        entry_price=3.80,
        action="SELL_TO_OPEN",
        trade_date="2026-07-14"
    ))
    c2.add_leg(OptionLeg(
        strike=200.0,
        option_type=OptionType.PUT,
        side=OptionSide.BUY,
        quantity=1,
        entry_price=1.80,
        action="BUY_TO_CLOSE",
        trade_date="2026-07-16"
    ))
    storage.save_chain(c2)

    app = create_app(db_path=db_file, sources_dir="sources")
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


def test_index_redirect(client):
    response = client.get("/")
    assert response.status_code == 302
    assert "/import" in response.headers["Location"]


def test_import_page(client):
    response = client.get("/import")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert "Broker Activity Import" in html
    assert "Positions Gathered Within Strategy Chains" in html


def test_chains_page_and_status_selector(client):
    # Test All
    response_all = client.get("/chains?status=all")
    assert response_all.status_code == 200
    html_all = response_all.get_data(as_text=True)
    assert "AAPL 2026-08-31 Strategy" in html_all
    assert "IBM 2026-07-14 Strategy" in html_all

    # Test Active Only
    response_active = client.get("/chains?status=active")
    assert response_active.status_code == 200
    html_active = response_active.get_data(as_text=True)
    assert "AAPL 2026-08-31 Strategy" in html_active
    assert "IBM 2026-07-14 Strategy" not in html_active

    # Test Closed Only
    response_closed = client.get("/chains?status=closed")
    assert response_closed.status_code == 200
    html_closed = response_closed.get_data(as_text=True)
    assert "IBM 2026-07-14 Strategy" in html_closed
    assert "AAPL 2026-08-31 Strategy" not in html_closed


def test_api_run_import(client):
    response = client.post("/api/run-import")
    assert response.status_code == 200
    data = response.get_json()
    assert data["success"] is True
    assert "processed_files" in data
    assert "new_legs" in data
    assert "skipped_duplicates" in data


def test_delete_and_revert_endpoints(client):
    # 1. Delete chain ID 1
    del_res = client.post("/api/chains/1/delete")
    assert del_res.status_code == 200
    del_data = del_res.get_json()
    assert del_data["success"] is True

    # 2. Check it does not appear in active
    res_active = client.get("/chains?status=active")
    assert "AAPL 2026-08-31 Strategy" not in res_active.get_data(as_text=True)

    # 3. Check it appears in deleted tab
    res_deleted = client.get("/chains?status=deleted")
    assert res_deleted.status_code == 200
    assert "AAPL 2026-08-31 Strategy" in res_deleted.get_data(as_text=True)
    assert "Revert" in res_deleted.get_data(as_text=True)

    # 4. Revert chain ID 1
    rev_res = client.post("/api/chains/1/revert")
    assert rev_res.status_code == 200
    rev_data = rev_res.get_json()
    assert rev_data["success"] is True

    # 5. Check it is restored back into active
    res_restored = client.get("/chains?status=active")
    assert "AAPL 2026-08-31 Strategy" in res_restored.get_data(as_text=True)
