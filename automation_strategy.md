# Budget Automation - High-Level Strategy

This document outlines a high-level technical strategy for automating the monthly budget generation process based on the requirements specified in `workflow_requirements.md`.

## 1. Overall Orchestration

A central Python script (e.g., `main.py`) will orchestrate the workflow. This script will import and execute functions/modules dedicated to specific tasks (email handling, PDF processing, data parsing, Sheets interaction). Execution will be triggered manually. The script will determine the target month (previous calendar month) at the start.

```python
# Example Conceptual Structure (main.py)
import email_handler
import pdf_processor
import data_parser
import sheets_manager
import config
import datetime
import os # Added for cleanup example

def get_previous_month():
    today = datetime.date.today()
    first_day_of_current_month = today.replace(day=1)
    last_day_of_previous_month = first_day_of_current_month - datetime.timedelta(days=1)
    year = last_day_of_previous_month.year
    month_num = last_day_of_previous_month.month
    month_name = last_day_of_previous_month.strftime("%B")
    return year, month_num, month_name

def main():
    cfg = config.load_config()
    year, month_num, month_name = get_previous_month()
    print(f"Processing budget for: {month_name} {year}")

    download_dir = './downloads' # Define download directory
    os.makedirs(download_dir, exist_ok=True) # Ensure it exists

    try:
        # 1. Email Fetching & PDF Downloading
        pdf_files = email_handler.download_budget_pdfs(cfg['email'], year, month_num, download_dir)
        if not pdf_files:
            print("No relevant PDFs found in emails.")
            return

        # 2. Data Parsing & Structuring
        all_transactions = []
        processed_files = []
        for pdf_path in pdf_files:
            try:
                transactions = pdf_processor.extract_transactions(pdf_path, cfg['parsing'])
                all_transactions.extend(transactions)
                processed_files.append(pdf_path) # Track successfully processed files
            except Exception as e:
                print(f"Error processing {os.path.basename(pdf_path)}: {e}")
                # Decide whether to continue or stop on error

        # 3. Categorization & Filtering
        categorized_data = data_parser.categorize_transactions(all_transactions, cfg['categories'])
        filtered_data = data_parser.filter_expenses(categorized_data, cfg['filters'])

        # 4. Google Sheet Interaction
        output_filename = f"Accounts-{year}-{month_name}"
        sheets_manager.create_and_populate_budget_sheet(
            cfg['google_sheets'],
            output_filename,
            filtered_data,
            cfg.get('template_id'), # Use .get for optional template
            cfg['drive_folder_id']
        )

        print(f"Budget sheet '{output_filename}' created successfully in Google Drive.")

    except Exception as e:
        print(f"An error occurred during the process: {e}")
        # Add more specific error handling/logging

    finally:
        # 5. Cleanup (Optional)
        if cfg.get('cleanup_downloads', False):
             print("Cleaning up downloaded files...")
             for pdf_path in pdf_files: # Attempt cleanup for all downloaded, even if processing failed for some
                 try:
                     os.remove(pdf_path)
                 except OSError as e:
                     print(f"Error removing file {pdf_path}: {e}")
        print("Process finished.")


if __name__ == "__main__":
    main()

```

## 2. Configuration Management

A central configuration file (e.g., `config.yaml` or `.env`) will store necessary parameters:
*   **Email:** Credentials (using OAuth 2.0 tokens stored securely, *not* hardcoded), server details (IMAP/API endpoint), search criteria (sender/subject keywords).
*   **PDF Parsing:** LLM API key (if used), endpoint, model details, prompt templates.
*   **Categorization:** Rules (keyword-to-category mapping), default category.
*   **Filtering:** List of categories or keywords to exclude (e.g., 'Investment', 'Insurance').
*   **Google Sheets:** API credentials (OAuth 2.0 token), target Google Drive folder ID, optional template Sheet ID (`template_id`).
*   **Cleanup:** Boolean flag (`cleanup_downloads`) to enable/disable removal of downloaded PDFs.

## 3. Email Access & PDF Downloading

