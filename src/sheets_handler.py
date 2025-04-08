# src/sheets_handler.py

import gspread
import gspread.utils # Ensure utils is imported
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
import pandas as pd
import datetime
import logging
import os
from typing import List, Optional
from models import Category # Added import
# Assuming Transaction model is defined in pdf_parser
try:
    from pdf_parser import Transaction
except ImportError:
    # Define a dummy class if pdf_parser is not available during standalone testing/linting
    class Transaction:
        pass

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Define scopes required for Google Sheets and Drive APIs
SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]

def parse_date_flexible(date_input):
    """
    Parses a date string from multiple formats ('%d/%m/%Y', '%d-%b-%y')
    and returns it in 'DD/MM/YYYY' format.
    Returns an empty string if parsing fails or input is invalid.
    """
    if not isinstance(date_input, str) or not date_input:
        # logging.debug(f"Invalid date input type or empty: {date_input}") # Optional debug log
        return '' # Handle None, empty strings, or non-string types

    formats_to_try = ['%d/%m/%Y', '%d-%b-%y', '%d/%m/%Y %H:%M:%S'] # Added datetime format
    parsed_date = None

    for fmt in formats_to_try:
        try:
            # Attempt to parse the date string with the current format
            parsed_date = datetime.datetime.strptime(date_input, fmt)
            # If parsing is successful, break the loop
            break
        except ValueError:
            # If parsing fails, continue to the next format
            continue

    if parsed_date:
        # If a date was successfully parsed, format it to DD/MM/YYYY
        return parsed_date.strftime('%d/%m/%Y')
    else:
        # If no format matched, log a warning and return an empty string
        logging.warning(f"Could not parse date string '{date_input}' using formats {formats_to_try}. Writing empty string.")
        return ''

def _get_drive_service(creds):
    """Builds and returns a Google Drive API service client."""
    try:
        service = build('drive', 'v3', credentials=creds)
        return service
    except HttpError as error:
        logging.error(f"An error occurred building Drive service: {error}")
        raise
    except Exception as e:
        logging.error(f"An unexpected error occurred building Drive service: {e}")
        raise

def _find_file_in_folder(service, folder_id, file_name):
    """Finds a file by name within a specific Google Drive folder."""
    try:
        query = f"name='{file_name}' and '{folder_id}' in parents and trashed=false"
        response = service.files().list(q=query, spaces='drive', fields='files(id, name)').execute()
        files = response.get('files', [])
        if files:
            logging.info(f"Found existing file '{file_name}' with ID: {files[0]['id']}")
            return files[0]['id']
        else:
            logging.info(f"File '{file_name}' not found in folder ID '{folder_id}'.")
            return None
    except HttpError as error:
        logging.error(f"An error occurred searching for file '{file_name}': {error}")
        # Don't raise here, allow creation flow
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred searching for file '{file_name}': {e}")
        return None


# Removed _copy_template function as we are creating sheets programmatically
def _create_blank_spreadsheet(gc, sheet_name):
    """Creates a new blank Google Spreadsheet."""
    try:
        logging.info(f"Creating a new blank spreadsheet named '{sheet_name}'")
        spreadsheet = gc.create(sheet_name)
        # Share with the user or keep private based on service account?
        # For now, it's owned by the service account. User needs to add sharing if needed.
        logging.info(f"Blank spreadsheet created with ID: {spreadsheet.id}")
        return spreadsheet
    except gspread.exceptions.APIError as error:
        logging.error(f"An error occurred creating blank sheet '{sheet_name}': {error}")
        raise
    except Exception as e:
        logging.error(f"An unexpected error occurred creating blank sheet '{sheet_name}': {e}")
        raise


def _find_or_create_folder(service, parent_folder_id, folder_name):
    """Finds a folder by name within a parent folder, or creates it if not found."""
    folder_id = None
    try:
        # Search for the folder
        query = (f"mimeType='application/vnd.google-apps.folder' and "
                 f"name='{folder_name}' and "
                 f"'{parent_folder_id}' in parents and "
                 f"trashed=false")
        response = service.files().list(q=query,
                                        spaces='drive',
                                        fields='files(id, name)').execute()
        folders = response.get('files', [])

        if folders:
            folder_id = folders[0]['id']
            logging.info(f"Found existing year folder '{folder_name}' with ID: {folder_id}")
        else:
            # Create the folder if not found
            logging.info(f"Year folder '{folder_name}' not found in parent '{parent_folder_id}'. Creating...")
            file_metadata = {
                'name': folder_name,
                'mimeType': 'application/vnd.google-apps.folder',
                'parents': [parent_folder_id]
            }
            created_folder = service.files().create(body=file_metadata, fields='id').execute()
            folder_id = created_folder.get('id')
            if folder_id:
                logging.info(f"Created year folder '{folder_name}' with ID: {folder_id}")
            else:
                logging.error(f"Failed to create year folder '{folder_name}' - no ID returned.")
                # folder_id remains None

    except HttpError as error:
        logging.error(f"An error occurred finding/creating folder '{folder_name}' in '{parent_folder_id}': {error}")
        # folder_id remains None
    except Exception as e:
        logging.error(f"An unexpected error occurred finding/creating folder '{folder_name}': {e}")
        # folder_id remains None

    return folder_id

