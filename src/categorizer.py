import re
import os
import logging
import json # Added for potential input formatting
from typing import List, Dict, Any, Union, Optional
# Removed: from enum import Enum
from pydantic import BaseModel, Field, ValidationError
from pydantic_ai import Agent
from pydantic_ai.agent import AgentRunResult # Added import
from pydantic_ai.models.gemini import GeminiModel
from pydantic_ai.providers.google_gla import GoogleGLAProvider

# Import Category and Transaction from models using absolute import
from src.models import Transaction, Category, DEFAULT_CATEGORY_ENUM # Updated import

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Load Categorization Prompt ---
# IMPORTANT: This prompt needs a complete rewrite for the new AI tasks:
# It must instruct the AI to generate short_description, category_str, is_expense, and is_split
# based on the raw transaction details provided in the input.
FULL_PROMPT = "LOAD_PROMPT_FROM_FILE_OR_CONFIG" # Placeholder: Load actual prompt here
try:
    prompt_file_path = os.path.join(os.path.dirname(__file__), 'categorization_prompt.txt')
    if os.path.exists(prompt_file_path):
        with open(prompt_file_path, 'r') as f:
            FULL_PROMPT = f.read()
        logger.info("Successfully loaded categorization prompt from file.")
    else:
        logger.error(f"Categorization prompt file not found at: {prompt_file_path}")
        FULL_PROMPT = "Error: Prompt file not found. AI processing will likely fail."
except Exception as e:
    logger.error(f"Failed to load categorization prompt: {e}")
    FULL_PROMPT = "Error: Failed to load prompt. AI processing will likely fail."

FULL_PROMPT += "\n\n" +  "Valid Categories: \n" + "\n  ".join([cat.name for cat in Category]) + "\n\n" # Append valid categories to the prompt

# --- Category Enum moved to models.py ---


# --- Pydantic Models for AI Output ---

# Model for a single processed transaction returned by AI
class AIProcessedTransaction(BaseModel):
    # Removed short_description - we want to preserve the original description
    original_index: int = Field(..., description="The original index of the transaction from the input batch.")
    category_str: str = Field(..., description="The assigned category name (e.g., 'Food', 'Grocery', 'Rent') based on the rules and transaction details.")
    is_expense: int = Field(..., description="0 if income/transfer/refund, 1 if expense.")
    is_split: int = Field(..., description="0 if not split, 1 if needs splitting, 2 if part of an existing split.")

# Model for the batch output from the AI Agent
class AIProcessedBatch(BaseModel):
    processed_transactions: List[AIProcessedTransaction] = Field(..., description="A list of transactions processed by the AI.")


# --- AI Processing Setup ---

API_KEY = os.getenv("GEMINI_API_KEY", os.getenv("GOOGLE_API_KEY"))
agent = None
gemini_model = None

if not API_KEY:
    logger.error("GEMINI_API_KEY or GOOGLE_API_KEY environment variable not set. AI Processing disabled.")
else:
    try:
        gemini_model = GeminiModel('gemini-1.5-flash', provider=GoogleGLAProvider(api_key=API_KEY))
        logger.info("GeminiModel initialized successfully.")

        # Initialize the Agent with the BATCH output type
        agent = Agent(model=gemini_model, result_type=AIProcessedBatch)
        logger.info("PydanticAI Agent with Gemini initialized successfully for batch processing.")

    except ImportError as ie:
        logger.error(f"Required libraries ('pydantic-ai', 'google-generativeai') not found or import error: {ie}. Please check installation.")
        agent = None
    except Exception as e:
        logger.error(f"Error initializing PydanticAI Agent or GeminiModel: {e}")
        agent = None


# Helper function to safely get values (remains useful for preparing input)
def get_transaction_value(transaction: Union[Dict[str, Any], Transaction], key: str, default: Any = None) -> Any:
    """Safely gets a value from a transaction, handling both dict and object access."""
    value = default
    if isinstance(transaction, dict):
        value = transaction.get(key, default)
        # Add common variations if needed, e.g., transaction.get(key.lower(), default)
    elif hasattr(transaction, key):
         value = getattr(transaction, key, default)

    # Ensure we return an empty string instead of None for text fields if default is None
    if value is None and isinstance(default, str):
        return ""
    return value


