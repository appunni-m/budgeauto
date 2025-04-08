# Budget Automation Workflow Requirements

## 1. Introduction &amp; Goal

The goal is to automate the current manual process of compiling monthly budget data from email PDF attachments into a structured Google Sheet. The automation should run on-demand for the previous month and replicate the existing Google Sheet structure, including specific sheets, columns, dropdowns, and equations, while placing the output file in the designated Google Drive `Budget` folder.

## 2. Target Automated Workflow

The automated workflow should perform the following steps sequentially:

1.  **Initiation:** Triggered manually by the user (on-demand).
2.  **Date Determination:** Identify the target month (previous calendar month).
3.  **Email Fetching:** Access the user's specified email account.
4.  **Email Filtering:** Search for relevant emails containing budget-related PDF attachments for the target month.
5.  **PDF Downloading:** Download the identified PDF attachments.
6.  **Data Parsing:** Extract transaction data from the downloaded PDFs using an LLM or other parsing mechanism.
7.  **Data Structuring:** Organize extracted data into a standardized format (e.g., Date, Description, Amount, Account).
8.  **Categorization:** Apply predefined rules or logic to categorize each transaction.
9.  **Expense Filtering:** Filter out transactions related to investments and insurance premiums.
10. **Sheet Preparation:** Create a new Google Sheet or use a template replicating the structure of the existing manual sheets.
11. **Data Population:** Populate the relevant account-specific sheets with the processed transaction data.
12. **Manual Sheet Handling:** Populate the 'Cash' and 'Achu' sheets with placeholder/dummy data structures, preserving existing formats and equations.
13. **Summary &amp; Report Update:** Ensure the 'Summary' and 'Report' sheets update correctly based on the populated data (preserving existing equations). (*Note: 'Final Recon' replaces the previous 'Summary' concept based on new details*).
14. **Output Generation:** Save the completed Google Sheet to the user's Google Drive `Budget` folder, following the naming convention `Accounts-YYYY-Month` (e.g., `Accounts-2024-May`).
15. **Cleanup (Optional):** Remove temporary downloaded files.
16. **Notification:** Inform the user of completion or any errors encountered.

## 3. Inputs

*   **Email Access:** Secure method to access the user's email (e.g., App Password, OAuth token). Specific email provider needs to be identified.
*   **Target Month Trigger:** Mechanism for the user to initiate the process (implicitly for the *previous* calendar month).
*   **Categorization Rules:** A defined set of rules or a mapping file for transaction categorization.
*   **Filtering Criteria:** Explicit definition of how to identify investment and insurance transactions to be excluded.
*   **Google Drive Credentials:** Authorization to write files to the user's Google Drive, specifically the `Budget` folder.
*   **Template (Optional):** An existing Google Sheet file to use as a template for structure, equations, and dropdowns.

## 4. Processing Steps Details

*   **Email Fetching:** Needs robust handling for different email providers (e.g., Gmail, Outlook) and potential 2FA.
*   **PDF Parsing:** Requires a reliable method (likely LLM-based as per current process) to handle variations in PDF layouts from different sources. Error handling for unparseable PDFs is crucial.
*   **Categorization:** Logic needs to be clearly defined. Could be keyword-based, rule-based, or potentially require an external mapping file.
*   **Filtering:** Rules for identifying investment/insurance transactions must be precise.
*   **Google Sheet Interaction:** Use Google Sheets API. Needs careful handling to preserve existing dropdowns, formatting, and complex equations, especially in `Final Recon`. Creating sheets based on identified accounts dynamically.
*   **Dummy Data:** Define the structure and default values for the 'Cash' and 'Achu' sheets.

## 5. Output