# Removed _add_required_sheets function, sheet management handled in main function
def update_google_sheet(all_transactions: List[Transaction], config: dict, credentials) -> Optional[str]:
    """
    Creates or updates a Google Sheet budget report programmatically based on transaction data,
    using provided OAuth 2.0 credentials, and ensuring it's placed within the correct
    year-specific subfolder in Google Drive.

    Args:
        all_transactions (List[Transaction]): List of all categorized Pydantic Transaction objects
                                              (including non-expenses for account discovery).
        config (dict): Configuration dictionary containing keys like:
                       'GOOGLE_DRIVE_BUDGET_FOLDER_ID'.
        credentials: Authorized Google OAuth 2.0 credentials object
                     (e.g., from google-auth-oauthlib flow).

    Returns:
        Optional[str]: The URL of the created/updated Google Sheet, or None if an error occurred.

    Raises:
        ValueError: If required configuration keys are missing.
        Exception: For errors during API interaction or processing.
    """
    logging.info("Starting Google Sheet update process...")

    main_budget_folder_id = config.get('GOOGLE_DRIVE_BUDGET_FOLDER_ID')
    # template_id = config.get('GOOGLE_SHEETS_TEMPLATE_ID') # Removed template dependency

    if not main_budget_folder_id:
        raise ValueError("Missing 'GOOGLE_DRIVE_BUDGET_FOLDER_ID' in configuration.")

    # --- Authentication ---
    try:
        # Use the passed OAuth credentials
        gc = gspread.authorize(credentials)
        # Build Drive and Sheets API services
        drive_service = _get_drive_service(credentials)
        sheets_service = build('sheets', 'v4', credentials=credentials)
        logging.info("Google Drive and Sheets API authentication successful.")
    except FileNotFoundError as e:
        logging.error(f"Credentials file error: {e}")
        raise # Propagate critical error
    except Exception as e:
        logging.error(f"Failed to authenticate with Google APIs: {e}")
        return None # Or raise depending on desired main script behavior

    # --- Determine Target Year, Month, and Sheet Name ---
    today = datetime.date.today()
    first_day_of_current_month = today.replace(day=1)
    last_day_of_previous_month = first_day_of_current_month - datetime.timedelta(days=1)
    year = last_day_of_previous_month.year
    year_str = str(year) # Folder name will be the year string
    month_name = last_day_of_previous_month.strftime("%B")
    month_num_str = last_day_of_previous_month.strftime("%m") # Get month number (01, 02, ...)
    # Use the required naming convention: Accounts-YYYY-MonthName
    sheet_name = f"Accounts-{year_str}-{month_name}"
    logging.info(f"Target sheet name: '{sheet_name}' for year {year_str}, month {month_name}")

    # --- Find or Create Year Subfolder ---
    logging.info(f"Checking for year subfolder '{year_str}' in main budget folder '{main_budget_folder_id}'...")
    year_folder_id = _find_or_create_folder(drive_service, main_budget_folder_id, year_str)

    if not year_folder_id:
        logging.error(f"Could not find or create the year subfolder '{year_str}'. Cannot proceed.")
        return None # Critical failure if we can't get the target folder

    logging.info(f"Using year subfolder ID: {year_folder_id}")

    # --- Find Existing Sheet or Copy Template/Create New within Year Folder ---
    spreadsheet = None
    sheet_id = _find_file_in_folder(drive_service, year_folder_id, sheet_name)

    if sheet_id:
        try:
            spreadsheet = gc.open_by_key(sheet_id)
            logging.info(f"Opened existing spreadsheet '{sheet_name}' (ID: {sheet_id}) in folder '{year_folder_id}'")
        except gspread.exceptions.SpreadsheetNotFound:
            logging.warning(f"Found sheet ID {sheet_id} via Drive API but gspread couldn't open it. Attempting copy/create.")
            sheet_id = None # Reset ID so we try to create below
        except gspread.exceptions.APIError as e:
            logging.error(f"API error opening existing sheet ID {sheet_id}: {e}")
            return None # Cannot proceed
        except Exception as e:
            logging.error(f"Unexpected error opening existing sheet ID {sheet_id}: {e}")
            return None # Cannot proceed

    if not spreadsheet:
        # If sheet wasn't found, create a new blank one
        logging.info(f"Spreadsheet '{sheet_name}' not found. Creating a new blank spreadsheet.")
        try:
            # Create the blank sheet (initially in root or default location)
            spreadsheet = _create_blank_spreadsheet(gc, sheet_name)
            # Move the newly created blank sheet to the target YEAR folder
            # Ensure 'root' is removed if it was created there by default
            file_info = drive_service.files().get(fileId=spreadsheet.id, fields='parents').execute()
            previous_parents = ",".join(file_info.get('parents'))
            drive_service.files().update(fileId=spreadsheet.id,
                                         addParents=year_folder_id,
                                         removeParents=previous_parents, # Remove from previous location(s)
                                         fields='id, parents').execute()
            logging.info(f"Moved new blank sheet '{sheet_name}' (ID: {spreadsheet.id}) to year folder ID '{year_folder_id}'")
        except Exception as e:
            logging.error(f"Failed to create or move blank spreadsheet '{sheet_name}': {e}")
            # If creation failed, spreadsheet is None, so return None
            # If move failed, we might still have the sheet but not in the right place. Return None for now.
            if spreadsheet: # Cleanup if sheet was created but move failed
                try:
                    logging.info(f"Attempting to delete partially created sheet {spreadsheet.id}")
                    drive_service.files().delete(fileId=spreadsheet.id).execute()
                except Exception as delete_e:
                    logging.error(f"Failed to delete partially created sheet {spreadsheet.id}: {delete_e}")
            return None
    if not spreadsheet:
         logging.error("Failed to obtain a spreadsheet instance (existing, copied, or new).")
         return None

    # --- Define Target Sheet Structure ---
    # Use all_transactions to get the full list of accounts
    all_account_names = sorted(list(set(tx.source_account or 'Unknown' for tx in all_transactions if tx.source_account)))
    logging.info(f"Identified account sheets needed from transactions: {all_account_names}")

    # Define the standard sheets and the dynamically generated ones
    standard_sheets = ['Cash', 'Achu']
    required_sheets = ['Final Recon', 'Reporting']
    # Ensure dynamic names are valid sheet names (gspread might handle some cases)
    account_sheets = [name for name in all_account_names if name] # Filter out empty names

    # Combine standard, required, and account sheets, ensuring required ones are present and avoiding duplicates
    base_sheets = list(dict.fromkeys(standard_sheets + required_sheets)) # Preserves order, removes duplicates
    target_sheet_names = base_sheets + [name for name in account_sheets if name not in base_sheets] # Add account sheets not already present

    logging.info(f"Target sheets for workbook '{sheet_name}': {target_sheet_names}")

    # Filter transactions for populating sheets (exclude investments/insurance - assuming this is done before calling)
    # For now, assume all_transactions contains the data to be populated.
    # If filtering is needed here, it should be added based on category or other flags.
    transactions_to_populate = all_transactions # Use all for now, adjust if filtering needed here

    # Initialize unique_categories here to ensure it's always defined
    unique_categories = []

    if not transactions_to_populate:
        logging.warning("No transactions provided to populate sheets.")
        # We might still want to create the structure
        # return spreadsheet.url # Let it continue to create structure
    try:
        # Convert Pydantic objects to dicts for DataFrame creation
        transaction_dicts = [tx.model_dump() for tx in transactions_to_populate]
        df_transactions = pd.DataFrame(transaction_dicts)
        # Ensure required columns exist based on Pydantic model fields and target sheet structure
        # Target columns: Txn Date, is Expense, Category, Short desc, Description, Cost, Is Split
        # Map from Transaction model: date, ?, category, short_desc, description, amount, ?
        # Need to add 'is Expense' and 'Is Split' logic if not present in Transaction model
        required_cols = ['date', 'category', 'short_description', 'description', 'amount', 'source_account', 'transaction_type'] # Base from Transaction (Added transaction_type)
        for col in required_cols:
            if col not in df_transactions.columns:
                # Amount and transaction_type are critical, others can be None
                if col == 'amount':
                    logging.error("Critical: 'amount' column missing in DataFrame from Transaction model.")
                    # Decide error handling: return None or raise? For now, log and add None.
                    df_transactions[col] = None
                elif col == 'transaction_type':
                     logging.error("Critical: 'transaction_type' column missing in DataFrame from Transaction model. Cannot determine amount sign.")
                     # Decide error handling: return None or raise? For now, log and add None.
                     df_transactions[col] = None # Or 'unknown'?
                else:
                    logging.warning(f"Column '{col}' missing in DataFrame from Transaction model. Adding with None.")
                    df_transactions[col] = None

        # Ensure 'amount' is numeric before sign change
        df_transactions['amount'] = pd.to_numeric(df_transactions['amount'], errors='coerce')

        # Apply sign based on transaction_type (Debit = negative, Credit = positive)
        # Use abs() to ensure we handle cases where amount might already have a sign incorrectly
        df_transactions['amount'] = df_transactions.apply(
            lambda row: -abs(row['amount']) if pd.notna(row['amount']) and row['transaction_type'] == 'credit' # Credit is negative
            else abs(row['amount']) if pd.notna(row['amount']) and row['transaction_type'] == 'debit' # Debit is positive
            else row['amount'], # Keep as is (e.g., NaN or if type is unknown/missing)
            axis=1
        )
        logging.info("Applied amount sign based on transaction_type (debit: negative, credit: positive).")


        # Add placeholder columns if they don't exist - these need actual logic later
        # TODO: Review if 'is_expense' logic should be derived from transaction_type
        if 'is_expense' not in df_transactions.columns:
             logging.warning("Column 'is_expense' not in Transaction model. Adding placeholder value 1.")
             df_transactions['is_expense'] = 1 # Placeholder - needs logic based on transaction_type?
        if 'is_split' not in df_transactions.columns:
             logging.warning("Column 'is_split' not in Transaction model. Adding placeholder value 0.")
             df_transactions['is_split'] = 0 # Placeholder

        # Select and order columns for writing to Account/Cash/Achu sheets
        # Headers: Txn Date, is Expense, Category, Short desc, Description, Cost, Is Split
        output_columns_data = ['date', 'is_expense', 'category', 'short_description', 'description', 'amount', 'is_split'] # Updated short_desc
        # Keep source_account for grouping
        df_to_write = df_transactions[['source_account'] + output_columns_data].copy()

        # --- Amount sign is now handled earlier based on transaction_type ---
        # Ensure 'is_expense' is numeric if needed later (it's currently a placeholder)
        df_to_write['is_expense'] = pd.to_numeric(df_to_write['is_expense'], errors='coerce')


        # Convert date column to string in desired format if needed (e.g., 'YYYY-MM-DD')
        # Assuming 'transaction_date' is already a string or datetime object
        # Convert date column to string 'YYYY-MM-DD' if it's not already
        # Convert date column to string 'YYYY-MM-DD'
        # Apply flexible date parsing and format to DD/MM/YYYY
        if 'date' in df_to_write.columns:
            logging.info("Applying flexible date parsing to 'date' column...")
            df_to_write['date'] = df_to_write['date'].apply(parse_date_flexible)
            logging.info("Finished applying flexible date parsing.")
        else:
             logging.warning("Column 'date' not found in DataFrame. Cannot apply date parsing.")
             df_to_write['date'] = '' # Ensure column exists if it was missing

        # Rename columns to match sheet headers for clarity (optional, but good practice)
        # df_to_write.rename(columns={'date': 'Txn Date', 'amount': 'Cost'}, inplace=True) # Example
        # Convert Category enum to string value for JSON serialization
        if 'category' in df_to_write.columns:
             df_to_write['category'] = df_to_write['category'].apply(lambda x: x.value if isinstance(x, Category) else x)

        # Replace NaN/None with empty strings for gspread compatibility in other columns
        df_to_write = df_to_write.fillna('') # Avoid inplace=True

        grouped_data = df_to_write.groupby('source_account')

        # Extract unique categories for data validation AFTER df_to_write is prepared
        if 'category' in df_to_write.columns:
            unique_categories = df_to_write['category'].unique().tolist()
            # Remove potential None or empty strings early
            unique_categories = [cat for cat in unique_categories if cat and pd.notna(cat)]
            # Keep it sorted for consistency in logs/validation (optional)
            unique_categories.sort()
        # else: unique_categories remains [] as initialized earlier

    except Exception as e:
        logging.error(f"Error processing transaction data with pandas: {e}")
        return None # Cannot proceed without data

    # --- Manage Sheets (Create/Delete) ---
    logging.info(f"Synchronizing sheets in workbook '{spreadsheet.title}'...")
    try:
        existing_sheets = {ws.title: ws for ws in spreadsheet.worksheets()}
        existing_sheet_names = set(existing_sheets.keys())
        target_sheet_names_set = set(target_sheet_names)

        # Delete sheets that exist but are not in the target list
        sheets_to_delete = existing_sheet_names - target_sheet_names_set
        # Don't delete the very last sheet if it's the only one left and needs deletion
        can_delete = len(existing_sheet_names) > 1 or not sheets_to_delete
        if sheets_to_delete and can_delete:
            for sheet_name in sheets_to_delete:
                 # Avoid deleting the default 'Sheet1' if it's the only one left and not targeted
                 if len(existing_sheet_names) == 1 and sheet_name == 'Sheet1':
                     logging.warning("Skipping deletion of 'Sheet1' as it's the only sheet left.")
                     continue
                 try:
                     logging.info(f"Deleting existing sheet not in target list: '{sheet_name}'")
                     spreadsheet.del_worksheet(existing_sheets[sheet_name])
                     existing_sheet_names.remove(sheet_name) # Update current state
                 except Exception as e:
                     logging.error(f"Error deleting sheet '{sheet_name}': {e}")

        # Add sheets that are in the target list but don't exist
        sheets_to_add = target_sheet_names_set - existing_sheet_names
        for sheet_name in target_sheet_names: # Iterate in desired order
            if sheet_name in sheets_to_add:
                try:
                    logging.info(f"Adding missing target sheet: '{sheet_name}'")
                    # Handle case where 'Sheet1' exists but needs renaming
                    if 'Sheet1' in existing_sheet_names and len(existing_sheet_names) == 1:
                         logging.info(f"Renaming existing 'Sheet1' to '{sheet_name}'")
                         ws = existing_sheets['Sheet1']
                         ws.update_title(sheet_name)
                         existing_sheets[sheet_name] = ws # Update dict
                         del existing_sheets['Sheet1']
                         existing_sheet_names.remove('Sheet1')
                         existing_sheet_names.add(sheet_name)
                    else:
                        spreadsheet.add_worksheet(title=sheet_name, rows="100", cols="20") # Adjust size later
                        existing_sheet_names.add(sheet_name) # Update current state
                except Exception as e:
                    logging.error(f"Error adding sheet '{sheet_name}': {e}")
                    # Decide if we should continue or raise

        # Reorder sheets to match target_sheet_names order (best effort)
        try:
             # Get current worksheets after adds/deletes
             current_worksheets = spreadsheet.worksheets()
             current_titles_ordered = [ws.title for ws in current_worksheets]
             target_order_map = {name: i for i, name in enumerate(target_sheet_names)}

             # Create list of worksheet objects in the desired order
             ordered_worksheets = sorted(current_worksheets, key=lambda ws: target_order_map.get(ws.title, 999))

             # Prepare reordering requests (requires batch update)
             requests = []
             for i, ws in enumerate(ordered_worksheets):
                 requests.append({
                     "updateSheetProperties": {
                         "properties": {
                             "sheetId": ws.id,
                             "index": i
                         },
                         "fields": "index"
                     }
                 })
             if requests:
                 logging.info("Reordering sheets...")
                 spreadsheet.batch_update({"requests": requests})

        except Exception as e:
            logging.warning(f"Could not reorder sheets: {e}")


    except gspread.exceptions.APIError as e:
        logging.error(f"API error managing sheets for {spreadsheet.id}: {e}")
        return None # Cannot proceed if we can't manage sheets
    except Exception as e:
        logging.error(f"Unexpected error managing sheets for {spreadsheet.id}: {e}")
        return None

    # --- Get Fresh Sheet IDs AFTER Synchronization ---
    sheet_id_map = {}
    try:
        logging.info(f"Fetching updated sheet metadata for spreadsheet ID: {spreadsheet.id}")
        sheet_metadata = sheets_service.spreadsheets().get(spreadsheetId=spreadsheet.id, fields='sheets(properties(sheetId,title))').execute()
        for sheet_prop in sheet_metadata.get('sheets', []):
            properties = sheet_prop.get('properties', {})
            title = properties.get('title')
            sheet_id = properties.get('sheetId')
            if title and sheet_id is not None:
                sheet_id_map[title] = sheet_id
        logging.info(f"Successfully fetched updated sheet IDs: {list(sheet_id_map.keys())}")
    except HttpError as error:
        logging.error(f"API error fetching updated sheet metadata: {error}")
        return None # Cannot proceed without sheet IDs
    except Exception as e:
        logging.error(f"Unexpected error fetching updated sheet metadata: {e}")
        return None

    # --- Define Headers ---
    headers = {
        "Account": ["Txn Date", "is Expense", "Category", "Short Description", "Description", "Cost", "Is Split", "Appu", "Achu"], # Updated header
        "Cash": ["Txn Date", "is Expense", "Category", "Short Description", "Description", "Cost", "Is Split", "Appu", "Achu"], # Updated header
        "Achu": ["Txn Date", "is Expense", "Category", "Short Description", "Description", "Cost", "Is Split", "Appu", "Achu"], # Updated header
        "Final Recon": ["Source", "Category", "Appu Expense", "Achu Expense", "Description", "Actual Amount", "", "Category Heading", "Appu", "Achu", "Actual amount"] # No change needed here
    }

    # --- Populate Headers ---
    logging.info("Populating headers for all target sheets...")
    for sheet_name in target_sheet_names:
        try:
            worksheet = spreadsheet.worksheet(sheet_name)
            sheet_type = "Account" # Default
            if sheet_name == "Cash": sheet_type = "Cash"
            elif sheet_name == "Achu": sheet_type = "Achu"
            elif sheet_name == "Final Recon": sheet_type = "Final Recon"

            current_headers = headers.get(sheet_type)
            if current_headers:
                logging.debug(f"Setting headers for '{sheet_name}' ({sheet_type}): {current_headers}")
                worksheet.update('A1', [current_headers], value_input_option='USER_ENTERED')
                # Optional: Freeze header row
                worksheet.freeze(rows=1)
            else:
                logging.warning(f"No defined headers for sheet type derived from name '{sheet_name}'. Skipping header population.")

        except gspread.exceptions.WorksheetNotFound:
            logging.warning(f"Sheet '{sheet_name}' not found during header population. Skipping.")
        except Exception as e:
            logging.error(f"Error populating headers for sheet '{sheet_name}': {e}")

    # --- Populate Data (Account/Cash/Achu Sheets) ---
    logging.info("Populating data for Account/Cash/Achu sheets...")
    account_sheet_names = set(account_sheets + ['Cash', 'Achu']) # Sheets to populate data into
    populated_rows_count = {} # Keep track of rows for formula application

    for account_name, group_df in grouped_data:
        target_sheet_name = account_name
        if not target_sheet_name or target_sheet_name == 'Unknown':
            logging.warning(f"Skipping {len(group_df)} transactions with missing or 'Unknown' source_account.")
            continue

        if target_sheet_name not in account_sheet_names:
             logging.warning(f"Account '{target_sheet_name}' from data is not in the target sheet list. Skipping population.")
             continue

        logging.info(f"Populating data for sheet: '{target_sheet_name}'")
        try:
            worksheet = spreadsheet.worksheet(target_sheet_name)

            sheet_id = sheet_id_map.get(target_sheet_name)

            if sheet_id is None:
                logging.error(f"Could not find sheetId for sheet '{target_sheet_name}'. Skipping deletion and update.")
                continue

            # --- Clear existing data (Rows 2 onwards) using values().clear() ---
            # Clear data (A-G) and formula columns (H-I)
            clear_range = f'{target_sheet_name}!A2:I'
            logging.info(f"Clearing existing data in range: {clear_range}")
            try:
                # Use sheets_service (built from credentials) to clear values
                sheets_service.spreadsheets().values().clear(
                    spreadsheetId=spreadsheet.id,
                    range=clear_range,
                    body={} # Empty body for clear operation
                ).execute()
                logging.info(f"Successfully cleared data in '{target_sheet_name}'.")
            except HttpError as error:
                logging.error(f"API error clearing data in sheet '{target_sheet_name}': {error}. Proceeding with data write attempt.")
                # Keeping original behavior: log error and continue

            # Prepare data for writing (list of lists)
            # Use output_columns_data defined earlier
            data_to_write = group_df[output_columns_data].values.tolist()

            if data_to_write:
                num_rows = len(data_to_write)
                num_cols = len(output_columns_data)
                end_cell = gspread.utils.rowcol_to_a1(num_rows + 1, num_cols) # +1 because it's 1-based index and starts at row 2
                update_range = f"A2:{end_cell}"

                logging.info(f"Writing {num_rows} rows to range {update_range} in sheet '{target_sheet_name}'")
                worksheet.update(update_range, data_to_write, value_input_option='USER_ENTERED')
                populated_rows_count[target_sheet_name] = num_rows
            else:
                logging.info(f"No data to write for account '{target_sheet_name}'")
                populated_rows_count[target_sheet_name] = 0

        except gspread.exceptions.WorksheetNotFound:
            logging.warning(f"Worksheet '{target_sheet_name}' not found during data population. Skipping.")
        except gspread.exceptions.APIError as error:
            logging.error(f"API error updating sheet '{target_sheet_name}': {error}")
            # Continue with other sheets?
        except Exception as e:
            logging.error(f"Unexpected error updating sheet '{target_sheet_name}': {e}")
            # Continue with other sheets?

    # --- Apply Formulas (Account/Cash/Achu Sheets) ---
    logging.info("Applying row-wise formulas for Appu/Achu columns...")
    for sheet_name, num_rows in populated_rows_count.items():
        if sheet_name in account_sheet_names and num_rows > 0:
            try:
                worksheet = spreadsheet.worksheet(sheet_name)
                is_achu_sheet = (sheet_name == "Achu")
                _apply_row_formulas(worksheet, start_row=2, end_row=num_rows + 1, is_achu_sheet=is_achu_sheet)
            except gspread.exceptions.WorksheetNotFound:
                 logging.warning(f"Sheet '{sheet_name}' not found during formula application. Skipping.")
            except Exception as e:
                 logging.error(f"Error applying row formulas to sheet '{sheet_name}': {e}")

    # --- Apply Formulas (Final Recon Sheet) --- (DISABLED) ---
    # logging.info("Applying aggregation formulas for Final Recon sheet...")
    # try:
    #     final_recon_sheet = spreadsheet.worksheet("Final Recon")
    #     # Get the names of sheets to include in the query (excluding Final Recon itself)
    #     source_sheet_names = [name for name in target_sheet_names if name != "Final Recon"]
    #     _apply_final_recon_formulas(final_recon_sheet, source_sheet_names)
    # except gspread.exceptions.WorksheetNotFound:
    #     logging.error("Sheet 'Final Recon' not found. Cannot apply aggregation formulas.")
    # except Exception as e:
    #     # Log the specific error from _apply_final_recon_formulas
    #     logging.error(f"Error applying Final Recon formulas: {e}")
    # --- End Disabled Final Recon Formulas ---
    # --- Apply Data Validation ---
    logging.info("Applying data validation rules...")
    # Get category values directly from the Enum for validation rule setting
    category_values_for_validation = sorted([cat.value for cat in Category if cat != Category.UNCATEGORIZED]) # Exclude UNKNOWN if needed

    # Log the unique categories found in the *data* (populated earlier)
    logging.info(f"Found {len(unique_categories)} unique categories in data: {unique_categories}") # Use populated unique_categories

    split_values = ['0', '1', '2'] # As strings for list validation

    for sheet_name in account_sheet_names: # Apply to Cash, Achu, and dynamic account sheets
        num_rows = populated_rows_count.get(sheet_name, 0)
        if num_rows > 0: # Only apply if data exists
            try:
                worksheet = spreadsheet.worksheet(sheet_name)
                end_row_index = num_rows + 1 # Apply down to the last populated row

                # Apply Category validation (Column C = 3) using Enum values
                if category_values_for_validation:
                     _apply_data_validation(
                         worksheet=worksheet,
                         start_row=2, end_row=end_row_index,
                         start_col=3, end_col=3, # Column C
                         condition_type='ONE_OF_LIST',
                         condition_values=category_values_for_validation, # Use Enum values for the rule
                         input_message="Select a category",
                         strict=True # Or False to allow other values with warning
                     )
                else:
                    logging.warning(f"No Enum categories found to apply validation in sheet '{sheet_name}'.")
                # Apply is_expense validation (Column B = 2) - List (0/1)
                _apply_data_validation(
                    worksheet=worksheet,
                    start_row=2, end_row=end_row_index,
                    start_col=2, end_col=2, # Column B
                    condition_type='ONE_OF_LIST',
                    condition_values=['0', '1'],
                    input_message="Enter 0 (Income/Transfer) or 1 (Expense)",
                    strict=True
                )

                # Apply Is Split validation (Column G = 7)
                _apply_data_validation(
                    worksheet=worksheet,
                    start_row=2, end_row=end_row_index,
                    start_col=7, end_col=7, # Column G
                    condition_type='ONE_OF_LIST',
                    condition_values=split_values,
                    input_message="Select 0 (No), 1 (Split 50/50), or 2 (Achu Only)",
                    strict=True
                )

            except gspread.exceptions.WorksheetNotFound:
                 logging.warning(f"Sheet '{sheet_name}' not found during data validation application. Skipping.")
            except Exception as e:
                 logging.error(f"Error applying data validation to sheet '{sheet_name}': {e}")

    # Note: 'Cash' and 'Achu' sheets are now handled like other account sheets regarding data population.
    # If they need specific placeholder data when no transactions exist, that logic could be added here.
    # For now, they will be blank if no transactions map to them.
    # --- Final Recon Sheet Formulas (Implementation needed) ---
    # The 'Final Recon' sheet structure is created, but formulas need to be applied.
    # logging.warning("'Final Recon' sheet formulas are not yet implemented.") # Removed, implementation exists
    logging.info(f"Google Sheet update process finished successfully for '{spreadsheet.title}'.") # Use spreadsheet.title
    return spreadsheet.url


