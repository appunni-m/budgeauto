import logging
from enum import Enum
# Added imports for Transaction model
import re
from pydantic import BaseModel, Field, ValidationError, validator
from typing import List, Optional, Literal

# Setup logging consistent with other modules
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Define the categories using Enum
class Category(str, Enum):
    ACHU = "Achu"
    AISHU = "Aishu"
    AMMA = "Amma"
    BANK = "Bank"
    BODY = "Body" # Likely Gym
    BUDGET = "Budget"
    CAR = "Car"
    CHRISTMAS = "Christmas"
    COOK = "Cook"
    DRESS = "Dress"
    EDUCATION = "Education"
    ELECTRICITY = "Electricity"
    ENTERTAINMENT = "Entertainment"
    FAITH = "Faith"
    FAMILY = "Family"
    FOOD = "Food"
    FUEL = "Fuel"
    GIFT = "Gift"
    GROCERY = "Grocery"
    GYM = "Gym"
    HOUSE = "House" # Note: Similar to Household/Rent
    HOUSEHOLD = "Household"
    INCOME_TAX = "Income Tax"
    INSURANCE = "Insurance"
    INTERNET = "Internet"
    INTERNATIONAL_TRIP = "International Trip"
    INVESTMENT = "Investment"
    KITCHEN = "Kitchen"
    MAID = "Maid"
    MEDICAL = "Medical"
    PHONE = "Phone"
    PHILANTHROPY = "Philanthropy"
    PROCESSED_TRANSACTIONS = "Processed Transactions"
    PROFESSION = "Profession"
    RENT = "Rent"
    SALE = "Sale"
    SALON = "Salon"
    SCOOTER = "Scooter"
    COURIER = "Courier" # Added
    PAYMENT = "Payment" # Added
    PETROL = "Petrol" # Added
    SHOPPING = "Shopping" # Added
    SOFTWARE = "Software" # Added
    SUBSCRIPTION = "Subscription" # Added
    TAX = "Tax" # Added
    TRANSPORTATION = "Transportation"
    TRAVEL = "Travel"
    UNCATEGORIZED = "Uncategorized" # Default/Fallback
    VALUE_ADD = "Value Add"
    WATER = "Water"
    WEDDING = "Wedding"
    # Added based on task requirements (Income/Credit/Transfer)
    INCOME = "Income"
    SALARY = "Salary"
    REFUND = "Refund"
    CASHBACK = "Cashback"
    INTEREST = "Interest"
    TRANSFER = "Transfer"
    @classmethod
    def from_string(cls, value: str):
        if not isinstance(value, str):
            return cls.UNCATEGORIZED
        search_value = value.upper().strip().replace(" ", "_") # Normalize for matching enum keys/values

        # Handle specific known variations/typos first
        if search_value == 'ENTERTAINTMENT': search_value = 'ENTERTAINMENT'
        if search_value == 'BODY': search_value = 'GYM'
        if search_value == 'HOUSE': search_value = 'HOUSEHOLD'
        # Match against Enum keys (case-insensitive)
        for member_name, member_obj in cls.__members__.items():
            if member_name == search_value:
                return member_obj
        # Match against Enum values (case-insensitive, original string value)
        for member_obj in cls:
            if member_obj.value.upper() == value.upper().strip(): # Compare against original input string value
                 return member_obj

        logger.warning(f"Could not map string '{value}' to Category enum. Falling back to UNCATEGORIZED.")
        return cls.UNCATEGORIZED

# Default category Enum member
DEFAULT_CATEGORY_ENUM = Category.UNCATEGORIZED

# --- Moved Pydantic Models ---

class Transaction(BaseModel):
    """Represents a single financial transaction extracted from a PDF."""
    date: Optional[str] = Field(None, description="Transaction date (e.g., YYYY-MM-DD or DD/MM/YYYY)") # Made optional as AI might miss it
    description: str = Field(..., description="Raw description of the transaction from the statement")
    amount: Optional[float] = Field(None, description="Raw transaction amount from the statement (usually positive)") # Made optional
    source_account: Optional[str] = Field(None, description="Account derived from the PDF filename")
    # Fields to be populated by the AI processing step:
    short_description: Optional[str] = Field(None, description="AI-generated short description")
    is_expense: Optional[int] = Field(None, description="AI-determined flag: 1 for expense, 0 for income/credit")
    is_split: Optional[int] = Field(1, description="AI-determined flag: Indicates if the transaction should be split (1 for True/0 for False)")
    category: Optional['Category'] = Field(None, description="Category assigned by AI or default") # Added field
    transaction_type: Literal['credit', 'debit'] = Field(..., description="Type of transaction: credit or debit")

    @validator('amount', pre=True, allow_reuse=True)
    def clean_amount(cls, v):
        if v is None:
            return None
        if isinstance(v, str):
            # Remove commas and handle potential currency symbols or extra spaces
            cleaned_v = re.sub(r'[^\d.-]', '', v)
            try:
                return float(cleaned_v)
            except ValueError:
                # Handle cases where cleaning results in non-numeric string (e.g., empty)
                return None # Or raise an error, or return 0.0 depending on desired behavior
        # Ensure output is float even if input is int
        try:
            return float(v)
        except (ValueError, TypeError):
             return None

class TransactionList(BaseModel):
    """Represents a list of transactions extracted from a document page."""
    transactions: List[Transaction]