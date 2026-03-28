import asyncio
import logging
import os
import sys

from orchestrator.config import load_config
from orchestrator.db import init_db
from orchestrator.sync import run_sync_loop


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("sync_worker")


async def main() -> None:
    config_path = os.environ.get("ORCHESTRATOR_CONFIG", "config.toml")

    logger.info(f"Loading configuration from {config_path}")
    app_config = load_config(config_path)

    logger.info("Initializing database session maker")
    init_db(app_config.database_url)

    logger.info("Starting synchronization background loop...")
    await run_sync_loop(app_config, interval=60)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Sync worker stopped by user.")
