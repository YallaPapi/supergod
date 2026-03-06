"""Launcher script for the PolyEdge scheduler.

Run on the 256GB Windows compute box (88.99.142.89).
Connects to remote PostgreSQL on 89.167.99.187.
"""
import asyncio
import logging
import sys
import os

# Ensure polyedge package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

# Configure logging so we can see INFO-level messages
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)

from polyedge.db import settings as db_settings
from polyedge.scheduler import run_forever

if __name__ == "__main__":
    print(f"Starting PolyEdge scheduler on {os.environ.get('COMPUTERNAME', 'unknown')}...", flush=True)
    print(f"DB URL: {db_settings.database_url[:50]}...", flush=True)
    asyncio.run(run_forever())
