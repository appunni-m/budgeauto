import os
import glob
import pandas as pd
import logging
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Define constants
INPUT_DIR = Path("/home/appunni/work/budgeauto/Budget/")
OUTPUT_FILE = Path("/home/appunni/work/budgeauto/All.xlsx")
EXPECTED_COLUMNS = 9
COLUMN_NAMES = [
    "Txn Date", "Expense", "Category", "Short Desc", "Txn Description",
    "Debit", "is Split", "Appu Expense", "Achu Expenses"
]

def find_excel_files(directory: Path) -> list[Path]:
    """Recursively finds all .xlsx files in the given directory."""
    logging.info(f"Searching for .xlsx files in {directory} and its subdirectories...")
    # Use rglob for recursive search
    files = list(directory.rglob("*.xlsx"))
    logging.info(f"Found {len(files)} .xlsx files.")
    return files

def process_excel_file(file_path: Path) -> list[pd.DataFrame]:
    """Processes a single Excel file to extract data from transaction sheets."""
    dataframes = []
    logging.info(f"Processing file: {file_path}")
    try:
        # Use openpyxl engine explicitly if needed, pandas usually detects automatically
        excel_file = pd.ExcelFile(file_path, engine='openpyxl')
        for sheet_name in excel_file.sheet_names:
            logging.info(f"  Checking sheet: {sheet_name}")
            try:
                # Read only the header row to check column count
                header_df = pd.read_excel(excel_file, sheet_name=sheet_name, nrows=0)
                if len(header_df.columns) == EXPECTED_COLUMNS:
                    logging.info(f"    Found transaction sheet (9 columns): {sheet_name}")
                    # Read the actual data, skipping the header row (row 1), header is row 0
                    # header=None ensures pandas doesn't infer headers from data
                    sheet_df = pd.read_excel(excel_file, sheet_name=sheet_name, skiprows=1, header=None)

                    # Assign standard column names only if data exists
                    if not sheet_df.empty:
                        # Ensure the number of columns read matches expected, handle discrepancies
                        if len(sheet_df.columns) == EXPECTED_COLUMNS:
                            sheet_df.columns = COLUMN_NAMES
                            dataframes.append(sheet_df)
                        elif len(sheet_df.columns) > EXPECTED_COLUMNS:
                             logging.warning(f"    Sheet '{sheet_name}' in {file_path} has more than {EXPECTED_COLUMNS} data columns ({len(sheet_df.columns)}). Taking first {EXPECTED_COLUMNS}.")
                             sheet_df = sheet_df.iloc[:, :EXPECTED_COLUMNS]
                             sheet_df.columns = COLUMN_NAMES
                             dataframes.append(sheet_df)
                        else: # len(sheet_df.columns) < EXPECTED_COLUMNS
                             logging.warning(f"    Sheet '{sheet_name}' in {file_path} has fewer than {EXPECTED_COLUMNS} data columns ({len(sheet_df.columns)}). Padding with NA.")
                             # Assign names to existing columns
                             sheet_df.columns = COLUMN_NAMES[:len(sheet_df.columns)]
                             # Add missing columns with NA values
                             for i in range(len(sheet_df.columns), EXPECTED_COLUMNS):
                                 sheet_df[COLUMN_NAMES[i]] = pd.NA
                             # Ensure correct order
                             sheet_df = sheet_df[COLUMN_NAMES]
                             dataframes.append(sheet_df)

                    else:
                         logging.warning(f"    Sheet '{sheet_name}' in {file_path} has 9 header columns but no data rows.")
                else:
                    logging.info(f"    Skipping sheet '{sheet_name}' (expected {EXPECTED_COLUMNS} header columns, found {len(header_df.columns)}).")
            except Exception as e:
                # Log error reading a specific sheet and continue with the next sheet
                logging.error(f"    Error reading sheet '{sheet_name}' in {file_path}: {e}")
                continue
    except Exception as e:
        # Log error opening/processing the file and return empty list for this file
        logging.error(f"Error opening or processing file {file_path}: {e}")
    return dataframes

def combine_and_save(dataframes: list[pd.DataFrame], output_path: Path):
    """Combines list of DataFrames and saves to an Excel file."""
    if not dataframes:
        logging.warning("No transaction data found to combine. Creating an empty output file with standard headers.")
        # Create an empty DataFrame with the correct columns
        combined_df = pd.DataFrame(columns=COLUMN_NAMES)
    else:
        logging.info(f"Combining data from {len(dataframes)} transaction sheets...")
        try:
            # Concatenate all collected dataframes
            combined_df = pd.concat(dataframes, ignore_index=True)
            # Ensure final DataFrame has exactly the standard columns in the correct order,
            # This handles cases where concat might alter order or if any df had issues
            combined_df = combined_df[COLUMN_NAMES]
            logging.info(f"Combined DataFrame shape: {combined_df.shape}")
        except Exception as e:
            logging.error(f"Error during DataFrame concatenation: {e}")
            # Fallback to an empty DataFrame with headers if concatenation fails
            combined_df = pd.DataFrame(columns=COLUMN_NAMES)

    logging.info(f"Saving combined data to {output_path}...")
    try:
        # Save the combined data, overwriting if exists, without the index
        combined_df.to_excel(output_path, index=False, engine='openpyxl')
        logging.info(f"Successfully saved combined data to {output_path}.")
    except Exception as e:
        logging.error(f"Error saving data to {output_path}: {e}")

def main():
    """Main function to find, process, combine, and save transaction data."""
    excel_files = find_excel_files(INPUT_DIR)
    if not excel_files:
        logging.warning(f"No .xlsx files found in {INPUT_DIR}. Creating empty output file.")
        combine_and_save([], OUTPUT_FILE) # Ensure empty file is created
        return

    all_dataframes = []
    processed_files_count = 0
    for file_path in excel_files:
        # Skip temporary Excel files (often start with ~$)
        if file_path.name.startswith("~$"):
            logging.info(f"Skipping temporary file: {file_path}")
            continue
        processed_files_count += 1
        all_dataframes.extend(process_excel_file(file_path))

    logging.info(f"Finished processing {processed_files_count} potential Excel files.")
    combine_and_save(all_dataframes, OUTPUT_FILE)

if __name__ == "__main__":
    # Ensures the main logic runs only when the script is executed directly
    main()