def process_transactions_ai(raw_transactions: List[Transaction], config: Optional[Dict] = None) -> List[Transaction]:
    """
    Processes a batch of raw transactions using an AI Agent (Gemini) to enrich them.

    The AI is expected to generate:
    - short_description
    - category (as a string initially)
    - is_expense (boolean)
    - is_split (boolean)

    It modifies the original Transaction objects in the list by populating these fields.

    Args:
        raw_transactions (List[Transaction]): A list of raw transaction objects from models.py.
                                              Expected attributes: 'date', 'description', 'amount'.
                                              'short_description', 'category', 'is_expense', 'is_split' are initially None.
        config (dict, optional): Configuration dictionary (currently unused here).

    Returns:
        List[Transaction]: The same list of Transaction objects, now enriched with AI-generated data.
                           Returns the original list with defaults if AI processing fails.
    """
    if not agent:
        logger.error("Cannot process transactions: AI Agent is not initialized (check API key and libraries). Assigning defaults.")
        for txn in raw_transactions:
            # txn.short_description = txn.short_description or "AI Processing Disabled" # Keep original description
            txn.category = txn.category or DEFAULT_CATEGORY_ENUM # Use imported default
            txn.is_expense = txn.is_expense if txn.is_expense is not None else 1 # Default assumption: expense (1)
            txn.is_split = txn.is_split if txn.is_split is not None else False
        return raw_transactions

    if "Error:" in FULL_PROMPT:
         logger.error(f"Cannot process transactions: {FULL_PROMPT}. Assigning defaults.")
         for txn in raw_transactions:
             # txn.short_description = txn.short_description or "AI Prompt Error" # Keep original description
             txn.category = txn.category or DEFAULT_CATEGORY_ENUM # Use imported default
             txn.is_expense = txn.is_expense if txn.is_expense is not None else 1 # Default assumption: expense (1)
             txn.is_split = txn.is_split if txn.is_split is not None else 0 # Default assumption: not split (0)
         return raw_transactions

    if not raw_transactions:
        logger.info("No transactions provided for AI processing.")
        return []

    logger.info(f"Starting AI batch processing for {len(raw_transactions)} transactions...")

    # Prepare input for the AI Agent - format the list of raw transactions
    # Option 1: Simple list of descriptions (might lose context)
    # input_texts = [f"Date: {t.date}, Desc: {t.description}, Amt: {t.amount}" for t in raw_transactions]
    # input_for_ai = "\n---\n".join(input_texts)

    # Option 2: JSON string representation (better structure for AI)
    try:
        # Select relevant fields for the AI prompt
        input_data_for_ai = [
            {
                "index": i, # Include index for potential matching robustness
                "date": str(t.date) if t.date else "Unknown",
                "description": t.description or "",
                "amount": t.amount if t.amount is not None else 0.0
            }
            for i, t in enumerate(raw_transactions)
        ]
        input_for_ai = json.dumps(input_data_for_ai, indent=2)
        logger.debug(f"Prepared JSON input for AI:\n{input_for_ai[:500]}...") # Log snippet
    except Exception as json_err:
        logger.error(f"Error creating JSON input for AI: {json_err}. Falling back.")
        # Fallback: simple string list
        input_texts = [f"Index: {i}, Date: {t.date}, Desc: {t.description}, Amt: {t.amount}" for i, t in enumerate(raw_transactions)]
        input_for_ai = "\n---\n".join(input_texts)


    ai_processed_successfully = False # Flag to track success
    result = None # Initialize result
    validation_error_occurred = False
    processing_error_occurred = False

    try:
        # Call PydanticAI Agent for the entire batch
        logger.debug(f"Sending batch of {len(raw_transactions)} transactions to Agent...")
        # --- MODIFIED CALL ---
        # Pass only input_text. Assumes prompt/output model are handled by Agent config.
        # If this fails, may need to re-add prompt=FULL_PROMPT or adjust Agent init.
        result = agent.run_sync(input_for_ai) # Removed type hint, check type below
        # --- Log Raw AI Output (or validated/parsed output if raw isn't directly available) ---
        logger.info(f"Raw AI output received (pre-validation):\n```\n{result}\n```")
        # --- END MODIFIED CALL ---

        # --- Check result type, unwrap if necessary, and update original transactions ---
        processed_data = None
        if isinstance(result, AgentRunResult):
            logger.info("AI Agent returned AgentRunResult, extracting data.")
            processed_data = result.data # Extract the actual data
        else:
            logger.info("AI Agent returned result directly.")
            processed_data = result # Use the result as is

        if isinstance(processed_data, AIProcessedBatch):
            logger.info(f"Successfully obtained AIProcessedBatch with {len(processed_data.processed_transactions)} transactions.")

            # --- Update original transactions using original_index from AI results ---
            if len(processed_data.processed_transactions) != len(raw_transactions):
                logger.warning(f"Mismatch in transaction count! Input: {len(raw_transactions)}, AI Output: {len(processed_data.processed_transactions)}. Will update based on 'original_index' provided by AI.")
            
            updated_count = 0
            updated_indices = set() # Keep track of which original transactions were updated

            for ai_txn in processed_data.processed_transactions:
                try:
                    original_idx = ai_txn.original_index
                    if 0 <= original_idx < len(raw_transactions):
                        original_txn = raw_transactions[original_idx]

                        # Update fields in the original Transaction object
                        original_txn.category = Category.from_string(ai_txn.category_str) # Use imported Category
                        original_txn.is_expense = ai_txn.is_expense # Now an int (0 or 1)
                        original_txn.is_split = ai_txn.is_split     # Now an int (0, 1, or 2)
                        
                        updated_indices.add(original_idx)
                        updated_count += 1
                    else:
                        logger.warning(f"AI result provided an invalid original_index ({original_idx}) which is out of bounds for the input list (size {len(raw_transactions)}). Skipping this AI result.")
                except AttributeError:
                     logger.warning(f"AI result object missing 'original_index'. Skipping this AI result: {ai_txn}")
                except Exception as update_err:
                     logger.error(f"Error updating transaction at index {original_idx} with AI data ({ai_txn}): {update_err}")

            logger.info(f"Successfully processed {len(processed_data.processed_transactions)} AI results and updated {updated_count} original transactions.")
            
            # Log which original transactions were NOT updated
            not_updated_count = 0
            for i, txn in enumerate(raw_transactions):
                if i not in updated_indices:
                    logger.warning(f"Original transaction at index {i} (Desc: '{txn.description}') was NOT updated by the AI results.")
                    not_updated_count += 1
            if not_updated_count > 0:
                 logger.warning(f"Total {not_updated_count} original transactions were not updated.")

            ai_processed_successfully = True # Mark as successful
        else:
            # Handle unexpected result type (after potential unwrapping)
            logger.error(f"AI processing resulted in an unexpected data type: {type(processed_data)}. Expected AIProcessedBatch. Original result type was: {type(result)}. Data: {processed_data}")
            ai_processed_successfully = False # Ensure failure handling is triggered
    except ValidationError as ve:
         logger.error(f"AI output validation error: {ve}. Could not parse AI response into AIProcessedBatch.")
         validation_error_occurred = True # Mark validation error

    except Exception as e:
        logger.error(f"Error during AI Agent batch processing: {e}")
        processing_error_occurred = True # Mark general processing error

    # --- Handle AI Processing Failure (Assign Defaults) ---
    if not ai_processed_successfully:
        error_message = "AI Processing Error" # Default error message
        if validation_error_occurred:
            error_message = "AI Validation Error"
        elif processing_error_occurred:
             error_message = "AI Processing Error" # Could use the specific exception 'e' here if needed

        logger.warning(f"{error_message} occurred. Assigning default values to all transactions in the batch.")
        for txn in raw_transactions:
            # Overwrite fields with error defaults
            # txn.short_description = error_message # Keep original description
            txn.category = DEFAULT_CATEGORY_ENUM # Use imported default
            txn.is_expense = 1 # Default assumption on error: expense (1)
            txn.is_split = 0 # Default assumption on error: not split (0)

    logger.info(f"Finished AI processing for the batch.")
    return raw_transactions # Return the original list, now potentially enriched or with defaults


