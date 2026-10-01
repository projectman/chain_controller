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

    sources_dir = tmp_path / "sources"
    sources_dir.mkdir(exist_ok=True)
    app = create_app(db_path=db_file, sources_dir=str(sources_dir))
    app.config["TESTING"] = True
    with app.test_client() as c:
        c.storage = storage
        yield c




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


def test_simple_view_and_leg_delete_revert(client):
    # 1. Simple view all
    res_simple = client.get("/chains?view=simple&status=all")
    assert res_simple.status_code == 200
    html = res_simple.get_data(as_text=True)
    assert "Simple" in html
    assert "Leg ID" in html

    # 2. Delete leg 1
    del_leg_res = client.post("/api/legs/1/delete")
    assert del_leg_res.status_code == 200
    assert del_leg_res.get_json()["success"] is True

    # 3. Check leg 1 in deleted tab
    res_del_simple = client.get("/chains?view=simple&status=deleted")
    assert res_del_simple.status_code == 200
    assert "#1" in res_del_simple.get_data(as_text=True)

    # 4. Revert leg 1
    rev_leg_res = client.post("/api/legs/1/revert")
    assert rev_leg_res.status_code == 200
    assert rev_leg_res.get_json()["success"] is True


def test_chains_date_presets(client):
    # 1. 1M preset: only AAPL (opened 2026-08-31) should match, IBM (opened 2026-07-14) is > 30 days ago
    res_1m = client.get("/chains?date_preset=1m")
    assert res_1m.status_code == 200
    html_1m = res_1m.get_data(as_text=True)
    assert "AAPL 2026-08-31 Strategy" in html_1m
    assert "IBM 2026-07-14 Strategy" not in html_1m
    assert "TOTALS (1 chain)" in html_1m
    assert "$500.00" in html_1m

    # 2. 3M preset: both AAPL and IBM are within 90 days
    res_3m = client.get("/chains?date_preset=3m")
    assert res_3m.status_code == 200
    html_3m = res_3m.get_data(as_text=True)
    assert "AAPL 2026-08-31 Strategy" in html_3m
    assert "IBM 2026-07-14 Strategy" in html_3m
    assert "TOTALS (2 chains)" in html_3m
    # AAPL outlay is +$500, IBM outlay is -$200 => net outlay +$300.00
    assert "$300.00" in html_3m

    # 3. YTD and All presets
    res_ytd = client.get("/chains?date_preset=ytd")
    assert res_ytd.status_code == 200
    assert "AAPL 2026-08-31 Strategy" in res_ytd.get_data(as_text=True)
    assert "IBM 2026-07-14 Strategy" in res_ytd.get_data(as_text=True)

    res_all = client.get("/chains?date_preset=all")
    assert res_all.status_code == 200
    assert "AAPL 2026-08-31 Strategy" in res_all.get_data(as_text=True)
    assert "IBM 2026-07-14 Strategy" in res_all.get_data(as_text=True)


def test_chains_footer_totals_by_status(client):
    # Closed chains footer totals
    res_closed = client.get("/chains?status=closed")
    assert res_closed.status_code == 200
    html_closed = res_closed.get_data(as_text=True)
    assert "TOTALS (1 chain)" in html_closed
    # IBM Net Outlay is -$200.00
    assert "-$200.00" in html_closed
    # IBM Realized Profit is +$200.00
    assert "+$200.00" in html_closed

    # Active chains footer totals
    res_active = client.get("/chains?status=active")
    assert res_active.status_code == 200
    html_active = res_active.get_data(as_text=True)
    assert "TOTALS (1 chain)" in html_active
    # AAPL Net Outlay is +$500.00
    assert "$500.00" in html_active


def test_import_log_endpoints(client):
    log_id = client.storage.record_import_log(
        filename="Test_Activity.csv",
        file_sha256="testsha256hash123",
        file_size=1024,
        total_rows=15,
        new_legs=3,
        skipped_duplicates=2,
        raw_content="Date,Symbol,Action\n2026-09-30,HON,BUY_TO_OPEN",
        summary="Imported 3 new legs, skipped 2 duplicates."
    )

    # 1. Check /import page renders the log table
    import_page_res = client.get("/import")
    assert import_page_res.status_code == 200
    html = import_page_res.get_data(as_text=True)
    assert "Test_Activity.csv" in html
    assert "1.0 KB" in html

    # 2. Check GET /api/import-logs/<id> returns json with raw_content
    log_res = client.get(f"/api/import-logs/{log_id}")
    assert log_res.status_code == 200
    data = log_res.get_json()
    assert data["success"] is True
    assert data["log"]["filename"] == "Test_Activity.csv"
    assert "2026-09-30,HON,BUY_TO_OPEN" in data["log"]["raw_content"]

    # 3. Check GET /api/import-logs/<id>/download returns attachment
    dl_res = client.get(f"/api/import-logs/{log_id}/download")
    assert dl_res.status_code == 200
    assert dl_res.mimetype == "text/csv"
    assert "attachment" in dl_res.headers["Content-Disposition"]
    assert "2026-09-30,HON,BUY_TO_OPEN" in dl_res.get_data(as_text=True)


