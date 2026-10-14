"""Entry point for Kick Points Miner."""

from src.config import Config
from src.miner import PointsMiner


def main() -> None:
    """Application entry point."""
    config = Config.load()
    miner = PointsMiner(config)
    miner.start()


if __name__ == "__main__":
    main()
