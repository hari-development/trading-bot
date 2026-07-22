from datetime import datetime
from unittest.mock import MagicMock, patch
from core.engine import TradingEngine
from core.models import Direction, Position
from config.settings import system_config


@patch("core.engine.DashboardServer")
def test_engine_watchlist_options_check(mock_dashboard_server):
    # Setup engine with MagicMock broker
    broker = MagicMock()
    broker.get_ltp.return_value = 15.0
    
    engine = TradingEngine(broker=broker)
    
    # Mock RiskManager's can_trade to always allow trading
    engine.risk_manager.can_trade = MagicMock(return_value=(True, "OK"))
    
    # Empty open positions initially
    engine.open_positions = {}
    
    # We mock _evaluate_entry to see if it gets called
    engine._evaluate_entry = MagicMock()
    
    # Configure watchlist
    system_config.watchlist = ["RELIANCE", "INFY"]
    
    # Scenario 1: No open positions, should evaluate both symbols
    engine._cycle()
    assert engine._evaluate_entry.call_count == 2
    engine._evaluate_entry.assert_any_call("RELIANCE")
    engine._evaluate_entry.assert_any_call("INFY")
    
    # Reset mock
    engine._evaluate_entry.reset_mock()
    
    # Scenario 2: Active option position in RELIANCE (e.g. RELIANCE_26JUL09_1400_CE)
    engine.open_positions = {
        "RELIANCE_26JUL09_1400_CE": Position(
            symbol="RELIANCE_26JUL09_1400_CE",
            direction=Direction.LONG,
            entry_price=15.0,
            quantity=10,
            stop_loss=10.0,
            take_profit=20.0,
            entry_time=datetime.now(),
            strategy_name="ema_supertrend"
        )
    }
    
    engine._cycle()
    # It should only evaluate INFY, not RELIANCE because of the active option position
    assert engine._evaluate_entry.call_count == 1
    engine._evaluate_entry.assert_called_once_with("INFY")


@patch("core.engine.DashboardServer")
def test_engine_position_persistence(mock_dashboard_server, tmp_path):
    temp_file = tmp_path / "open_positions.json"
    with patch("core.engine.POSITIONS_FILE", temp_file):
        broker = MagicMock()
        engine = TradingEngine(broker=broker)
        
        # Initially empty when no position file exists
        assert len(engine.open_positions) == 0
        
        # Create a position
        pos = Position(
            symbol="RELIANCE_CE", direction=Direction.LONG, entry_price=15.0, quantity=10,
            stop_loss=10.0, take_profit=20.0, entry_time=datetime.now(), strategy_name="test",
            underlying_symbol="RELIANCE", underlying_entry_price=2500.0,
            underlying_direction=Direction.LONG, underlying_stop_loss=2480.0, underlying_take_profit=2540.0
        )
        
        engine.open_positions["RELIANCE_CE"] = pos
        engine._save_positions()
        
        # Re-initialize engine
        engine2 = TradingEngine(broker=broker)
        assert "RELIANCE_CE" in engine2.open_positions
        loaded = engine2.open_positions["RELIANCE_CE"]
        assert loaded.symbol == "RELIANCE_CE"
        assert loaded.direction == Direction.LONG
        assert loaded.underlying_symbol == "RELIANCE"
        assert loaded.underlying_entry_price == 2500.0
        assert loaded.underlying_stop_loss == 2480.0
        assert loaded.underlying_take_profit == 2540.0
