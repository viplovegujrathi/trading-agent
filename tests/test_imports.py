import importlib
import pytest


@pytest.mark.parametrize("m", ["cli", "prices_yahoo", "universe", "indicators", "broker"])
def test_module_imports(m):
    importlib.import_module(f"trading_agent.{m}")
