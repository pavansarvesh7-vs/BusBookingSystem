import logging
from pathlib import Path

logger = logging.getLogger("bus_booking_logger")
STRATEGY_FILE = Path("active_strategy.txt")
VALID_STRATEGIES = {"iterative", "threaded", "forking"}


class StrategyState:
    def __init__(self):
        self._strategy = self.get_persisted_strategy()

    def get_persisted_strategy(self) -> str:
        if STRATEGY_FILE.exists():
            try:
                val = STRATEGY_FILE.read_text().strip()
                if val in VALID_STRATEGIES:
                    return val
            except Exception as e:
                logger.warning(f"Could not read strategy file: {e}")
        return "iterative"
    @property
    def current_strategy(self) -> str:
        return self.get_persisted_strategy()

    def set_strategy(self, strategy: str):
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Invalid strategy: {strategy}")

        self._strategy = strategy
        try:
            STRATEGY_FILE.write_text(strategy)
        except Exception as e:
            logger.error(f"Failed to persist strategy to disk: {e}")

strategy_state = StrategyState()