*   **File Type:** Google Sheet.
*   **Location:** User's Google Drive, within the `Budget` folder.
*   **Naming Convention:** `Accounts-YYYY-Month` (e.g., `Accounts-2025-May`).
*   **Structure:**
        *   **Sheet Names:** The workbook will contain the following sheets:
            *   `Cash`: Manually handled or populated with placeholders.
            *   `Achu`: Manually handled or populated with placeholders.
            *   Account Sheets: Dynamically named based on parsed data (e.g., `HDFC CC`, `ICICI Savings`).
            *   `Final Recon`: Aggregates data from all other sheets.
            *   `Reporting`: (Deferred) This sheet's automation is currently out of scope.
        *   **Column Structure:**
            *   **Account/Cash/Achu Sheets:**
                *   `A`: `Txn Date`
                *   `B`: `is Expense` (Value indicating if it's an expense, e.g., 1)
                *   `C`: `Category` (Dropdown - see Data Validation)
                *   `D`: `Short desc`
                *   `E`: `Description`
                *   `F`: `Cost` (Transaction amount)
                *   `G`: `Is Split` (Dropdown - see Data Validation)
                *   `H`: `Appu` (Formula - see Formulas)
                *   `I`: `Achu` (Formula - see Formulas)
            *   **Final Recon Sheet:**
                *   `A`: `Source` (Sheet Name where transaction originated)
                *   `B`: `Category`
                *   `C`: `Appu Expense` (Aggregated from Col H of source sheets)
                *   `D`: `Achu Expense` (Aggregated from Col I of source sheets)
                *   `E`: `Description`
                *   `F`: `Actual Amount` (Aggregated from Col F of source sheets)
                *   `G`: (Spacer Column - Intentionally Blank)
                *   `H`: `Category Heading` (Unique categories from Col B)
                *   `I`: `Appu` (Sum of Col C per category in Col H)
                *   `J`: `Achu` (Sum of Col D per category in Col H)
                *   `K`: `Actual amount` (Sum of Col F per category in Col H)
        *   **Formulas:**
            *   **Achu Sheet (Specific):**
                *   Column H (`Appu`): `=IF(G{row}=1, F{row}/2, IF(G{row}=2, 0, F{row}))`
                *   Column I (`Achu`): `=IF(G{row}=1, F{row}/2, IF(G{row}=2, F{row}, 0))`
            *   **Other Account/Cash Sheets (Row-wise):**
                *   Column H (`Appu`): `=IF(G{row}=1, F{row}/2, IF(G{row}=2, 0, F{row}))`
                *   Column I (`Achu`): `=IF(G{row}=1, F{row}/2, IF(G{row}=2, F{row}, 0))`
                *   *(Note: `{row}` indicates the formula applies to the current row's values)*
            *   **Final Recon Sheet (Aggregation):**
                *   Columns A-F: Data aggregated from all Account/Cash/Achu sheets. **Crucially, this will use direct intra-workbook queries (e.g., `QUERY`, `FILTER`) instead of the previous `IMPORTRANGE(getSpreadsheetId(), ...)` approach for automation compatibility.** The logic involves stacking relevant columns (`Txn Date`, `Category`, `Short desc`, `Description`, `Cost`, `Appu`, `Achu`) from all source sheets.
                *   Column H: Unique list of categories derived from the aggregated data in Column B.
                *   Columns I-K: Sums of `Appu Expense`, `Achu Expense`, and `Actual Amount` (from columns C, D, F respectively) grouped by the unique categories listed in Column H.
        *   **Data Validation (Dropdowns):**
            *   **Account/Cash/Achu Sheets - Column C (`Category`):** Dropdown list populated dynamically based on categories identified during PDF parsing, potentially merged with a predefined list or user additions.
            *   **Account/Cash/Achu Sheets - Column G (`Is Split`):** Dropdown list with fixed values: `0`, `1`, `2`.

## 6. Assumptions

*   **Email Provider:** A specific email provider will be used (e.g., Gmail) that allows programmatic access.
*   **PDF Consistency:** PDFs containing transaction data have a reasonably consistent format amenable to parsing.
*   **Categorization Logic:** Clear and definable rules for categorization exist or can be created.
*   **Google Drive Access:** The system will have the necessary permissions to create/modify files in the target Google Drive folder.
*   **Sheet Structure Consistency:** The fundamental structure (sheets, key columns) detailed in Section 5 serves as the target template.
*   **LLM Availability/Cost:** An LLM is available and suitable for the parsing task, considering potential costs and rate limits.

## 7. Key Requirements Summary

*   **On-Demand Execution:** User triggers the process manually.
*   **Target Period:** Automation always processes data for the *previous* calendar month.
*   **Input Source:** Emails containing PDF attachments.
*   **Core Processing:** Parsing, Categorization, Filtering (exclude investments/insurance).
*   **Output:** Google Sheet in Google Drive (`Budget` folder).
*   **Structure Preservation:** Replicate specified sheet names, columns, dropdowns, and formulas (using intra-workbook queries for `Final Recon`).
*   **Manual Sheets:** Include 'Cash' and 'Achu' sheets with specified structure/formulas.
*   **Technology:** Likely involves Email APIs, PDF parsing (LLM), and Google Sheets API.