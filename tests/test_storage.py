import os
import pytest
from options_chain.models import OptionsChain, OptionLeg, OptionType, OptionSide
from options_chain.storage import ChainStorage


def test_sqlite_persistence(tmp_path):
    db_file = str(tmp_path / "test_options.db")
    storage = ChainStorage(db_path=db_file)

    chain = OptionsChain(
        symbol="QQQ",
        name="QQQ Debit Spread",
        underlying_entry_price=450.0,
        shares=100,
        share_entry_price=440.0
    )
    chain.add_leg(OptionLeg(strike=450.0, option_type=OptionType.CALL, side=OptionSide.BUY, quantity=2, entry_price=10.0))
    chain.add_leg(OptionLeg(strike=460.0, option_type=OptionType.CALL, side=OptionSide.SELL, quantity=2, entry_price=4.0))

    # Save chain
    chain_id = storage.save_chain(chain)
    assert chain_id is not None

    # Retrieve chain
    reloaded = storage.get_chain(chain_id)
    assert reloaded is not None
    assert reloaded.symbol == "QQQ"
    assert reloaded.name == "QQQ Debit Spread"
    assert reloaded.shares == 100
    assert len(reloaded.legs) == 2
    assert reloaded.legs[0].strike == 450.0
    assert reloaded.legs[1].strike == 460.0

    # List chains
    chains_list = storage.list_chains()
    assert len(chains_list) == 1
    assert chains_list[0]['symbol'] == "QQQ"

    # Delete chain
    deleted = storage.delete_chain(chain_id)
    assert deleted is True
    assert storage.get_chain(chain_id) is None


def test_csv_export_and_import(tmp_path):
    csv_file = str(tmp_path / "test_chain.csv")

    original_chain = OptionsChain(symbol="AMD", name="AMD Straddle", shares=50, share_entry_price=160.0)
    original_chain.add_leg(OptionLeg(strike=160.0, option_type=OptionType.CALL, side=OptionSide.BUY, quantity=1, entry_price=6.0))
    original_chain.add_leg(OptionLeg(strike=160.0, option_type=OptionType.PUT, side=OptionSide.BUY, quantity=1, entry_price=5.5))

    # Export to CSV
    ChainStorage.export_to_csv(original_chain, csv_file)
    assert os.path.exists(csv_file)

    # Import back from CSV
    imported_chain = ChainStorage.import_from_csv(csv_file)
    assert imported_chain.symbol == "AMD"
    assert imported_chain.name == "AMD Straddle"
    assert imported_chain.shares == 50
    assert imported_chain.share_entry_price == 160.0
    assert len(imported_chain.legs) == 2
    assert imported_chain.legs[0].option_type == OptionType.CALL
    assert imported_chain.legs[1].option_type == OptionType.PUT
    assert imported_chain.net_initial_cost == (6.0 * 100 + 5.5 * 100 + 50 * 160.0)