# --- Helper Functions for Formulas and Validation ---

def _apply_row_formulas(worksheet: gspread.Worksheet, start_row: int, end_row: int, is_achu_sheet: bool):
    """Applies Appu/Achu formulas row by row using batch update."""
    logging.info(f"Applying row formulas to '{worksheet.title}' from row {start_row} to {end_row}")
    requests = []

    # Define formula templates based on sheet type
    # Column F: Cost, Column G: Is Split
    if is_achu_sheet:
        # Achu Sheet Specific Formulas
        appu_formula_template = '=IF(G{row}=0, F{row}, IF(G{row}=1, F{row}/2, IF(G{row}=2, 0, 0)))' # Explicitly handle 0 first
        achu_formula_template = '=IF(G{row}=0, 0, IF(G{row}=1, F{row}/2, IF(G{row}=2, F{row}, 0)))' # Explicitly handle 0 first
    else:
        # Standard Account/Cash Formulas (Same as Achu in this case based on workflow_requirements.md?)
        # Double-checking requirements: Lines 82-87 show identical formulas.
        appu_formula_template = '=ROUND(IF(G{row}<>0, B{row}*F{row}/G{row}, 0))' # Appu: New formula based on user input
        achu_formula_template = '=ROUND(IF(G{row}<>1, IF(G{row}<>0, B{row}*F{row}/G{row}, B{row}*F{row}) , 0))' # Achu: New formula based on user input

    # Prepare requests in the format needed for batch_update
    # List of dicts like {'range': 'A1', 'values': [['=FORMULA']]}
    formula_requests = []
    for row in range(start_row, end_row + 1):
        # Appu Formula (Column H = 8)
        formula_requests.append({
            "range": gspread.utils.absolute_range_name(worksheet.title, f"H{row}"),
            "values": [[appu_formula_template.format(row=row)]]
        })
        # Achu Formula (Column I = 9)
        formula_requests.append({
            "range": gspread.utils.absolute_range_name(worksheet.title, f"I{row}"),
            "values": [[achu_formula_template.format(row=row)]]
        })

    if formula_requests:
        # Construct the batch_update body
        batch_update_requests = []
        for req in formula_requests:
            range_a1 = req["range"]
            # --- Start GridRange Fix ---
            try:
                # Assuming single cell range like "Sheet1!H2" or just "H2"
                # gspread.utils.absolute_range_name ensures sheet name is present
                cell_a1 = range_a1.split('!')[-1] # Get "H2" part
                start_row_idx, start_col_idx = gspread.utils.a1_to_rowcol(cell_a1)
                grid_range = {
                    'sheetId': worksheet.id,
                    'startRowIndex': start_row_idx - 1, # 0-based
                    'endRowIndex': start_row_idx,       # Exclusive end row
                    'startColumnIndex': start_col_idx - 1, # 0-based
                    'endColumnIndex': start_col_idx        # Exclusive end col
                }
            except (gspread.exceptions.InvalidInputValue, ValueError) as e:
                logging.error(f"Could not convert range '{range_a1}' to grid range: {e}. Skipping this request.")
                continue # Skip this request if range is invalid
            # --- End GridRange Fix ---

            batch_update_requests.append({
                "updateCells": {
                    # Ensure 'values' structure matches API: list of rows, each row is list of cells
                    "rows": [{"values": [{"userEnteredValue": {"formulaValue": cell_value}} for cell_value in row_vals]} for row_vals in req["values"]],
                    "fields": "userEnteredValue.formulaValue", # More specific field mask
                    "range": grid_range
                }
            })

        body_correct = {"requests": batch_update_requests}

        try:
            # Use the correctly structured body
            worksheet.spreadsheet.batch_update(body_correct)
            logging.info(f"Successfully applied {len(formula_requests)} row formulas to '{worksheet.title}'.")
        except gspread.exceptions.APIError as e:
            logging.error(f"API error applying row formulas to '{worksheet.title}': {e}")
            raise # Re-raise to be caught by the caller
        except Exception as e:
            logging.error(f"Unexpected error applying row formulas to '{worksheet.title}': {e}")
            raise # Re-raise


