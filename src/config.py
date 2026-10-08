import os
import sys
import logging
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from sqlalchemy import create_engine

# Load environment variables
load_dotenv()

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_DATA_PATH = Path(os.getenv("RAW_DATA_PATH", str(DATA_DIR / "raw" / "insurance_claims.csv")))
PROCESSED_DATA_PATH = Path(os.getenv("PROCESSED_DATA_PATH", str(DATA_DIR / "processed")))
SQL_DIR = BASE_DIR / "sql"

# Database Configuration
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "insurance_dw")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")

# Schemas
DB_SCHEMA_RAW = os.getenv("DB_SCHEMA_RAW", "staging")
DB_SCHEMA_CORE = os.getenv("DB_SCHEMA_CORE", "core")
DB_SCHEMA_MARTS = os.getenv("DB_SCHEMA_MARTS", "marts")

# Logger configuration
def setup_logging(level=logging.INFO):
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger("InsuranceClaimsPipeline")

logger = setup_logging()

def get_engine(database: Optional[str] = None):
    """Create and return a new SQLAlchemy engine.

    database: optional database name; defaults to DB_NAME. No module-level engine is kept,
    so callers own (and may dispose of) the engine they get.
    """
    db_name = database or DB_NAME
    url = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{db_name}"
    return create_engine(url, pool_pre_ping=True)
