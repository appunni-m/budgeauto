import fitz  # PyMuPDF
import re
import os
import io
import json
import sys # Added for exit() in preview mode
import google.generativeai as genai
from PIL import Image
from typing import List # Modified import
from src.models import Transaction, TransactionList # Added import

# --- Helper Functions ---

def get_gemini_prompt(model_schema: str) -> str:
    """Generates the prompt for the Gemini Vision API."""
    # Note: This prompt asks Gemini to extract date, description, amount, and transaction_type.
    # Other fields (short_description, is_expense, is_split) are added later.
    # The model_schema passed in is generated from models.py
    return f"""
Analyze the provided image, which is a page from a bank or credit card statement PDF.

**Instructions for Data Extraction:**
1.  **Focus strictly on identifying and extracting data only from the main transaction table(s) present on the page.**
2.  **The table typically contains columns like 'Date', 'Description', and 'Amount'.**
3.  **Ignore all other text, summaries, headers, footers, account details, interest calculations, or totals that are not part of the row-by-row transaction entries.**
4.  **Extract the transaction date, description, amount, and transaction_type for each row in the table.**
5.  **For the `amount` field, extract the numeric value exactly as it appears on the statement.** This is typically a positive number for both credits and debits in the transaction list. Do *not* attempt to add a negative sign for debits in the JSON output.
6.  **Determine the `transaction_type`:**
    *   Look for indicators like "CR", "Credit", or specific columns designated for credits. If found, set `transaction_type` to `"credit"`.
    *   Look for indicators like "DR", "Debit", or specific columns for debits. If found, set `transaction_type` to `"debit"`.
    *   **Default Assumption:** If no clear credit indicator ("CR", "Credit", credit column) is associated with the transaction row, assume it is a `"debit"`. This is common for credit card statements where most entries are purchases.
7.  **If no transaction table is found on the page, return an empty list for the 'transactions' field (i.e., {{"transactions": []}}).**

**Output Format:**
*   Format the extracted data as a single JSON object containing a key "transactions".
*   The value of "transactions" should be a list of JSON objects, where each object represents a single transaction.
*   Each transaction object MUST conform to the following structure (extract only these fields):

```json
{{
  "date": "string (e.g., YYYY-MM-DD or DD/MM/YYYY)",
  "description": "string",
  "amount": "float (as it appears on the statement, usually positive)",
  "transaction_type": "string ('credit' or 'debit')"
}}
```

*   Here is the Pydantic model schema for the *final* Transaction object for clarity (though you only extract date, description, amount, and transaction_type from the image):
```python
{model_schema}
```

*   Ensure the output is ONLY the JSON object, starting with `{{` and ending with `}}`. Do not include any introductory text, explanations, or markdown formatting like ```json ... ``` around the final JSON output.
"""

def render_page_to_image_bytes(page: fitz.Page) -> bytes:
    """Renders a PDF page to PNG image bytes."""
    pix = page.get_pixmap(dpi=200)  # Increase DPI for better OCR quality
    img_bytes = io.BytesIO()
    # Use Pillow to save the pixmap data as PNG
    if pix.alpha:
        img = Image.frombytes("RGBA", [pix.width, pix.height], pix.samples)
    else:
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    img.save(img_bytes, format="PNG")
    img_bytes.seek(0)
    return img_bytes.read()

def _get_account_name_via_ai(filename: str, allowed_names: List[str], model: genai.GenerativeModel) -> str:
    """
    Uses Gemini to determine the most likely account name for a given filename
    from a predefined list.

    Args:
        filename: The base name of the PDF file.
        allowed_names: A list of valid, canonical account names.
        model: The initialized Gemini GenerativeModel instance.

    Returns:
        The matched account name from allowed_names, or "Unknown Account" if no match found or an error occurs.
    """
    default_account = "Unknown Account"
    if not allowed_names:
        print("  Warning: No ACCOUNT_NAMES provided in config for AI mapping.")
        return default_account
    if not model:
        print("  Error: Gemini model not available for account name mapping.")
        return default_account

    prompt = f"Analyze the following filename: '{filename}'. Choose the single best matching account name from this list: {', '.join(allowed_names)}. Respond with *only* the chosen account name from the list, and nothing else."

    try:
        print(f"  Attempting AI account mapping for: {filename}")
        # Use a model suitable for text generation (assuming the same one passed is okay)
        response = model.generate_content(prompt)
        response_text = response.text.strip()

        # Validate against the allowed list
        if response_text in allowed_names:
            print(f"  AI mapped '{filename}' to '{response_text}'")
            return response_text
        else:
            print(f"  Warning: AI response '{response_text}' not in allowed list: {allowed_names}. Filename: {filename}")
            return default_account

    except Exception as e:
        print(f"  Error during AI account name mapping for '{filename}': {e}")
        return default_account