# Removed EXCLUDED_CATEGORIES set as filtering is now based on is_expense boolean

def filter_expenses(enriched_transactions: List[Transaction]) -> List[Transaction]:
    """
    Filters a list of AI-enriched transactions, keeping only those marked as expenses.

    Args:
        enriched_transactions (List[Transaction]): A list of Transaction objects that have been
                                                   processed by `process_transactions_ai`,
                                                   expected to have the 'is_expense' boolean attribute populated.

    Returns:
        List[Transaction]: A new list containing only the transactions where `is_expense` is True.
    """
    expense_transactions = []
    if not enriched_transactions:
        return []

    logger.info(f"Filtering expenses from {len(enriched_transactions)} enriched transactions based on 'is_expense' flag...")
    for transaction in enriched_transactions:
        # Check the is_expense flag populated by the AI
        # Default to 1 (expense) if flag is missing (conservative approach for expenses)
        is_expense_flag = getattr(transaction, 'is_expense', 1) # Safely access attribute, default to 1

        if is_expense_flag is None:
             # Use original description for logging now
             logger.warning(f"Transaction description '{getattr(transaction, 'description', 'N/A')}' has is_expense=None. Assuming it IS an expense.")
             is_expense_flag = 1 # Treat None as expense

        if is_expense_flag == 1: # Keep if 1 (expense)
            expense_transactions.append(transaction)

    logger.info(f"Filtered down to {len(expense_transactions)} expense transactions.")
    return expense_transactions

# (Optional: Keep commented-out example usage if helpful for testing)
# if __name__ == '__main__':
#     # Example usage would require setting up mock Transaction objects
#     # and ensuring GEMINI_API_KEY and the prompt file are available and CORRECTLY FORMATTED.
#     pass