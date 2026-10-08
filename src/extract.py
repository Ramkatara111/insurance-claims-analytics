import pandas as pd
from pathlib import Path
from src.config import RAW_DATA_PATH, logger

def extract_raw_claims(file_path: Path = RAW_DATA_PATH) -> pd.DataFrame:
    """
    Extracts raw insurance claims data from the verified CSV source.
    Performs initial file existence and schema integrity assertions.
    """
    path_obj = Path(file_path)
    if not path_obj.exists():
        raise FileNotFoundError(f"Raw data file not found at: {path_obj.resolve()}")
    
    logger.info(f"Loading raw dataset from {path_obj}...")
    
    # Read CSV while keeping 'None' intact for authorities_contacted
    # We do NOT want 'None' to be treated as NaN because 'None' contacted is a valid category
    df_raw = pd.read_csv(
        path_obj,
        keep_default_na=False,
        na_values=["", "#N/A", "#N/A N/A", "#NA", "-1.#IND", "-1.#QNAN", "-NaN", "-nan", "1.#IND", "1.#QNAN", "<NA>", "N/A", "NA", "NULL", "null", "NaN", "nan"]
    )
    
    row_count, col_count = df_raw.shape
    logger.info(f"Extraction successful: Extracted {row_count:,} rows and {col_count} columns.")
    
    # Basic data integrity checks
    if row_count != 1000:
        logger.warning(f"Expected 1,000 rows in insurance claims benchmark dataset, but found {row_count}.")
        
    return df_raw

if __name__ == "__main__":
    df = extract_raw_claims()
    print("Extracted shape:", df.shape)
    print("Columns:", list(df.columns[:5]), "...")
