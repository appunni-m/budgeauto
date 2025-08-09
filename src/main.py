import sys
import os

# Add the project root directory (the parent of 'src') to sys.path
# This allows absolute imports like 'from src.models import ...' to work
# when running main.py directly.
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# --- Existing imports and code follow ---

import datetime
# import os # Removed duplicate import
import shutil # Added for directory cleanup
import json # Added for checkpointing
import argparse # Added for command-line arguments
import logging # Added for date parsing warnings
from dateutil.relativedelta import relativedelta # Added for date calculations
from dateutil import parser as date_parser # Added for flexible date parsing
from typing import List # For type hinting
# Local Imports
import config
cfg = config.load_config()

import email_handler
import pdf_parser
from pdf_parser import Transaction
import categorizer
from categorizer import Category, process_transactions_ai, filter_expenses
from src.models import DEFAULT_CATEGORY_ENUM
import sheets_handler

# --- OAuth Imports ---
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.errors import HttpError # Keep for potential errors during auth
from googleapiclient.discovery import build # Needed by handlers

# --- Define Scopes ---
# Define all potentially required scopes upfront.
SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly', # For reading emails
    'https://www.googleapis.com/auth/spreadsheets',   # For updating Google Sheets
    'https://www.googleapis.com/auth/drive'           # For creating folders and managing files
]

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def get_previous_month():
    """Calculates the year, month number, and month name for the previous month."""
    today = datetime.date.today()
    first_day_of_current_month = today.replace(day=1)
    last_day_of_previous_month = first_day_of_current_month - datetime.timedelta(days=1)
    year = last_day_of_previous_month.year
    month_num = last_day_of_previous_month.month
    month_name = last_day_of_previous_month.strftime("%B")
    return year, month_num, month_name

def get_oauth_credentials(config_data, required_scopes):
    """
    Handles the OAuth 2.0 flow to obtain user credentials for the specified scopes.
    Checks existing token validity and scopes, refreshes if possible,
    or runs the authorization flow if needed.

    Args:
        config_data (dict): Loaded configuration containing file paths.
        required_scopes (list): List of scope strings required for the application.

    Returns:
        google.oauth2.credentials.Credentials: Valid credentials object or None if failed.
    """
    creds = None
    token_file = config_data.get('GMAIL_TOKEN_FILE', 'gmail_token.json') # Central token file
    creds_file = config_data.get('GMAIL_OAUTH_CREDENTIALS_FILE')

    if not creds_file:
        logger.error("Configuration Error: 'GMAIL_OAUTH_CREDENTIALS_FILE' is missing.")
        return None
    if not os.path.exists(creds_file):
        logger.error(f"Configuration Error: OAuth credentials file not found at '{creds_file}'")
        return None

    # 1. Load existing token if available
    if os.path.exists(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, required_scopes)
            logger.info(f"Loaded credentials from {token_file}")
        except ValueError as e:
            logger.warning(f"Warning loading token from '{token_file}': {e}. Checking scopes.")
            try:
                 creds = Credentials.from_authorized_user_file(token_file) # Load without scope check
                 logger.info("Loaded token initially, will verify scopes.")
            except Exception as load_err:
                 logger.error(f"Error loading token file '{token_file}' even without scope check: {load_err}. Will attempt re-authorization.")
                 creds = None
        except Exception as e:
            logger.error(f"Error loading token file '{token_file}': {e}. Will attempt re-authorization.")
            creds = None

    # 2. Check validity, refresh if expired, or run flow if missing/invalid/scopes insufficient
    needs_reauth = False
    if not creds:
        needs_reauth = True
        logger.info("No existing token found.")
    elif not creds.valid:
        if creds.expired and creds.refresh_token:
            logger.info("Credentials expired. Attempting to refresh...")
            try:
                creds.refresh(Request())
                logger.info("Token refreshed successfully.")
                # After refresh, save the updated token
                try:
                    with open(token_file, 'w') as token:
                        token.write(creds.to_json())
                    logger.info(f"Refreshed credentials saved to {token_file}")
                except IOError as e:
                    logger.error(f"Error saving refreshed token file '{token_file}': {e}")
            except Exception as e:
                logger.error(f"Error refreshing token: {e}. Need to re-authorize.")
                needs_reauth = True
        else:
            logger.warning("Credentials invalid and cannot be refreshed.")
            needs_reauth = True

    # 3. Check if all required scopes are present in the current (potentially refreshed) token
    if creds and creds.valid and not set(required_scopes).issubset(set(creds.scopes)):
        logger.warning("Warning: Current token is valid but missing required scopes.")
        logger.warning(f"Required: {required_scopes}")
        logger.warning(f"Token has: {creds.scopes}")
        logger.warning(f"Missing: {list(set(required_scopes) - set(creds.scopes))}")
        needs_reauth = True

    # 4. Run authorization flow if needed
    if needs_reauth:
        logger.info(f"Need to obtain new authorization (or grant missing scopes).")
        # Attempt to remove old token file if re-authorization is needed
        if os.path.exists(token_file):
            try:
                os.remove(token_file)
                logger.info(f"Removed potentially invalid/incomplete token file: {token_file}")
            except OSError as rm_err:
                logger.warning(f"Warning: Could not remove old token file '{token_file}': {rm_err}")

        # Run the installed app flow
        try:
            logger.info(f"Starting OAuth flow using '{creds_file}' for scopes: {required_scopes}")
            flow = InstalledAppFlow.from_client_secrets_file(creds_file, required_scopes)
            creds = flow.run_local_server(port=0) # Use port=0 for dynamic port assignment
            logger.info("OAuth flow completed. User authorized.")
        except FileNotFoundError:
             logger.error(f"Error: Credentials file not found at '{creds_file}'. Cannot start OAuth flow.")
             return None
        except Exception as e:
            logger.error(f"An error occurred during the OAuth flow: {e}", exc_info=True)
            return None

        # 5. Save the newly obtained credentials
        if creds:
            try:
                with open(token_file, 'w') as token:
                    token.write(creds.to_json())
                logger.info(f"New credentials saved to {token_file}")
            except IOError as e:
                logger.error(f"Error saving new token file '{token_file}': {e}")
                # Proceed with the current session creds even if saving fails
        else:
             logger.error("Could not obtain valid credentials after OAuth flow.")
             return None # Failed to get credentials

    # Final check
    if not creds or not creds.valid:
        logger.error("Failed to obtain valid OAuth credentials after all steps.")
        return None

    logger.info("Successfully obtained valid OAuth 2.0 credentials with required scopes.")
    return creds


