import os
from dotenv import load_dotenv

def load_config():
    """
    Loads configuration from a .env file in the project root
    and returns it as a dictionary.
    """
    # Construct the path to the .env file relative to this script's directory
    # Assumes config.py is in 'src' and .env is in the parent directory
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    dotenv_path = os.path.join(project_root, '.env')

    # Load the .env file
    load_dotenv(dotenv_path=dotenv_path)

    # Load specific variables into a dictionary
    # Add more variables here as needed based on .env.example
    config = {
        'GMAIL_ADDRESS': os.getenv('GMAIL_ADDRESS'),
        'GMAIL_APP_PASSWORD': os.getenv('GMAIL_APP_PASSWORD'), # May be unused now for email_handler
        'GMAIL_OAUTH_CREDENTIALS_FILE': os.getenv('GMAIL_OAUTH_CREDENTIALS_FILE'), # For InstalledAppFlow
        'GMAIL_TOKEN_FILE': os.getenv('GMAIL_TOKEN_FILE', 'gmail_token.json'), # Default token path if not set
        # 'GOOGLE_SERVICE_ACCOUNT_KEY_FILE': os.getenv('GOOGLE_SERVICE_ACCOUNT_KEY_FILE'), # Removed - Using OAuth
        'GOOGLE_DRIVE_BUDGET_FOLDER_ID': os.getenv('GOOGLE_DRIVE_BUDGET_FOLDER_ID'),
        'PDF_DOWNLOAD_PATH': os.getenv('PDF_DOWNLOAD_PATH', 'downloads/'), # Default if not set
        'OPENAI_API_KEY': os.getenv('OPENAI_API_KEY'), # Kept for potential future use
        # 'GEMINI_API_KEY': os.getenv('GEMINI_API_KEY'), # Removed - Using OAuth
        'PDF_PASSWORDS': [p.strip() for p in os.getenv('PDF_PASSWORDS', '').split(',') if p.strip()],
        # Add other potential config flags from strategy doc if needed
        # 'CLEANUP_DOWNLOADS': os.getenv('CLEANUP_DOWNLOADS', 'False').lower() == 'true',
        # 'TEMPLATE_ID': os.getenv('TEMPLATE_ID'),
        'ACCOUNT_NAMES': [
            "Canara Savings",
            "HDFC Savings",
            "ICIC Saphirro CC",
            "ICIC Amazon CC",
            "HDFC Regalia CC",
            "HDFC Swiggy CC",
            "Cash", # Assuming you might have manual entries
            "Achu"  # Assuming you might have manual entries
        ]
    }

    # Basic validation (optional, but good practice)
    # Update required vars - Service Account and Gemini Key are no longer directly required here
    # GMAIL_ADDRESS might not be strictly required if only OAuth is used, but keep for now.
    required_vars = ['GMAIL_ADDRESS', 'GMAIL_OAUTH_CREDENTIALS_FILE', 'GOOGLE_DRIVE_BUDGET_FOLDER_ID']
    missing_vars = [var for var in required_vars if not config.get(var)]
    if missing_vars:
        print(f"Warning: Missing required configuration variables in .env: {', '.join(missing_vars)}")
        # Depending on severity, you might raise an exception here instead
        # raise ValueError(f"Missing required configuration: {', '.join(missing_vars)}")

    return config

if __name__ == '__main__':
    # Example usage when running this script directly
    print("Loading configuration...")
    cfg = load_config()
    print("Configuration loaded:")
    for key, value in cfg.items():
        print(f"- {key}: {value}")