def _build_final_recon_query(source_sheet_names: List[str]) -> str:
    """Builds the QUERY or FILTER array formula for Final Recon columns A-F."""
    if not source_sheet_names:
        return '={"Source","Category","Appu Expense","Achu Expense","Description","Actual Amount";ARRAYFORMULA(IF(ROW(A2:A)=2,"No source sheets found",))}' # Return header + error message

    # Escape sheet names containing spaces or special characters for use in formulas
    def escape_sheet_name(name):
        # Replace single quotes with double single quotes for Sheets formulas
        name = name.replace("'", "''")
        return f"'{name}'" # Always quote for safety

    # Build the array literal for the QUERY data source
    # Selects: Category (C), Appu (H), Achu (I), Description (E), Cost (F)
    # Adds sheet name as the first column ('Source')
    query_parts = []
    for name in source_sheet_names:
        escaped_name = escape_sheet_name(name)
        # Range assumes data starts from row 2 and goes down to row 1000 (adjust if needed)
        # Col C: Category, Col H: Appu Exp, Col I: Achu Exp, Col E: Description, Col F: Actual Amount
        # Using FILTER and stacking with semicolons for robustness across different data sizes
        # ARRAYFORMULA adds the sheet name to each row
        # Ensure range references are correct (e.g., C2:C, H2:H etc. for open-ended)
        query_parts.append(
            f'FILTER({{ARRAYFORMULA(IF(LEN({escaped_name}!C2:C),"{name.replace("'", "''")}",)), ' # Add escaped sheet name
            f'{escaped_name}!C2:C, {escaped_name}!H2:H, {escaped_name}!I2:I, '
            f'{escaped_name}!E2:E, {escaped_name}!F2:F}}, '
            f'LEN({escaped_name}!C2:C)>0)' # Filter out rows where Category is blank using LEN
        )

    # Combine all FILTER parts into a single array literal
    combined_filters = "{" + "; ".join(query_parts) + "}"

    # Final formula using QUERY to select columns and handle potential errors/empty results
    # Select Col1 (Source), Col2 (Category), Col3 (Appu), Col4 (Achu), Col5 (Desc), Col6 (Actual)
    # Where Col2 (Category) is not null
    # Header row is handled separately by writing directly
    # Using IFERROR to handle cases where combined_filters is empty
    final_formula = (
        f'=IFERROR(QUERY({combined_filters}, '
        f'"SELECT Col1, Col2, Col3, Col4, Col5, Col6 WHERE Col2 IS NOT NULL LABEL Col1 \'Source\', Col2 \'Category\', Col3 \'Appu Expense\', Col4 \'Achu Expense\', Col5 \'Description\', Col6 \'Actual Amount\'", 0), '
        '"No data found")' # Message if query fails or returns nothing
    )

    # Alternative using just stacked FILTERs (might be simpler if QUERY has issues)
    # final_formula = f'=IFERROR({combined_filters}, "No data found")'

    logging.debug(f"Constructed Final Recon Query: {final_formula}")
    return final_formula


