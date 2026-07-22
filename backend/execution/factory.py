"""Selects the broker implementation based on config.system_config.mode.
This is the ONLY place mode-specific broker construction happens."""
from config.settings import system_config, risk_config
from execution.broker_base import Broker


def get_broker() -> Broker:
    if system_config.mode == "LIVE":
        from execution.kite_broker import KiteBroker
        return KiteBroker()
    else:
        from execution.paper_broker import PaperBroker
        return PaperBroker(starting_capital=risk_config.starting_capital)