*   **Method:** Use the **Gmail API** with **OAuth 2.0** for secure authentication. This is generally more robust and maintainable than IMAP + App Passwords.
*   **Library:** `google-api-python-client` for interacting with the Gmail API.
*   **Filtering:** Search emails within the target month/year range. Filter by sender (e.g., `from:bank@example.com`) and subject line keywords (e.g., `subject:"Monthly Statement"`). Use the `query` parameter in the API.
*   **Attachment Handling:** Identify emails with PDF attachments matching expected naming conventions (if any). Download relevant PDFs to a temporary local directory (e.g., `./downloads/`). Handle potential errors during download (e.g., network issues, permissions). Return list of downloaded file paths.

## 4. PDF Handling & Data Extraction

*   **Method:**
    1.  **Initial Attempt:** Use a Python library like **`PyMuPDF`** or **`pdfplumber`** to extract text content directly. These can sometimes extract tabular data effectively if the PDF structure is consistent.
    2.  **LLM Fallback/Primary:** If direct extraction is unreliable due to varying formats, feed the extracted text (or potentially image representations for complex PDFs using OCR if needed) to an **LLM API** (e.g., OpenAI, Anthropic) with a specific prompt requesting structured transaction data. This aligns with the requirement mentioning LLM use.
*   **Error Handling:** Implement robust error handling for PDFs that cannot be opened or parsed (log the error and skip the file, or raise an exception to be caught by the orchestrator).
*   **Output:** The goal is structured data for each transaction from a single PDF.

## 5. Data Parsing & Structuring

*   **Method:**
    *   **If using LLM:** Design the prompt to explicitly request output in a specific **JSON format**. Include examples in the prompt if necessary. Handle potential variations or errors in the LLM's JSON output (e.g., using try-except blocks during JSON parsing).
    *   **If using Regex/Rules (less likely given requirement):** Develop patterns based on common statement layouts. This is brittle and likely insufficient for varied sources.
*   **Structured Output Format (JSON):**
    ```json
    [
      {
        "Date": "YYYY-MM-DD", // Standardize date format
        "Description": "Transaction details",
        "Amount": 123.45, // Use negative for debits if applicable, or add a 'Type' field
        "Account": "Source Account Name/Number" // e.g., "Checking - 1234", "Credit Card - 5678"
      },
      // ... more transactions
    ]
    ```
*   **Account Identification:** The parsing step needs to reliably identify the source account for each transaction (e.g., from the PDF filename, title within the PDF, or consistent header/footer information). This might require specific logic per source bank/card.

## 6. Transaction Categorization

*   **Method:** Implement a **rule-based system** first, defined in the configuration file (`config.yaml`).
    *   Map keywords or merchant names found in the `Description` field to predefined categories (e.g., `"AMAZON"` -> `"Shopping"`, `"STARBUCKS"` -> `"Food & Drink"`). Use case-insensitive matching.
    *   Use regular expressions for more complex pattern matching if needed (e.g., matching utility bill patterns).
    *   Define the order of rules if multiple could match.
    *   Assign a default category (e.g., `"Uncategorized"`) if no rules match.
*   **Input:** Takes the list of structured transactions.
*   **Output:** Adds a `Category` field to each transaction dictionary.
    ```json
    {
      "Date": "YYYY-MM-DD",
      "Description": "Transaction details",
      "Amount": 123.45,
      "Account": "Checking - 1234",
      "Category": "Shopping" // Added field
    }
    ```
*   **Future Enhancement:** Could be replaced or augmented by a simple ML classifier trained on historical data if rules become unmanageable.

## 7. Expense Filtering

*   **Method:** After categorization, filter the list of transactions.
*   **Logic:** Remove any transaction where the assigned `Category` matches entries in the `filtering_keywords` list from the configuration (e.g., `['Investment', 'Insurance Premium', 'Transfer', 'Internal Transfer']`). Use case-insensitive comparison.
*   **Input:** List of categorized transactions.
*   **Output:** Filtered list of transactions (only expenses intended for the budget).

## 8. Google Sheets Interaction

