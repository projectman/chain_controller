import pytest
from options_chain.margin_calculator import (
    MarginCalculator, parse_money, parse_contract, parse_file_date, compute_metrics, compute_totals,
)
from options_chain.storage import ChainStorage
from web_app import create_app

HEADER = (
    "Fidelity Investments - Option Summary X73981319\r\rQuote data as of 10/06/2026.\r\r"
    "Symbol,Description,Strategy,Expiration & Strike,Quantity,Bid,Ask,Cost basis*,Market value,"
    "Avg. cost,$ Total Gain/Loss,% Total Gain/Loss,Last,Change,% Change,Margin requirements,\r"
)
ROWS = (
    "AA,ALCOA,Naked Put,AA OCT 16 2026 $40 PUT,-8,$0.43,$0.49,-$474.69,-$392.00,-$0.59,82.69,+17.42%,$0.46,-$0.04,-8.00%,$6306.00,\r"
    "ADBE,ADOBE,Covered Call,ADBE NOV 20 2026 $250 CALL,-1,$8.35,$9.20,-$2144.29,-$920.00,-$21.44,1224.29,+57.10%,$8.90,-$0.35,-3.78%,--,\r"
    "BAC,BANK OF AMERICA,Naked Put,BAC DEC 18 2026 $52.50 PUT,-4,$1.65,$1.74,-$800.00,-$700.00,-$1.71,-19.99,-1.95%,$1.70,-$0.01,-0.58%,\"$5,000.00\",\r"
    "BAC,BANK OF AMERICA,Naked Put,BAC DEC 18 2026 $52.50 PUT,-2,$1.65,$1.74,-$224.01,-$344.00,-$1.71,-19.99,-1.95%,$1.70,-$0.01,-0.58%,$3226.00,\r"
)


@pytest.fixture
def sources(tmp_path):
    d = tmp_path / "sources"
    d.mkdir()
    (d / "Fidelity_Investment_Option_Summary_X73981319_Oct-06-2026.csv").write_text(HEADER + ROWS, newline="")
    return d


def test_parsers():
    assert parse_money("-$1,234.50") == -1234.50
    assert parse_money("--") == 0.0
    assert parse_contract("AA OCT 16 2026 $40 PUT") == {"symbol": "AA", "expiration": "2026-10-16", "strike": 40.0}
    assert parse_contract("BAC DEC 18 2026 $52.50 PUT")["strike"] == 52.5
    assert parse_contract("ADBE NOV 20 2026 $250 CALL") is None
    assert parse_file_date("x/Fidelity_Investment_Option_Summary_X73981319_Oct-06-2026.csv") == "2026-10-06"


def test_metrics_and_totals():
    m = compute_metrics(-8, 40, -474.69, 6306)
    assert m["max_risk"] == pytest.approx(31525.31)
    assert m["margin_ratio"] == pytest.approx(6306 / 31525.31)
    t = compute_totals([{"max_risk": 100.0, "margin": 20.0}, {"max_risk": 300.0, "margin": 40.0}])
    assert t["total_ratio"] == pytest.approx(0.15)


def test_extract_only_naked_puts_and_merge(sources):
    path = next(sources.glob("Fidelity_*"))
    pos = MarginCalculator.extract_naked_puts(str(path))
    assert [p["symbol"] for p in pos] == ["AA", "BAC"]
    bac = pos[1]
    assert bac["part_count"] == 2
    assert bac["quantity"] == -6
    assert bac["cost_basis"] == pytest.approx(-1024.01)
    assert bac["margin"] == pytest.approx(8226.0)
    assert bac["max_risk"] == pytest.approx(6 * 52.5 * 100 - 1024.01)


def test_import_dedup_and_new_snapshot(sources, tmp_path):
    storage = ChainStorage(db_path=str(tmp_path / "m.db"))
    r1 = MarginCalculator.import_sources_folder(str(sources), storage)
    assert (r1["new_positions"], r1["skipped_duplicates"]) == (2, 0)
    r2 = MarginCalculator.import_sources_folder(str(sources), storage)
    assert (r2["new_positions"], r2["skipped_duplicates"]) == (0, 2)

    (sources / "Fidelity_Investment_Option_Summary_X73981319_Oct-07-2026.csv").write_text(HEADER + ROWS, newline="")
    r3 = MarginCalculator.import_sources_folder(str(sources), storage)
    assert r3["new_positions"] == 2
    assert storage.list_margin_dates() == ["2026-10-07", "2026-10-06"]
    assert len(storage.list_margin_positions()) == 2


def test_page_and_import_endpoint(sources, tmp_path):
    db = str(tmp_path / "w.db")
    app = create_app(db_path=db, sources_dir=str(sources))
    client = app.test_client()
    assert "No naked put positions" in client.get("/margin-calculator").get_data(as_text=True)

    res = client.post("/api/run-import").get_json()
    assert res["success"] is True
    assert res["margin_new"] == 2

    html = client.get("/margin-calculator").get_data(as_text=True)
    assert "Margin Calculator" in html
    assert "TOTALS (2 positions)" in html
    assert "AA" in html and "BAC" in html
    filtered = client.get("/margin-calculator?q=AA").get_data(as_text=True)
    assert "TOTALS (1 position)" in filtered