def _apply_final_recon_formulas(worksheet: gspread.Worksheet, source_sheet_names: List[str]):
    """Applies aggregation formulas to the Final Recon sheet."""
    logging.info(f"Applying aggregation formulas to '{worksheet.title}'")

    # --- Clear existing formula ranges ---
    # Clear A2:F (aggregated data), H2:K (summary)
    clear_ranges = ['A2:F1000', 'H2:K1000'] # Clear large ranges
    try:
        worksheet.batch_clear(clear_ranges)
        logging.info(f"Cleared previous formula ranges in '{worksheet.title}': {clear_ranges}")
    except Exception as e:
        logging.warning(f"Could not clear ranges in '{worksheet.title}': {e}")


    # --- Formulas ---
    # Formula for Columns A-F (Aggregated Data)
    agg_formula = _build_final_recon_query(source_sheet_names)

    # Formula for Column H (Unique Categories) - assumes aggregated data starts in B2
    cat_heading_formula = '=IFERROR(UNIQUE(FILTER(B2:B, B2:B<>"")), "")'

    # Formulas for Columns I, J, K (SUMIFS based on Category Heading in H) - assumes summary starts in H2
    # I (Appu): Sum Col C (Appu Expense) where Col B (Category) matches H2
    appu_sum_formula = '=IF(H2<>"", SUMIFS(C2:C, B2:B, H2), "")'
    # J (Achu): Sum Col D (Achu Expense) where Col B (Category) matches H2
    achu_sum_formula = '=IF(H2<>"", SUMIFS(D2:D, B2:B, H2), "")'
    # K (Actual Amount): Sum Col F (Actual Amount) where Col B (Category) matches H2
    # OR simply sum I2 and J2? Requirements line 80 says "Sum of Col F per category". Let's use SUMIFS on F.
    # actual_sum_formula = '=IF(H2<>"", SUMIFS(F2:F, B2:B, H2), "")'
    # Simpler: Sum the calculated Appu/Achu sums for that category
    actual_sum_formula = '=IF(H2<>"", SUM(I2:J2), "")'


    # --- Apply Formulas using Batch Update ---
    requests = [
        # Aggregation Formula in A2
        {"range": gspread.utils.absolute_range_name(worksheet.title, "A2"), "values": [[agg_formula]]},
        # Category Heading Formula in H2
        {"range": gspread.utils.absolute_range_name(worksheet.title, "H2"), "values": [[cat_heading_formula]]},
        # Appu Sum Formula in I2 (will auto-expand if H expands)
        {"range": gspread.utils.absolute_range_name(worksheet.title, "I2"), "values": [[appu_sum_formula]]},
        # Achu Sum Formula in J2
        {"range": gspread.utils.absolute_range_name(worksheet.title, "J2"), "values": [[achu_sum_formula]]},
        # Actual Sum Formula in K2
        {"range": gspread.utils.absolute_range_name(worksheet.title, "K2"), "values": [[actual_sum_formula]]},
    ]

    # Apply ARRAYFORMULA wrapper for SUMIFS if needed for older sheets versions, but modern Sheets usually expands SUMIFS.
    # If I2:K2 don't auto-expand, wrap them like: =ARRAYFORMULA(IF(H2:H<>"", SUMIFS(...), ""))

    # Correct body structure for batch_update with formulas
    batch_update_requests = []
    for req in requests:
        range_a1 = req["range"]
        # --- Start GridRange Fix ---
        try:
            # Assuming single cell range like "Final Recon!A2" or just "A2"
            cell_a1 = range_a1.split('!')[-1] # Get "A2" part
            start_row_idx, start_col_idx = gspread.utils.a1_to_rowcol(cell_a1)
            grid_range = {
                'sheetId': worksheet.id,
                'startRowIndex': start_row_idx - 1, # 0-based
                'endRowIndex': start_row_idx,       # Exclusive end row
                'startColumnIndex': start_col_idx - 1, # 0-based
                'endColumnIndex': start_col_idx        # Exclusive end col
            }
        except (gspread.exceptions.InvalidInputValue, ValueError) as e:
            logging.error(f"Could not convert range '{range_a1}' to grid range: {e}. Skipping this request.")
            continue # Skip this request if range is invalid
        # --- End GridRange Fix ---

        batch_update_requests.append({
            "updateCells": {
                "rows": [{"values": [{"userEnteredValue": {"formulaValue": cell_value}} for cell_value in row_vals]} for row_vals in req["values"]],
                "fields": "userEnteredValue.formulaValue", # Use specific field mask
                "range": grid_range
            }
        })

    body_correct = {"requests": batch_update_requests}

    try:
        # Use the correctly structured body
        worksheet.spreadsheet.batch_update(body_correct)
        logging.info(f"Successfully applied aggregation formulas to '{worksheet.title}'.")
    except gspread.exceptions.APIError as e:
        logging.error(f"API error applying aggregation formulas to '{worksheet.title}': {e}")
        raise # Re-raise
    except Exception as e:
        logging.error(f"Unexpected error applying aggregation formulas to '{worksheet.title}': {e}")
        raise # Re-raise