# --- Core Parsing Function ---

def parse_pdfs(pdf_info_list: List[dict], config: dict, credentials, preview_mode: bool = False) -> List[Transaction]: # Type hint uses imported Transaction
    """
    Parses multiple PDF files using screenshots and Gemini Vision API
    to extract raw transaction data (date, description, amount) and adds the
    source_account based on filename and email subject rules. Does NOT determine is_expense or short_description.

    Args:
        pdf_info_list (list): A list of dictionaries, where each dictionary contains
                              'path' (str) and 'subject' (str) for a downloaded PDF.
                              Example: [{'path': '/path/to/file.pdf', 'subject': 'Your Statement'}]
        config (dict): The loaded application configuration, including 'PDF_PASSWORDS' and 'ACCOUNT_NAMES'.
        credentials: OAuth 2.0 credentials object for Google API authentication.
        preview_mode (bool): If True, enables interactive preview after each PDF.

    Returns:
        list: A list of Pydantic Transaction objects containing raw extracted data
              (date, description, amount) plus the derived source_account.
              Fields like is_expense, short_description, is_split will be None.
    """
    all_transactions: List[Transaction] = [] # Type hint uses imported Transaction
    pdf_passwords = config.get('PDF_PASSWORDS', []) # Expecting a list from config

    # Note: Gemini API key logic removed. Authentication relies on provided credentials (ADC).
    try:
        # Configure GenAI. It should pick up credentials from the environment
        # if the 'credentials' object is standard Google Auth credentials.
        # If specific project is needed, might need: genai.configure(project=credentials.project_id)
        genai.configure(api_key=os.getenv("GOOGLE_API_KEY")) # Assuming ADC works or GEMINI_API_KEY is set for GenAI library
        model = genai.GenerativeModel('gemini-1.5-flash')
    except Exception as e:
        print(f"Error configuring Gemini client: {e}")
        return []

    # Get the Pydantic model schema as a string to include in the prompt
    # We pass the full schema, but instruct Gemini to only extract specific fields
    # Need to generate schema from the imported Transaction model now
    try:
        transaction_schema = Transaction.model_json_schema() # Use imported Transaction schema
        prompt_text = get_gemini_prompt(json.dumps(transaction_schema, indent=2))
    except Exception as e:
        print(f"Error generating Transaction schema or prompt: {e}")
        return [] # Cannot proceed without a valid prompt

    for pdf_info in pdf_info_list:
        pdf_path = pdf_info['path']
        pdf_subject = pdf_info.get('subject', '') # Get subject, default to empty string if missing
        print(f"Processing PDF: {pdf_path} (Subject: '{pdf_subject}')...")
        base_name = os.path.basename(pdf_path)

        # Determine source_account using AI based on filename and configured account names
        account_names = config.get('ACCOUNT_NAMES', [])
        if not isinstance(account_names, list):
            print(f"  Warning: ACCOUNT_NAMES in config is not a list. Found type: {type(account_names)}. Skipping AI mapping.")
            account_names = [] # Ensure it's a list to avoid errors

        # --- Rule-based check for HDFC Savings filename pattern ---
        hdfc_savings_account_name = "HDFC Savings" # From config.py
        # Pattern: Anything_DDMMYYYY_Anything.pdf (case-sensitive extension)
        hdfc_pattern = r'^.+_\d{8}_.+\.pdf$'

        # --- Combined Rule-based check for HDFC Savings ---
        # Check 1: Filename pattern
        filename_matches = re.match(hdfc_pattern, base_name) is not None
        # Check 2: Subject keywords (case-insensitive)
        subject_matches = ("hdfc" in pdf_subject.lower() and "statement" in pdf_subject.lower())

        if hdfc_savings_account_name in account_names and filename_matches and subject_matches:
            print(f"  Combined Rule matched: Identified '{base_name}' as '{hdfc_savings_account_name}' based on filename pattern AND subject keywords ('{pdf_subject}').")
            source_account = hdfc_savings_account_name
        else:
            # Fallback to AI mapping if combined rule doesn't match or HDFC Savings isn't in config
            print(f"  Combined rule not matched for '{base_name}' (Filename match: {filename_matches}, Subject match: {subject_matches}) or '{hdfc_savings_account_name}' not in config. Falling back to AI mapping.")
            source_account = _get_account_name_via_ai(base_name, account_names, model)
        # The determined source_account will be assigned to transactions later

        doc = None
        opened_successfully = False
        try:
            # Try opening without password first
            try:
                doc = fitz.open(pdf_path)
                if doc.needs_pass:
                    doc.close() # Close if password needed but not provided yet
                    doc = None
                else:
                    opened_successfully = True
                    print(f"Opened {base_name} without password.")
            except Exception as e:
                 print(f"Could not open {base_name} without password, will try provided passwords. Error: {e}")


            # If not opened, try passwords
            if not opened_successfully and pdf_passwords:
                for password in pdf_passwords:
                    try:
                        doc = fitz.open(pdf_path)
                        if doc.authenticate(password):
                            print(f"Successfully authenticated {base_name} with a password.")
                            opened_successfully = True
                            break # Exit password loop on success
                        else:
                             doc.close() # Close if auth failed
                             doc = None
                    except Exception as e:
                        print(f"Error trying password for {base_name}: {e}")
                        if doc: doc.close()
                        doc = None
                if not opened_successfully:
                     print(f"Warning: Could not open {base_name} with any of the provided passwords.")

            elif not opened_successfully and not pdf_passwords:
                 print(f"Warning: {base_name} requires a password, but no passwords were provided in config.")


            if not doc or not opened_successfully:
                continue # Skip to the next PDF

            pdf_transactions: List[Transaction] = [] # Type hint uses imported Transaction
            for page_num, page in enumerate(doc):
                print(f"  Processing page {page_num + 1}/{len(doc)}...")
                try:
                    image_bytes = render_page_to_image_bytes(page)
                    image_part = {"mime_type": "image/png", "data": image_bytes}

                    # Send to Gemini
                    response = model.generate_content([prompt_text, image_part])

                    # Clean potential markdown formatting if Gemini didn't follow instructions perfectly
                    response_text = response.text.strip()
                    if response_text.startswith("```json"):
                        response_text = response_text[7:]
                    if response_text.endswith("```"):
                        response_text = response_text[:-3]
                    response_text = response_text.strip()

                    # Parse and validate with Pydantic
                    try:
                        # Gemini now returns date, description, amount, and transaction_type per the prompt.
                        # We validate against TransactionList, which expects Transaction objects.
                        # Pydantic will initialize Transaction objects using the data provided by Gemini,
                        # leaving other fields (like short_description, category) as None initially.
                        # Use imported TransactionList here
                        parsed_data = TransactionList.model_validate_json(response_text)
                        page_transactions = parsed_data.transactions
                        print(f"    Extracted {len(page_transactions)} transactions from page {page_num + 1}.")

                        # Add source account to each transaction
                        for tx in page_transactions:
                            tx.source_account = source_account
                            # Set split value based on description
                            if 'achu' in tx.description.lower():
                                tx.is_split = 2
                            # Other fields (short_description, is_expense) remain None

                        pdf_transactions.extend(page_transactions)
                    # Removed ValidationError import, so cannot catch it specifically. Catching general Exception instead.
                    # except ValidationError as ve:
                    #     print(f"    Error validating Gemini response for page {page_num + 1}: {ve}")
                    #     print(f"    Raw Gemini Response Text:\n{response.text[:500]}...") # Log part of the raw response
                    except json.JSONDecodeError as je:
                         print(f"    Error decoding JSON from Gemini response for page {page_num + 1}: {je}")
                         print(f"    Raw Gemini Response Text:\n{response.text[:500]}...")
                    except Exception as parse_e: # Catch broader exceptions during parsing/validation
                         print(f"    Unexpected error parsing/validating response for page {page_num + 1}: {parse_e}")
                         print(f"    Raw Gemini Response Text:\n{response.text[:500]}...")


                except Exception as page_e:
                    print(f"  Error processing page {page_num + 1} of {base_name}: {page_e}")

            # --- Preview Mode Logic ---
            add_transactions_to_main_list = True # Default to adding
            if preview_mode and pdf_transactions:
                print("-" * 40)
                print(f"PREVIEW: Raw Transactions extracted from: {base_name}")
                print("-" * 40)
                for i, tx in enumerate(pdf_transactions):
                    # Print raw extracted data + source account
                    print(f"  {i+1}. Date: {tx.date}, Desc: {tx.description}, Amount: {tx.amount}, Account: {tx.source_account}")
                print("-" * 40)

                while True:
                    user_input = input("Press Enter to ADD these raw transactions and continue, 's' to SKIP this file, 'q' to QUIT: ").lower().strip()
                    if user_input == '':
                        print(f"Adding raw transactions from {base_name}...")
                        break # Proceed to add
                    elif user_input == 's':
                        print(f"Skipping transactions from {base_name}...")
                        add_transactions_to_main_list = False
                        break # Skip adding
                    elif user_input == 'q':
                        print("Quitting script as requested.")
                        sys.exit(0) # Exit gracefully
                    else:
                        print("Invalid input. Please press Enter, 's', or 'q'.")
                print("-" * 40)
            # --- End Preview Mode Logic ---

            # Add the extracted (raw) transactions to the main list if not skipped
            if add_transactions_to_main_list:
                if pdf_transactions:
                    print(f"Adding {len(pdf_transactions)} raw transactions from {base_name} to the main list.")
                    all_transactions.extend(pdf_transactions)
                else:
                    print(f"No transactions extracted from {base_name} to add.")
            # If add_transactions_to_main_list is False (due to 's' in preview), this block is skipped.

        except Exception as e:
            print(f"Error processing PDF file {base_name}: {e}")
        finally:
            if doc:
                doc.close()

    print(f"Finished processing PDFs. Total raw transactions extracted: {len(all_transactions)}")
    return all_transactions

