# BudgeAuto: Automated Budget Tracker

## Overview

BudgeAuto is a Python application designed to automate the process of my personal budget tracking. It fetches bank and credit card statements directly from my Gmail account, extracts transaction data from PDF attachments using AI, categorizes these transactions, and uploads the structured data to a designated Google Sheet for easy analysis and reporting.

## Features

*   **Email Fetching:** Automatically scans a specified Gmail account for emails containing bank/credit card statements based on sender or subject criteria.
*   **PDF Attachment Handling:** Downloads PDF statement attachments from identified emails.
*   **AI-Powered PDF Parsing:** Utilizes the Google Gemini Vision API to accurately parse transaction details (date, description, amount) from various PDF statement formats.
*   **Flexible Date Handling:** Supports multiple common date formats found in statements (`DD/MM/YYYY`, `DD-Mon-YY`, `DD/MM/YYYY HH:MM:SS`).
*   **Transaction Type Identification:** Determines whether a transaction is a credit or debit and adjusts the amount sign accordingly (debits are positive, credits are negative for expense tracking).
*   **AI-Based Categorization:** Leverages the Google Gemini API to automatically assign expense categories to transactions. Provides a default category ("Uncategorized") if AI categorization fails or is uncertain.
*   **Persistent Checkpoints & Resume Logic:** Implements a two-stage checkpoint system:
    *   `processed_transactions.json`: Stores transactions after PDF parsing.
    *   `categorized_transactions.json`: Stores transactions after categorization.
    *   The script automatically detects these files and resumes processing from the last completed stage, preventing duplicate entries and redundant API calls.
*   **Google Sheets Integration:** Uploads the final processed and categorized transaction data to a specified Google Sheet.
*   **Custom Calculations:** Applies specific formulas to calculate shared expenses (e.g., 'Appu'/'Achu' split based on rules).
*   **Data Validation:** Adds dropdown menus for specific columns (like 'is_expense') in the Google Sheet for easier manual adjustments.
*   **Sheet Management:** Ensures required sheets ('Final Recon', 'Reporting') exist within the target Google Workbook.
*   **User Confirmation:** Includes a confirmation step before uploading data to Google Sheets, allowing for review.

## Prerequisites

*   Python 3.10 or higher
*   pip (Python package installer, usually included with Python)
*   A Google Account (for Gmail, Google Sheets, and Google Cloud/AI services)

## Setup Instructions

1.  **Clone Repository:**
    ```bash
    git clone <repository_url> # Replace <repository_url> with the actual URL
    cd budgeauto
    ```
    (Alternatively, download the source code ZIP and extract it.)