# --- Data Validation Helper ---
def _apply_data_validation(worksheet: gspread.Worksheet, start_row: int, end_row: int, start_col: int, end_col: int, condition_type: str, condition_values: List, input_message: str = None, strict: bool = True):
    """
    Applies data validation rules to a specified cell range using batch update.

    Args:
        worksheet: The gspread Worksheet object.
        start_row: The starting row index (1-based).
        end_row: The ending row index (1-based).
        start_col: The starting column index (1-based).
        end_col: The ending column index (1-based).
        condition_type: The type of validation (e.g., 'ONE_OF_LIST', 'NUMBER_GREATER').
        condition_values: A list of values for the condition (e.g., list of strings for ONE_OF_LIST).
        input_message: Optional message shown when cell is selected.
        strict: If True, invalid data is rejected. If False, allows invalid data with a warning.
    """
    range_desc = f"R{start_row}C{start_col}:R{end_row}C{end_col}"
    logging.info(f"Applying data validation to '{worksheet.title}' range {range_desc} "
                 f"(Type: {condition_type}, Strict: {strict})")

    try:
        # Prepare condition values for the API request
        api_condition_values = [{'userEnteredValue': str(v)} for v in condition_values]

        # Build the request body for batchUpdate
        # GridRange construction here was already correct.
        requests = [{
            'setDataValidation': {
                'range': {
                    'sheetId': worksheet.id,
                    'startRowIndex': start_row - 1, # API uses 0-based index
                    'endRowIndex': end_row,         # Sheets API end index is inclusive for rows/cols, but GridRange is exclusive
                    'startColumnIndex': start_col - 1,
                    'endColumnIndex': end_col
                },
                'rule': {
                    'condition': {
                        'type': condition_type,
                        'values': api_condition_values
                    },
                    'inputMessage': input_message or f"Select from list", # Keep message concise
                    'showCustomUi': True, # Show dropdown arrow for lists
                    'strict': strict
                }
            }
        }]
        worksheet.spreadsheet.batch_update({'requests': requests})

        logging.debug(f"Successfully applied data validation to range {range_desc} in '{worksheet.title}'.")

    except gspread.exceptions.APIError as e:
        # Check for specific errors, e.g., invalid range or condition type
        logging.error(f"API error applying data validation to {range_desc} in '{worksheet.title}': {e}")
        # Don't raise here, allow other validations/steps to proceed
    except Exception as e:
        logging.error(f"Unexpected error applying data validation to {range_desc} in '{worksheet.title}': {e}")
        # Don't raise here