# Example usage (optional, for testing)
if __name__ == '__main__':
    # This block allows testing the parser independently.
    # You would need to create a dummy config and point to test PDFs.
    print("Running pdf_parser.py directly for testing...")
    # Example: Create a dummy config (API Key removed)
    test_config = {
        'PDF_PASSWORDS': [p.strip() for p in os.getenv('PDF_PASSWORDS', '').split(',') if p.strip()],
        'ACCOUNT_NAMES': ['Amazon Pay ICICI Bank Credit Card', 'ICICI Bank Credit Card Sapphiro'] # Example
    }
    # Example: List PDFs in a test directory (replace with your test path)
    test_pdf_dir = 'downloads/' # Or specify a dedicated test PDF folder
    test_pdf_files = []
    if os.path.isdir(test_pdf_dir):
        test_pdf_files = [os.path.join(test_pdf_dir, f) for f in os.listdir(test_pdf_dir) if f.lower().endswith('.pdf')]

    if not test_pdf_files:
        print(f"No test PDF files found in specified directory: {test_pdf_dir}")
    # Removed API Key check
    else:
        print(f"Found test PDFs: {test_pdf_files}")
        print(f"Using passwords: {test_config['PDF_PASSWORDS']}")
        print("Note: Running parser directly requires valid Application Default Credentials or GEMINI_API_KEY env var.")
        # Pass None for credentials in this direct test setup.
        # Real execution requires credentials from the main application flow.
        extracted_data = parse_pdfs(test_pdf_files, test_config, None, preview_mode=True) # Enable preview for testing
        print("\n--- Extracted Raw Transactions (Sample) ---")
        for i, tx in enumerate(extracted_data[:5]): # Print first 5
            # Use model_dump_json from the imported Transaction model
            print(tx.model_dump_json(indent=2))
        print(f"\nTotal extracted: {len(extracted_data)}")