*   **Method:** Use the **Google Sheets API v4** with **OAuth 2.0**.
*   **Library:** **`gspread`** (simpler API, often sufficient) or `google-api-python-client` (more features). `gspread` is recommended for ease of use.
*   **Sheet Creation & Structure (Programmatic):**
    *   **Primary Strategy:** The `sheets_handler.py` module will **programmatically create the entire spreadsheet structure** using the Google Sheets API. This ensures adherence to the required layout defined in `workflow_requirements.md`.
    *   **Responsibilities:**
        *   Create a new spreadsheet named `Accounts-YYYY-Month` in the specified Google Drive folder (`drive_folder_id`).
        *   Create all required sheets: 'Cash', 'Achu', dynamically named account sheets (based on parsed data), and 'Final Recon'.
        *   Set the exact column headers for 'Account'/'Cash'/'Achu' sheets (`Txn Date`, `is Expense`, `Category`, `Short desc`, `Description`, `Cost`, `Is Split`, `Appu`, `Achu`).
        *   Set the exact column headers for the 'Final Recon' sheet (`Source`, `Category`, `Appu Expense`, `Achu Expense`, `Description`, `Actual Amount`, Spacer, `Category Heading`, `Appu`, `Achu`, `Actual amount`).
        *   Apply the specified row-wise formulas for `Appu` (Column H) and `Achu` (Column I) to the relevant columns in the 'Achu', 'Cash', and dynamic account sheets.
        *   Implement the aggregation logic for the 'Final Recon' sheet (Columns A-F and H-K) using direct intra-workbook functions like `QUERY` or `FILTER` to aggregate data from all other relevant sheets (replacing any previous `IMPORTRANGE` approach).
        *   Set up data validation rules:
            *   Column C (`Category`) on Account/Cash/Achu sheets: Dropdown using a list of categories dynamically generated during the categorization step.
            *   Column G (`Is Split`) on Account/Cash/Achu sheets: Dropdown with the fixed list `[0, 1, 2]`.
    *   **Minimal Template (Optional):** A template sheet *might* optionally be used *only* for applying basic visual formatting (fonts, colors, basic cell borders) if desired, but it will **not** be relied upon for sheet structure, headers, formulas, or data validation rules. If used, its ID would be specified in the config (`template_id`).
*   **Data Population:**
    *   Group filtered transactions by `Account`.
    *   For each account:
        *   Ensure the corresponding sheet (created in the previous step) exists.
        *   Format the transaction data into a list of lists (rows), matching the column order (`Txn Date`, `is Expense`, `Category`, `Short desc`, `Description`, `Cost`, `Is Split`). Note that `Appu` and `Achu` columns are formula-driven and not directly populated with data here.
        *   Append the formatted transaction data to the appropriate sheet using `append_rows` or `batch_update`.
*   **Manual Sheet Handling ('Cash', 'Achu'):**
    *   These sheets are created programmatically as part of the structure setup.
    *   They will include the standard headers and the required formulas in columns H and I. No specific transaction data needs to be populated unless placeholder rows are explicitly required (which would also be added via API).
*   **Final Recon Sheet:**
    *   This sheet is created programmatically.
    *   The aggregation formulas (e.g., `QUERY`) are added programmatically to populate columns A-F and H-K based on data in the other sheets. No direct transaction data is written here; it's entirely formula-driven based on the other sheets.
*   **Dropdowns & Formatting:**
    *   Data Validation rules for dropdowns (`Category`, `Is Split`) are applied programmatically via the API (`batch_update` with `setDataValidation` requests).
    *   Basic visual formatting might optionally be applied based on a minimal template (see Sheet Creation) or added programmatically via the API if needed.

## 9. Cleanup & Notification

*   **Cleanup:** If `cleanup_downloads` is true in the config, iterate through the downloaded PDF file paths and use `os.remove()` to delete them. Include error handling for file removal.
*   **Notification:** Implement basic logging to the console using the `print()` function or the `logging` module, indicating start, progress (e.g., file being processed), success, or failure, including the name and location of the generated Google Sheet or specific error messages encountered.