# Example usage (Commented out - requires Transaction objects and valid config for testing)
# if __name__ == '__main__':
#     print("This script is intended to be imported as a module.")
    # Add minimal test code here if needed, using dummy data and config
    # Requires a valid credentials file and folder ID for testing
    # try:
    #     # Load dummy config (replace with actual paths/IDs for testing)
    #     dummy_config = {
    #         'GOOGLE_SHEETS_CREDENTIALS_FILE': 'path/to/your/credentials.json', # Replace
    #         'GOOGLE_DRIVE_BUDGET_FOLDER_ID': 'your_folder_id', # Replace
    #         'GOOGLE_SHEETS_TEMPLATE_ID': None # Or 'your_template_id' # Replace
    #     }
    #     # Create dummy Transaction objects
    #     dummy_all = [
    #         Transaction(date='2024-05-01', description='Coffee', category='Food', amount=-5.0, source_account='Checking', transaction_type='DEBIT'),
    #         Transaction(date='2024-05-02', description='Salary', category='Income', amount=2000.0, source_account='Checking', transaction_type='CREDIT'),
    #         Transaction(date='2024-05-03', description='Groceries', category='Groceries', amount=-75.5, source_account='Credit Card', transaction_type='DEBIT'),
    #         Transaction(date='2024-05-04', description='Investment XYZ', category='Investment', amount=-500.0, source_account='Brokerage', transaction_type='DEBIT'),
    #     ]
    #     dummy_expenses = [
    #          Transaction(date='2024-05-01', description='Coffee', category='Food', amount=-5.0, source_account='Checking', transaction_type='DEBIT'),
    #          Transaction(date='2024-05-03', description='Groceries', category='Groceries', amount=-75.5, source_account='Credit Card', transaction_type='DEBIT'),
    #     ]
    #
    #     sheet_url = update_google_sheet(dummy_all, dummy_expenses, dummy_config)
    #     if sheet_url:
    #         print(f"Test finished. Sheet URL: {sheet_url}")
    #     else:
    #         print("Test finished with errors.")
    #
    # except (ValueError, FileNotFoundError) as e:
    #      print(f"Configuration error: {e}")
    # except Exception as e:
    #      print(f"An unexpected error occurred during test: {e}")