2.  **Install Dependencies:**
    Create a virtual environment (recommended):
    ```bash
    python -m venv venv
    source venv/bin/activate # On Windows use `venv\Scripts\activate`
    ```
    Install required packages:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Google Cloud Project Setup:**
    *   Go to the [Google Cloud Console](https://console.cloud.google.com/).
    *   Create a new project or select an existing one.
    *   Enable the **Gmail API**, **Google Sheets API**, and **Google Drive API** for your project. Search for them in the API library and click "Enable".

4.  **Create OAuth 2.0 Credentials:**
    *   In the Google Cloud Console, navigate to "APIs & Services" > "Credentials".
    *   Click "+ CREATE CREDENTIALS" > "OAuth client ID".
    *   Select "Desktop app" as the Application type.
    *   Give it a name (e.g., "BudgeAuto Client").
    *   Click "Create".
    *   A pop-up will show your Client ID and Client Secret. Click "DOWNLOAD JSON".
    *   **Rename the downloaded file to `budget_app_oauth.json` and place it in the root directory of the `budgeauto` project.**
    *   *Note:* The script requires the following permissions (scopes), which you will be asked to grant during the first run:
        *   `https://www.googleapis.com/auth/gmail.readonly` (To read emails and download attachments)
        *   `https://www.googleapis.com/auth/spreadsheets` (To read from and write to your Google Sheet)
        *   `https://www.googleapis.com/auth/drive` (Often required for managing authentication tokens and potentially file operations)

5.  **Google AI API Key:**
    *   Go to [Google AI Studio](https://aistudio.google.com/) or the Google Cloud Console (under "APIs & Services" > "Credentials", create an API Key).
    *   Create an API key for using the Gemini API. Securely copy this key.

6.  **Configure Environment Variables:**
    *   Copy the example environment file:
        ```bash
        cp .env.example .env
        ```
    *   Open the `.env` file in a text editor and fill in the required values:
        *   `GOOGLE_AI_API_KEY`: Your Google Gemini API key obtained in the previous step.
        *   `EMAIL_ADDRESS`: The Gmail address the script should access for statements.
        *   `EMAIL_PASSWORD`: **Important:** If you have 2-Factor Authentication (2FA) enabled on your Google Account (recommended), you **must** generate an "App Password" for BudgeAuto. Go to your Google Account settings -> Security -> App passwords. Use the generated 16-character App Password here. If you don't use 2FA, you can use your regular Gmail password, but this is less secure.
        *   `GOOGLE_SHEET_ID`: The ID of the Google Sheet where you want to upload data. You can find this in the Sheet's URL (e.g., `https://docs.google.com/spreadsheets/d/THIS_IS_THE_ID/edit`).
        *   `SHEET_NAME`: The name of the primary sheet within your Google Sheet workbook where transactions will be written (e.g., `Transactions`).

7.  **Initial Google Authentication:**
    *   Run the script for the first time from the project's root directory:
        ```bash
        python src/main.py
        ```
    *   The script should automatically open a web browser window.
    *   Log in to the Google Account you specified in `.env`.
    *   Review the requested permissions (Gmail read-only, Sheets read/write, Drive) and grant access.
    *   Upon successful authorization, the script will create a `token.json` file (or similar, depending on the library used) in the project directory. This file stores your authorization credentials securely, so you won't need to re-authenticate each time you run the script.

## Usage

Ensure your virtual environment is activated (`source venv/bin/activate` or `venv\Scripts\activate`).

Run the main script from the project's root directory:
```bash
python src/main.py
```
The script will:
1.  Attempt to authenticate using `token.json`.
2.  Check for checkpoint files (`processed_transactions.json`, `categorized_transactions.json`) to determine where to resume.
3.  Fetch new emails containing statements.
4.  Download and parse PDF attachments (resuming if `processed_transactions.json` exists).
5.  Categorize transactions (resuming if `categorized_transactions.json` exists).
6.  Prompt you for confirmation before uploading the data to Google Sheets.
7.  Upload the data and apply formatting/formulas to the specified sheet.

## Workflow & Checkpoints

The script follows this general workflow:

1.  **Fetch Emails:** Connects to Gmail and finds relevant statement emails.
2.  **Download PDFs:** Saves PDF attachments locally.
3.  **Parse PDFs:** Extracts transaction data using Gemini Vision. Saves results incrementally to `processed_transactions.json`.
4.  **Categorize:** Sends transaction descriptions to Gemini for categorization. Saves results incrementally to `categorized_transactions.json`.
5.  **Confirm:** Asks the user to confirm before proceeding with the upload.
6.  **Upload:** Writes the categorized data to the target Google Sheet.
7.  **Cleanup:** Deletes local checkpoint files (`processed_transactions.json`, `categorized_transactions.json`) after a successful upload.

**Resume Logic:**
*   If the script is run and `categorized_transactions.json` exists, it skips fetching, parsing, and categorization, loading data directly from this file before the confirmation step.
*   If only `processed_transactions.json` exists, it skips fetching and parsing, loading data from this file and proceeding only with categorization.
*   If neither file exists, it starts the full workflow from fetching emails.

This ensures that network issues or interruptions don't require reprocessing already completed steps.

## Google Sheet Structure

*   **Main Transaction Sheet:** (Name defined by `SHEET_NAME` in `.env`) This sheet contains the core transaction data with columns like:
    *   `Date`
    *   `Description`
    *   `Amount` (Debits +, Credits -)
    *   `Category` (Auto-filled by AI)
    *   `is_expense` (Dropdown: Yes/No)
    *   `Appu` (Calculated split amount)
    *   `Achu` (Calculated split amount)
    *   Other columns as defined/needed by the script.
*   **Final Recon Sheet:** Automatically created/managed by the script for reconciliation purposes (details depend on script logic).
*   **Reporting Sheet:** Automatically created/managed by the script for summary reports (details depend on script logic).

The script also applies formatting (like number formats) and data validation (dropdowns) to the main sheet.