# Define checkpoint filenames
processed_transactions_file = "processed_transactions.json"
categorized_transactions_file = "categorized_transactions.json"


def filter_transactions_by_date(transactions: List[Transaction], target_year: int, target_month: int) -> List[Transaction]:
    """
    Filters transactions to include the target month plus the first two days of the next month.
    Ignores transactions without a valid date.
    """
    try:
        start_date = datetime.date(target_year, target_month, 1)
        # Calculate the first day of the month *after* the target month
        first_day_next_month = (start_date + relativedelta(months=1))
        # End date is the second day of the next month (inclusive)
        end_date_inclusive = first_day_next_month + datetime.timedelta(days=1) # Day 2

        logger.info(f"Applying date filter: Keeping transactions from {start_date.isoformat()} to {end_date_inclusive.isoformat()} (inclusive).")

        filtered_list = []
        ignored_count = 0
        no_date_count = 0
        for t in transactions:
            if t.date is None:
                no_date_count += 1
                continue # Ignore transactions with no date

            transaction_date_obj = None # Initialize
            if t.date is None:
                no_date_count += 1
                continue # Ignore transactions with no date

            if isinstance(t.date, datetime.date):
                transaction_date_obj = t.date
            elif isinstance(t.date, datetime.datetime):
                transaction_date_obj = t.date.date() # Convert datetime to date
            elif isinstance(t.date, str):
                parsed_date = None
                # Define all formats to try
                formats_to_try = [
                    "%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", # Existing
                    "%b %d, %Y", "%d/%m/%Y %H:%M:%S", "%d-%b-%y" # New
                ]
                for fmt in formats_to_try:
                    try:
                        if fmt == "%d/%m/%Y %H:%M:%S":
                            # Parse as datetime first, then get date
                            parsed_datetime = datetime.datetime.strptime(t.date, fmt)
                            parsed_date = parsed_datetime.date()
                        else:
                            # Parse directly to date
                            parsed_date = datetime.datetime.strptime(t.date, fmt).date()
                        
                        # If parsing succeeded, break the loop
                        if parsed_date:
                            break
                    except ValueError:
                        continue # Try next format
                
                if parsed_date:
                    transaction_date_obj = parsed_date
                else:
                    logger.warning(f"Could not parse date string '{t.date}' for transaction: {t.description}. Skipping.")
                    no_date_count += 1 # Count as skipped due to bad date format
                    continue # Skip this transaction
            else:
                # Handle unexpected types
                logger.warning(f"Unexpected date type '{type(t.date)}' for transaction: {t.description}. Skipping.")
                no_date_count += 1
                continue

            # Now use transaction_date_obj in the comparison
            if transaction_date_obj and start_date <= transaction_date_obj <= end_date_inclusive:
                filtered_list.append(t)
            else:
                # This 'else' now covers out-of-range dates AND dates that failed parsing/had wrong type
                # We already incremented no_date_count for parsing failures, so only increment ignored_count for out-of-range
                if transaction_date_obj: # Only increment ignored_count if we had a valid date object to compare
                     ignored_count += 1

        logger.info(f"Date Filtering Results: Kept {len(filtered_list)}, Ignored {ignored_count} (out of range), Skipped {no_date_count} (no date).")
        return filtered_list
    except Exception as e:
        logger.error(f"An unexpected error occurred during date filtering for {target_year}-{target_month}: {e}", exc_info=True)
        return transactions # Return original list on error to avoid losing data


