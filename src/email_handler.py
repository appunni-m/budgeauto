# -*- coding: utf-8 -*-
"""
Handles fetching emails and downloading PDF attachments from a personal Gmail
account using OAuth 2.0 (Installed Application Flow).

**IMPORTANT:** This module requires OAuth 2.0 Desktop Application credentials
obtained from Google Cloud Console. On the first run, it will open a browser
window for the user to authorize access to their Gmail account. Subsequent runs
will use the stored token ('gmail_token.json' by default).
"""

import os
import base64
import datetime
import re # Added for subject date parsing
from dateutil import parser # Added for robust date parsing
# from google.oauth2 import service_account # Removed: Using OAuth 2.0 InstalledAppFlow
# from google.oauth2.credentials import Credentials # No longer needed here, handled in main.py
# from google_auth_oauthlib.flow import InstalledAppFlow # No longer needed here
# from google.auth.transport.requests import Request # No longer needed here
from googleapiclient.discovery import build # Still needed to build the service
from googleapiclient.errors import HttpError # Still needed for error handling

# SCOPES are now defined and handled in main.py

# Removed _get_gmail_service_oauth function. Authentication is now handled in main.py
# and credentials object is passed to fetch_and_download_pdfs.


# Removed _get_previous_month_range function. Date range is now calculated dynamically below.
def fetch_and_download_pdfs(config, credentials):
    """
    Fetches emails from the previous month based on config criteria
    and downloads any PDF attachments found, using the provided OAuth credentials.

    Args:
        config (dict): Loaded configuration dictionary. Expected keys:
                       'PDF_DOWNLOAD_PATH': Directory to save PDFs.
                       'GMAIL_SEARCH_SENDER_FILTER' (optional): 'from:...' filter string.
                       'GMAIL_SEARCH_SUBJECT_FILTER' (optional): 'subject:...' filter string.
                       (OAuth related keys like GMAIL_OAUTH_CREDENTIALS_FILE and
                        GMAIL_TOKEN_FILE are used in main.py, not directly here).
        credentials (google.oauth2.credentials.Credentials): Valid OAuth 2.0 credentials
                                                             obtained from main.py.

    Returns:
        list: A list of dictionaries, where each dictionary contains the 'path'
              (str) of the downloaded PDF and its corresponding 'subject' (str).
              Example: [{'path': '/path/to/file.pdf', 'subject': 'Your Statement'}].
              Returns an empty list if no relevant emails with PDFs are found
              or if an error occurs.
    """
    print("Attempting to fetch emails and download PDFs...")

    # Get configuration values needed by this function
    download_path = config.get('PDF_DOWNLOAD_PATH', './downloads')
    # GMAIL_ADDRESS is no longer relevant for authentication here
    # target_user_email_for_search = config.get('GMAIL_ADDRESS') # Removed

    # Validate download path config
    if not download_path:
         print("Configuration Error: 'PDF_DOWNLOAD_PATH' is missing or empty.")
         return []

    # Ensure download directory exists
    try:
        os.makedirs(download_path, exist_ok=True)
    except OSError as e:
        print(f"Error creating download directory '{download_path}': {e}")
        return []

    # Build the Gmail service using the provided credentials
    if not credentials:
        print("Error: No valid credentials provided to fetch_and_download_pdfs. Aborting.")
        return []
    try:
        service = build('gmail', 'v1', credentials=credentials)
        print("Successfully built Gmail service using provided OAuth credentials.")
    except HttpError as error:
        print(f'An HTTP error occurred building the Gmail service: {error}')
        return []
    except Exception as e:
        print(f'An unexpected error occurred building the Gmail service: {e}')
        return []

    # Proceed with fetching emails if service build was successful
    try:

        # Calculate date range for the last 30 days
        today = datetime.date.today()
        end_date_dt = today # Use today as the end date (exclusive in Gmail query)
        start_date_dt = today - datetime.timedelta(days=30)

        # Format for Gmail API search query (YYYY/MM/DD)
        start_date = start_date_dt.strftime('%Y/%m/%d')
        end_date = end_date_dt.strftime('%Y/%m/%d') # Gmail 'before' is exclusive

        print(f"Searching emails for the authorized user ('me') from the last 30 days (after:{start_date} before:{end_date})...")
        # If you still want to log the configured email address for context:
        # if target_user_email_for_search:
        #     print(f"(Configured GMAIL_ADDRESS for context: {target_user_email_for_search})")

        # Construct search query
        query_parts = [
            f"after:{start_date}",
            f"before:{end_date}",
            "has:attachment", # Look for emails with attachments
            "filename:pdf",    # Specifically look for PDF attachments
            # Server-side subject filtering using OR logic
            'subject:("Credit Card Statement" OR "E - Pass Sheet" OR "Combined Account Statement" OR "Combined Email Statement")'
        ]
        sender_filter = config.get('GMAIL_SEARCH_SENDER_FILTER')
        if sender_filter:
            query_parts.append(sender_filter)
        # Removed optional subject_filter from config - using specific hardcoded filter above

        query = " ".join(query_parts)
        print(f"Using Gmail query: {query}")

        # Search for messages
        results = service.users().messages().list(userId='me', q=query).execute()
        messages = results.get('messages', [])

        if not messages:
            print("No emails found matching the criteria.")
            return []

        print(f"Found {len(messages)} potential emails.")
        downloaded_files_info = [] # Renamed to reflect new structure

        for msg_summary in messages:
            msg_id = msg_summary['id']
            try:
                # Get the full message details
                message = service.users().messages().get(userId='me', id=msg_id, format='full').execute()
                payload = message.get('payload', {})
                headers = payload.get('headers', [])
                parts = payload.get('parts', [])

                # Extract basic info for logging/naming
                subject = next((h['value'] for h in headers if h['name'].lower() == 'subject'), 'NoSubject')
                sender = next((h['value'] for h in headers if h['name'].lower() == 'from'), 'UnknownSender')
                date_str = next((h['value'] for h in headers if h['name'].lower() == 'date'), None)
                msg_date = 'UnknownDate' # Default if all parsing fails

                # 1. Try parsing the 'Date' header
                if date_str:
                    try:
                        # Use dateutil.parser for robust parsing of various formats
                        parsed_dt = parser.parse(date_str)
                        msg_date = parsed_dt.strftime('%Y-%m-%d')
                        print(f"  Successfully parsed date header '{date_str}' to '{msg_date}' using dateutil.")
                    except (ValueError, OverflowError, TypeError, AttributeError) as e:
                        # Catch potential errors from parser.parse
                        print(f"  Warning: Could not parse date header '{date_str}' using dateutil.parser: {e}. Trying subject.")
                        # Fall through to subject parsing (msg_date remains 'UnknownDate')

                # 2. If header parsing failed or wasn't possible, try parsing the subject
                if msg_date == 'UnknownDate':
                    print(f"  Attempting to extract date from subject: '{subject}'")
                    month_map = {name.lower(): f"{i:02d}" for i, name in enumerate(['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'], 1)}
                    subject_date_found = False

                    # Pattern: Month-YYYY (e.g., March-2024)
                    match = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)-(\d{4})', subject, re.IGNORECASE)
                    if match:
                        month_name, year = match.groups()
                        month_num = month_map.get(month_name.lower())
                        if month_num:
                            msg_date = f"{year}-{month_num}-01" # Use 1st day of month
                            subject_date_found = True
                            print(f"    Found subject date (Month-YYYY): {msg_date}")

                    # Pattern: MM/YYYY (e.g., 03/2024)
                    if not subject_date_found:
                        match = re.search(r'(\d{1,2})/(\d{4})', subject)
                        if match:
                            month, year = match.groups()
                            msg_date = f"{year}-{int(month):02d}-01" # Use 1st day
                            subject_date_found = True
                            print(f"    Found subject date (MM/YYYY): {msg_date}")

                    # Pattern: YYYY/MM (e.g., 2024/03)
                    if not subject_date_found:
                        match = re.search(r'(\d{4})/(\d{1,2})', subject)
                        if match:
                            year, month = match.groups()
                            msg_date = f"{year}-{int(month):02d}-01" # Use 1st day
                            subject_date_found = True
                            print(f"    Found subject date (YYYY/MM): {msg_date}")

                    if not subject_date_found:
                        print("    Could not extract date from subject.")
                        # msg_date remains 'UnknownDate'

                # msg_date is now either YYYY-MM-DD from header, YYYY-MM-01 from subject, or 'UnknownDate'

                # --- Client-side subject filtering removed - now handled by server-side query ---


                # --- Recursive search for PDF parts with enhanced detection ---
                def _find_pdf_parts(part):
                    """
                    Recursively search for downloadable PDF parts based on MIME type
                    or filename.
                    """
                    found_parts = []
                    mime_type = part.get('mimeType', '').lower()
                    filename = part.get('filename', '')
                    attachment_id = part.get('body', {}).get('attachmentId')

                    is_potential_pdf = False
                    reason = ""

                    # Check 1: Standard PDF MIME types
                    if mime_type in ['application/pdf', 'application/x-pdf']:
                        is_potential_pdf = True
                        reason = f"MIME type is {mime_type}"
                    # Check 2: Octet-stream with .pdf filename (case-insensitive)
                    elif mime_type == 'application/octet-stream' and filename and filename.lower().endswith('.pdf'):
                        is_potential_pdf = True
                        reason = "MIME type is application/octet-stream and filename ends with .pdf"

                    # Check if it's a downloadable attachment
                    if is_potential_pdf:
                        if filename and attachment_id:
                            # Log identification reason
                            print(f"    Identified potential PDF attachment: '{filename}' (Reason: {reason}).")
                            found_parts.append(part)
                        else:
                            # Log if it looked like a PDF but wasn't downloadable/named correctly by the API
                            print(f"    Skipping potential PDF part: Has PDF characteristics ({reason}) but missing filename ('{filename}') or attachmentId ('{attachment_id}').")
                    elif filename and filename.lower().endswith('.pdf'):
                        # Log parts with .pdf filenames that were skipped due to non-matching MIME type
                        print(f"    Skipping part with filename '{filename}': MIME type '{mime_type}' is not a recognized PDF type or octet-stream.")


                    # Recursively check nested parts
                    nested_parts = part.get('parts')
                    if nested_parts:
                        for nested_part in nested_parts:
                            # Use extend to add all found parts from the recursive call
                            found_parts.extend(_find_pdf_parts(nested_part))

                    return found_parts

                # Start search from the main payload
                pdf_parts_to_download = _find_pdf_parts(payload)
                # --- End Recursive search ---

                if not pdf_parts_to_download:
                     print(f"  No PDF attachments found in email from '{sender}' with subject '{subject}' (ID: {msg_id}).")
                     continue # Skip to the next email message

                # Process found PDF parts
                print(f"  Found {len(pdf_parts_to_download)} PDF part(s) in email from '{sender}' subject '{subject}' (ID: {msg_id}). Processing...")
                for part in pdf_parts_to_download: # Iterate through the parts found by the recursive search
                    filename = part.get('filename')
                    body = part.get('body', {}) # Already know it has body and attachmentId from _find_pdf_parts
                    attachment_id = body.get('attachmentId')

                    # The check 'mime_type == application/pdf' is implicitly done by _find_pdf_parts
                    if filename and attachment_id: # Simplified check as function guarantees PDF mime type and attachmentId
                        print(f"  Found PDF attachment: '{filename}' in email from '{sender}' with subject '{subject}'")

                        # Get the attachment data
                        attachment = service.users().messages().attachments().get(
                            userId='me', messageId=msg_id, id=attachment_id
                        ).execute()
                        file_data = base64.urlsafe_b64decode(attachment['data'].encode('UTF-8'))

                        # Sanitize filename components
                        safe_sender = "".join(c if c.isalnum() or c in (' ', '_', '-') else '_' for c in sender.split('<')[0].strip())
                        safe_subject = "".join(c if c.isalnum() or c in (' ', '_', '-') else '_' for c in subject)
                        safe_filename = "".join(c if c.isalnum() or c in ('.', '_', '-') else '_' for c in filename)

                        # Create a unique-ish filename
                        output_filename = f"{msg_date}_{safe_sender}_{safe_subject}_{safe_filename}"
                        # Truncate if too long (OS limits)
                        max_len = 200 # Conservative max length
                        if len(output_filename) > max_len:
                             # Keep date, sender start, subject start, and original filename end
                             keep_len = max_len - len(f"{msg_date}_..._..._{safe_filename}") - 3 # Account for ellipses and separators
                             half_keep = keep_len // 2
                             output_filename = f"{msg_date}_{safe_sender[:half_keep]}..._{safe_subject[:half_keep]}..._{safe_filename}"
                             output_filename = output_filename[:max_len] # Final trim just in case


                        filepath = os.path.join(download_path, output_filename)

                        # Avoid overwriting - add a counter if file exists
                        counter = 1
                        original_filepath = filepath
                        while os.path.exists(filepath):
                            name, ext = os.path.splitext(original_filepath)
                            filepath = f"{name}_{counter}{ext}"
                            counter += 1
                            if counter > 100: # Safety break
                                print(f"Warning: Could not find unique name for {original_filepath} after 100 attempts. Skipping.")
                                filepath = None
                                break
                        if filepath is None:
                            continue


                        print(f"    Downloading to: {filepath}")
                        try:
                            with open(filepath, 'wb') as f:
                                f.write(file_data)
                            downloaded_files_info.append({'path': filepath, 'subject': subject})
                        except IOError as e:
                            print(f"    Error writing file '{filepath}': {e}")
                        except Exception as e:
                            print(f"    An unexpected error occurred during file write for '{filepath}': {e}")
                        # Removed the outer loop's responsibility for finding PDFs,
                        # now just processes the ones found by _find_pdf_parts.

            except HttpError as error:
                print(f"An HTTP error occurred processing message ID {msg_id}: {error}")
                # Decide if you want to continue with other messages or stop
                # continue
            except Exception as e:
                print(f"An unexpected error occurred processing message ID {msg_id}: {e}")
                # continue

        print(f"Finished processing emails. Downloaded {len(downloaded_files_info)} PDF files.")
        return downloaded_files_info

    except HttpError as error:
        print(f'An HTTP error occurred during email fetching: {error}')
        if error.resp.status == 403:
             print("Error 403: Check if the Gmail API is enabled in your Google Cloud project.")
        elif error.resp.status == 401:
             print("Error 401: Authentication failed. The OAuth token might be invalid or revoked. Try deleting the token file and re-running.")
        return []
    except FileNotFoundError as e:
        print(f"Configuration Error: {e}")
        return []
    except Exception as e:
        print(f'An unexpected error occurred in fetch_and_download_pdfs: {e}')
        # Consider logging the traceback here for debugging
        # import traceback
        # traceback.print_exc()
        return []

# Example usage block removed.
# To test, ensure your main script loads the correct config including:
# 'GMAIL_OAUTH_CREDENTIALS_FILE': 'path/to/your/gmail_oauth_desktop_creds.json',
# 'GMAIL_TOKEN_FILE': 'gmail_token.json', # Or your desired path
# 'PDF_DOWNLOAD_PATH': './downloads' # Or your desired path