import pytest
from trading_agent.config import load_config
from trading_agent.structure import Quote


@pytest.fixture
def cfg():
    return load_config()


def q(kind, strike, bid, ask, delta, oi=1000):
    return Quote(strike, kind, bid, ask, delta, oi)


@pytest.fixture
def chain():
    # price 100, 50dma 105. Short put 95 (delta .15), short call 110 (delta .15).
    return [
        q("P", 95, 1.45, 1.55, -0.15), q("P", 90, 0.45, 0.55, -0.07),
        q("P", 92.5, 0.70, 0.80, -0.10),
        q("C", 110, 0.68, 0.72, 0.15), q("C", 115, 0.05, 0.15, 0.04),
        q("C", 112.5, 0.25, 0.35, 0.08),
    ]