def main():
    """Main execution function."""

    # --- Clear Downloads Directory ---
    downloads_dir = 'downloads'
    logger.info(f"--- Attempting to clear contents of '{downloads_dir}' directory ---")
    if os.path.isdir(downloads_dir):
        for item_name in os.listdir(downloads_dir):
            item_path = os.path.join(downloads_dir, item_name)
            try:
                if os.path.isfile(item_path) or os.path.islink(item_path):
                    os.remove(item_path)
                elif os.path.isdir(item_path):
                    shutil.rmtree(item_path)
            except Exception as e:
                logger.error(f"Failed to delete {item_path}. Reason: {e}")
        logger.info(f"--- Finished clearing '{downloads_dir}' directory ---")
    else:
        logger.warning(f"Directory '{downloads_dir}' not found. Skipping cleanup.")
    # --- End Clear Downloads Directory ---

    # --- Argument Parsing ---
    parser = argparse.ArgumentParser(description="Automate budget processing from email/PDFs to Google Sheets.")
    parser.add_argument(
        '--preview',
        action='store_true',
        help="Enable preview mode to review transactions from each PDF before processing."
    )
    args = parser.parse_args()
    # --- End Argument Parsing ---

    if not cfg:
        logger.critical("Failed to load configuration. Exiting.")
        return

    year, month_num, month_name = get_previous_month()
    logger.info(f"--- Starting Budget Automation for: {month_name} {year} ---")

    # --- Centralized OAuth Credential Handling ---
    logger.info("Obtaining OAuth credentials...")
    credentials = get_oauth_credentials(cfg, SCOPES)
    if not credentials:
        logger.critical("Failed to obtain necessary OAuth credentials. Exiting.")
        return
    logger.info("Credentials obtained successfully.")
    # --- End OAuth Handling ---

    # --- Two-Stage Resume/Checkpoint Logic ---
    all_transactions: List[Transaction] | None = None # Holds data after fetch/parse/combine OR loaded from processed_file
    final_transactions: List[Transaction] | None = None # Holds data after categorization OR loaded from categorized_file
    resume_stage: str | None = None # Tracks which stage we are resuming from ('processed', 'categorized', or None)

    logger.info(f"--- Checking for Checkpoint Files ---")
    # Stage 2 Check: Categorized File
    logger.info(f"Checking for categorized transactions file: {categorized_transactions_file}")
    if os.path.exists(categorized_transactions_file):
        logger.info(f"Found categorized file. Attempting to resume from: {categorized_transactions_file}")
        try:
            with open(categorized_transactions_file, 'r') as f:
                loaded_dicts = json.load(f)
                if loaded_dicts:
                    logger.info(f"Raw data loaded. Deserializing {len(loaded_dicts)} items into Transaction objects...")
                    deserialized_transactions = []
                    for i, item_dict in enumerate(loaded_dicts):
                        try:
                            # Deserialize category
                            if 'category' in item_dict and isinstance(item_dict['category'], str):
                                try:
                                    item_dict['category'] = Category.from_string(item_dict['category'])
                                except ValueError:
                                    logger.warning(f"  Item {i+1}: Unknown category string '{item_dict['category']}'. Setting to None.")
                                    item_dict['category'] = None
                            elif 'category' in item_dict and not isinstance(item_dict['category'], (Category, type(None))):
                                logger.warning(f"  Item {i+1}: Unexpected category type {type(item_dict['category'])}. Setting to None.")
                                item_dict['category'] = None

                            # Date string will be passed directly to Transaction model
                            # which expects Optional[str], no pre-parsing needed here.

                            transaction_obj = Transaction(**item_dict)
                            deserialized_transactions.append(transaction_obj)
                        except Exception as deser_err:
                            logger.warning(f"  Warning: Failed to deserialize item {i+1}: {deser_err}. Data: {item_dict}")
                    final_transactions = deserialized_transactions # Load directly into final list
                    resume_stage = 'categorized'
                    logger.info(f"Successfully resumed from categorized stage with {len(final_transactions)} transactions.")
                else:
                    logger.info(f"File {categorized_transactions_file} is empty. Will check for processed file.")
                    # Don't set resume_stage yet, proceed to check processed file
        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Warning: Failed to load/decode '{categorized_transactions_file}': {e}. Will check for processed file.")
        except Exception as e:
            logger.error(f"Warning: Unexpected error loading '{categorized_transactions_file}': {e}", exc_info=True)

    # Stage 1 Check: Processed File (only if not resumed from categorized)
    if resume_stage != 'categorized':
        logger.info(f"Checking for processed transactions file: {processed_transactions_file}")
        if os.path.exists(processed_transactions_file):
            logger.info(f"Found processed file. Attempting to resume from: {processed_transactions_file}")
            try:
                with open(processed_transactions_file, 'r') as f:
                    loaded_dicts = json.load(f)
                    if loaded_dicts:
                        logger.info(f"Raw data loaded. Deserializing {len(loaded_dicts)} items into Transaction objects...")
                        deserialized_transactions = []
                        for i, item_dict in enumerate(loaded_dicts):
                             try:
                                # Deserialize date (assuming ISO string) - No category expected here yet
                                if 'date' in item_dict and isinstance(item_dict['date'], str):
                                    try:
                                        item_dict['date'] = sheets_handler.parse_date_flexible(item_dict['date'])
                                    except ValueError:
                                        logger.warning(f"  Item {i+1}: Invalid date string '{item_dict['date']}'. Setting to None.")
                                        item_dict['date'] = None
                                # Ensure category is None if present but shouldn't be
                                if 'category' in item_dict:
                                    logger.warning(f"  Item {i+1}: Unexpected 'category' field found in processed file. Ignoring.")
                                    item_dict['category'] = None

                                transaction_obj = Transaction(**item_dict)
                                deserialized_transactions.append(transaction_obj)
                             except Exception as deser_err:
                                logger.warning(f"  Warning: Failed to deserialize item {i+1}: {deser_err}. Data: {item_dict}")
                        all_transactions = deserialized_transactions # Load into intermediate list
                        resume_stage = 'processed'
                        logger.info(f"Successfully resumed from processed stage with {len(all_transactions)} transactions. Needs categorization.")
                    else:
                        logger.info(f"File {processed_transactions_file} is empty. Proceeding with fresh run.")
                        resume_stage = None
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Warning: Failed to load/decode '{processed_transactions_file}': {e}. Proceeding with fresh run.")
                resume_stage = None
            except Exception as e:
                logger.error(f"Warning: Unexpected error loading '{processed_transactions_file}': {e}", exc_info=True)
                resume_stage = None
        else:
             logger.info(f"No checkpoint files found ({categorized_transactions_file} or {processed_transactions_file}). Starting fresh run.")
             resume_stage = None # Explicitly set for clarity
    # --- End Resume/Checkpoint Logic ---


    # --- Stage 1: Fetch, Parse, Combine, Filter, and Save Processed ---
    if resume_stage is None: # Only run if starting fresh
        logger.info("--- Stage 1: Fetching, Parsing, Combining, Filtering ---")
        try:
            # Step 1 & 2: Fetch Emails and Download PDFs
            downloaded_pdf_info = email_handler.fetch_and_download_pdfs(cfg, credentials)
            if not downloaded_pdf_info:
                logger.info("No relevant PDFs found or downloaded via email.")
                all_transactions = [] # Ensure it's an empty list
            else:
                # Step 3: Parse PDFs
                parsed_transactions = pdf_parser.parse_pdfs(downloaded_pdf_info, cfg, credentials, args.preview)
                if parsed_transactions:
                    logger.info(f"Successfully parsed {len(parsed_transactions)} transactions from PDFs.")
                    all_transactions = parsed_transactions
                else:
                     logger.info("No transactions were extracted from PDFs.")
                     all_transactions = [] # Ensure it's an empty list

            # Ensure all_transactions is a list before proceeding (even if empty)
            if all_transactions is None: # Should not happen due to above, but safety check
                all_transactions = []

            # Apply Date Filtering AFTER parsing
            logger.info(f"Applying date filter for target month {month_name} {year}...")
            all_transactions = filter_transactions_by_date(all_transactions, year, month_num)
            logger.info(f"Proceeding with {len(all_transactions)} transactions after date filtering.")

            # Save to Processed Checkpoint File
            if all_transactions:
                logger.info(f"Attempting to save {len(all_transactions)} processed transactions to checkpoint: {processed_transactions_file}")
                try:
                    data_to_save = []
                    for t in all_transactions:
                        if hasattr(t, 'model_dump'):
                            dumped = t.model_dump(mode='json')
                            # Ensure category is NOT saved here
                            if 'category' in dumped:
                                pass
                            data_to_save.append(dumped)
                        else:
                            logger.warning(f"  Skipping unknown type in processed save: {type(t)}")

                    with open(processed_transactions_file, 'w') as f:
                        json.dump(data_to_save, f, indent=4)
                    logger.info(f"Successfully saved {len(data_to_save)} processed transactions to checkpoint: {processed_transactions_file}")
                except (IOError, TypeError) as e:
                    logger.error(f"Error saving processed transactions checkpoint: {e}")
                except Exception as e:
                    logger.error(f"Unexpected error saving processed transactions checkpoint: {e}", exc_info=True)
            else:
                 logger.info("No transactions after filtering, skipping save to processed checkpoint.")

        except Exception as e:
            logger.error(f"An error occurred during Stage 1 (fetch/parse/filter/save): {e}", exc_info=True)
            # If stage 1 fails, all_transactions might be None or incomplete.
            # The next stage check will handle this.

    # --- Stage 2: Categorize and Save Categorized ---
    if resume_stage in [None, 'processed']: # Run if starting fresh OR resuming from processed
        logger.info("--- Stage 2: Categorizing Transactions ---")
        # Ensure we have transactions to categorize (either from Stage 1 or loaded in Stage 1 resume)
        if not all_transactions:
             logger.warning("No transactions available to categorize (either Stage 1 failed or processed file was empty/invalid). Skipping categorization.")
        else:
            try:
                logger.info(f"Starting AI enrichment for {len(all_transactions)} transactions...")
                enriched_transactions = process_transactions_ai(all_transactions, cfg)

                if not enriched_transactions:
                    logger.warning("No transactions remaining after AI enrichment step.")
                    final_transactions = [] # Ensure it's an empty list
                else:
                    logger.info(f"AI enrichment complete. {len(enriched_transactions)} transactions processed.")
                    final_transactions = enriched_transactions # Assign to final list

                    # Ensure all transactions have a category before saving
                    logger.info("Ensuring all transactions have a category assigned...")
                    assigned_default_count = 0
                    for txn in final_transactions:
                        if txn.category is None:
                            txn.category = DEFAULT_CATEGORY_ENUM
                            assigned_default_count += 1
                    if assigned_default_count > 0:
                        logger.info(f"Assigned default category '{DEFAULT_CATEGORY_ENUM.value}' to {assigned_default_count} transactions that had None.")

                    # Save to Categorized Checkpoint File
                    logger.info(f"Attempting to save {len(final_transactions)} categorized transactions to checkpoint: {categorized_transactions_file}")
                    try:
                        data_to_save = []
                        for t in final_transactions:
                             if hasattr(t, 'model_dump'):
                                 dumped = t.model_dump(mode='json')

                                 data_to_save.append(dumped)
                             else:
                                 logger.warning(f"  Skipping unknown type in categorized save: {type(t)}")

                        with open(categorized_transactions_file, 'w') as f:
                            json.dump(data_to_save, f, indent=4)
                        logger.info(f"Successfully saved {len(data_to_save)} categorized transactions to checkpoint: {categorized_transactions_file}")
                    except (IOError, TypeError) as e:
                        logger.error(f"Error saving categorized transactions checkpoint: {e}")
                    except Exception as e:
                        logger.error(f"Unexpected error saving categorized transactions checkpoint: {e}", exc_info=True)

            except Exception as e:
                logger.error(f"An error occurred during Stage 2 (categorization/save): {e}", exc_info=True)
                # If categorization fails, final_transactions might be None or incomplete.
                # The final check handles this.
    # Note: If resume_stage == 'categorized', final_transactions was loaded directly earlier.

    # If we resumed, all_transactions holds the data. If we ran full process, enriched_transactions holds it.
    # At this point, 'final_transactions' should hold the data if:
    # 1. We resumed from 'categorized' stage.
    # 2. We ran Stage 2 (categorization) successfully (either fresh run or resumed from 'processed').
    # It might be None or empty if errors occurred or no transactions were found/processed.

    # --- Final Check and User Confirmation ---
    # Check final_transactions instead of all_transactions
    if not final_transactions: # Check if list is None or empty
        logger.warning("No transactions available (neither resumed nor processed). Exiting.")
        return # Exit if absolutely no transactions

    logger.info(f"Processing complete. Proceeding with {len(final_transactions)} transactions.")

    # --- User Confirmation Checkpoint ---
    # Use final_transactions which contains either resumed or newly processed data
    logger.info(f"Ready for Google Sheets. Found {len(final_transactions)} transactions.")
    if resume_stage == 'categorized':
         logger.info(f"Using transactions resumed from final checkpoint: {categorized_transactions_file}")
    elif resume_stage == 'processed':
         logger.info(f"Using transactions categorized after resuming from processed checkpoint: {processed_transactions_file}")
         logger.info(f"Review the final categorized data saved in: {categorized_transactions_file}")
    else: # Fresh run
         logger.info(f"Using newly processed and categorized transactions.")
         logger.info(f"Review the final categorized data saved in: {categorized_transactions_file}")
         logger.info(f"(Intermediate processed data also saved in: {processed_transactions_file})")
    proceed = input("Proceed with writing these transactions to Google Sheets? (yes/no): ").lower().strip()

    if proceed == "yes":
        # Step 6: Update Google Sheet
        logger.info("Updating Google Sheet...")
        try:
            # Pass final_transactions (contains all accounts) and credentials
            sheet_link = sheets_handler.update_google_sheet(
                final_transactions,       # Pass the final list of transactions
                cfg,                      # Pass config
                credentials               # Pass the obtained credentials
            )

            if sheet_link:
                logger.info(f"Google Sheet successfully updated/created at: {sheet_link}")
                # Checkpoint files (processed and categorized) are intentionally kept.

                logger.info(f"--- Budget Automation Process Complete ---")

            else:
                logger.error("--- Budget Automation Process Failed ---")
                logger.error("Failed to update or create the Google Sheet. Check logs for details.")
                # Checkpoint files are kept if upload failed.

        except Exception as e:
            logger.error(f"An error occurred during the sheet update process: {e}", exc_info=True)
            # Checkpoint files are kept if upload failed.
    else:
        logger.warning("Aborting sheet update as requested.")
        logger.info(f"Aborted sheet update. Checkpoint files '{processed_transactions_file}' and '{categorized_transactions_file}' were not deleted.")
        # Optionally, add a return statement here if you want the script to exit completely
        # return


    # (Removed orphaned except and finally blocks from previous structure)


if __name__ == "__main__":
    main()