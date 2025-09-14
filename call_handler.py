import os
from twilio.rest import Client as TwilioRestClient # Renamed for clarity in this file
import asyncio
from openai import OpenAI
from app import omnidim_client # Import OmniDimension client and config
# Load environment variables
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
# Removed TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER") # This is the Twilio Voice enabled number
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
OMNIDIM_AGENT_ID= os.getenv("OMNIDIM_AGENT_ID")
OMNIDIM_FROM_NUMBER =  os.getenv("OMNIDIM_FROM_NUMBER")
# Removed OMNIDIM_API_KEY = os.getenv("OMNIDIM_API_KEY")
# Removed OMNIDIM_FROM_NUMBER_ID = os.getenv("OMNIDIM_FROM_NUMBER_ID") # New: OmniDimension 'from' number ID for outbound calls

twilio_rest_client = TwilioRestClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
openai_client = OpenAI()
# Removed omnidimension_client = OmniDimensionClient(OMNIDIM_API_KEY)

# --- Mock Data and Helper Functions (for now, will be replaced with proper imports/db fetches) ---
SUPPLIERS = {
    "Supplier A": {
        "phone": "+919971129359",
        "items": {
            "rice": {"price_per_unit": 45.0, "unit": "kg"},
            "flour": {"price_per_unit": 30.0, "unit": "kg"},
        }
    },
    "Supplier B": {
        "phone": "+919971129359",
        "items": {
            "rice": {"price_per_unit": 47.0, "unit": "kg"},
            "flour": {"price_per_unit": 28.0, "unit": "kg"},
        }
    },
}

MESSAGES = {
    "en": {
        "order_confirmed_shopkeeper": "✅ Order for {quantity} {unit} of {item_name} from {supplier_name} confirmed and stock updated. Expected delivery in 2 days.",
        "order_failed_shopkeeper": "❌ Failed to confirm order for {item_name} from {supplier_name}. Reason: {reason}"
    },
    "hi": {
        "order_confirmed_shopkeeper": "✅ {item_name} के {quantity} {unit} का {supplier_name} से ऑर्डर पुष्ट हो गया है। स्टॉक अपडेट कर दिया गया है। डिलीवरी 2 दिनों में अपेक्षित है।",
        "order_failed_shopkeeper": "❌ Failed to confirm order for {item_name} from {supplier_name}. Reason: {reason}"
    }
}

async def send_whatsapp_message(to_number: str, message_body: str):
    try:
        print(f"DEBUG: Attempting to send WhatsApp message to {to_number} from {TWILIO_WHATSAPP_NUMBER}. Message: {message_body}")
        message = await asyncio.to_thread(
            twilio_rest_client.messages.create,
            to=to_number,
            from_=TWILIO_WHATSAPP_NUMBER,
            body=message_body
        )
        print(f"DEBUG: WhatsApp message sent successfully. SID: {message.sid}")
    except Exception as e:
        print(f"ERROR: Failed to send WhatsApp message to {to_number}: {e}")

# This will now be handled by app.py's MESSAGES dictionary
# def get_message(lang: str, key: str, **kwargs) -> str:
#     # Placeholder for message retrieval. In a real scenario, this would come from app.py MESSAGES dictionary.
#     messages = {
#         "en": {
#             "call_initiated": "Initiating call to {supplier_name} at {supplier_phone_number} for {quantity} {unit} of {item_name}. I will notify you once the order is confirmed.",
#             "order_confirmed_shopkeeper": "✅ Order for {quantity} {unit} of {item_name} from {supplier_name} confirmed and stock updated. Expected delivery in 2 days.",
#             "order_failed_shopkeeper": "❌ Failed to confirm order for {item_name} from {supplier_name}. Reason: {reason}"
#         }
#     }
#     return messages.get(lang, messages["en"]).get(key, f"Missing message for {key}").format(**kwargs)

# --- End Mock Data and Helper Functions ---

# Add this debugging function to your code to test the OmniDimension API connection

async def debug_omnidimension_connection():
    """Debug function to test OmniDimension API connection and agent access."""
    try:
        print(f"DEBUG: Testing OmniDimension connection...")
        print(f"DEBUG: Using Agent ID: {OMNIDIM_AGENT_ID}")
        print(f"DEBUG: Using From Number ID: {OMNIDIM_FROM_NUMBER}")
        
        # First, try to list available agents to verify API access
        try:
            agents = await asyncio.to_thread(omnidim_client.agent.list_agents)
            print(f"DEBUG: Available agents: {agents}")
            
            # Check if our agent ID exists in the list
            agent_found = any(agent.id == int(OMNIDIM_AGENT_ID) for agent in agents)
            print(f"DEBUG: Agent ID {OMNIDIM_AGENT_ID} found: {agent_found}")
            
        except Exception as e:
            print(f"ERROR: Failed to list agents: {e}")
            return False
        
        # Try to get specific agent details
        try:
            agent_details = await asyncio.to_thread(
                omnidim_client.agent.get_agent,
                agent_id=int(OMNIDIM_AGENT_ID)
            )
            print(f"DEBUG: Agent details: {agent_details}")
            
        except Exception as e:
            print(f"ERROR: Failed to get agent details: {e}")
            return False
        
        # Test a simple API call (like listing phone numbers)
        try:
            phone_numbers = await asyncio.to_thread(omnidim_client.phone_number.list_phone_numbers)
            print(f"DEBUG: Available phone numbers: {phone_numbers}")
            
            # Check if our from number exists
            from_number_found = any(phone.id == int(OMNIDIM_FROM_NUMBER) for phone in phone_numbers)
            print(f"DEBUG: From Number ID {OMNIDIM_FROM_NUMBER} found: {from_number_found}")
            
        except Exception as e:
            print(f"ERROR: Failed to list phone numbers: {e}")
            return False
            
        return True
        
    except Exception as e:
        print(f"ERROR: General OmniDimension API error: {e}")
        return False

# Modified version of your initiate_outbound_call function with better error handling
# Add this debugging function to your code to test the OmniDimension API connection

async def debug_omnidimension_connection():
    """Debug function to test OmniDimension API connection and agent access."""
    try:
        print(f"DEBUG: Testing OmniDimension connection...")
        print(f"DEBUG: Using Agent ID: {OMNIDIM_AGENT_ID}")
        print(f"DEBUG: Using From Number ID: {OMNIDIM_FROM_NUMBER}")
        
        # First, try to list available agents to verify API access
        try:
            agents = await asyncio.to_thread(omnidim_client.agent.list_agents)
            print(f"DEBUG: Available agents: {agents}")
            
            # Check if our agent ID exists in the list
            agent_found = any(agent.id == int(OMNIDIM_AGENT_ID) for agent in agents)
            print(f"DEBUG: Agent ID {OMNIDIM_AGENT_ID} found: {agent_found}")
            
        except Exception as e:
            print(f"ERROR: Failed to list agents: {e}")
            return False
        
        # Try to get specific agent details
        try:
            agent_details = await asyncio.to_thread(
                omnidim_client.agent.get_agent,
                agent_id=int(OMNIDIM_AGENT_ID)
            )
            print(f"DEBUG: Agent details: {agent_details}")
            
        except Exception as e:
            print(f"ERROR: Failed to get agent details: {e}")
            return False
        
        # Test a simple API call (like listing phone numbers)
        try:
            phone_numbers = await asyncio.to_thread(omnidim_client.phone_number.list_phone_numbers)
            print(f"DEBUG: Available phone numbers: {phone_numbers}")
            
            # Check if our from number exists
            from_number_found = any(phone.id == int(OMNIDIM_FROM_NUMBER) for phone in phone_numbers)
            print(f"DEBUG: From Number ID {OMNIDIM_FROM_NUMBER} found: {from_number_found}")
            
        except Exception as e:
            print(f"ERROR: Failed to list phone numbers: {e}")
            return False
            
        return True
        
    except Exception as e:
        print(f"ERROR: General OmniDimension API error: {e}")
        return False

# Modified version of your initiate_outbound_call function with better error handling
import os
import asyncio
from app import omnidim_client, OMNIDIM_AGENT_ID, OMNIDIM_FROM_NUMBER_ID

# --- Main Functions ---

async def initiate_outbound_call(to_number: str, order_details: str, supplier_name: str, user_id: str) -> bool:
    """Initiates an outbound call to a supplier with a list of items."""
    
    # --- Step 1: Validate environment variables before use ---
    agent_id_str = os.getenv("OMNIDIM_AGENT_ID")
    from_number_id_str = os.getenv("OMNIDIM_FROM_NUMBER")

    if not agent_id_str or not from_number_id_str:
        print("ERROR_CALL: Missing OMNIDIM_AGENT_ID or OMNIDIM_FROM_NUMBER_ID in .env file.")
        return False

    try:
        print(f"DEBUG_CALL: Initiating outbound call to {to_number} using OmniDimension.")
        
        context_for_call = {
            "order_details": order_details,
            "supplier_name": supplier_name,
            "user_id": user_id,
        }
        
        print(f"DEBUG_CALL: Call context: {context_for_call}")

        call_response = await asyncio.to_thread(
            omnidim_client.call.dispatch_call,
            agent_id=int(agent_id_str),
            to_number=to_number,
            from_number_id=int(from_number_id_str),
            call_context=context_for_call
        )
        
        print(f"DEBUG_CALL: Raw call response: {call_response}")

        if isinstance(call_response, dict) and call_response.get('json', {}).get('success'):
            request_id = call_response.get('json', {}).get('requestId', 'unknown')
            print(f"DEBUG_CALL: OmniDimension Call dispatched successfully. Request ID: {request_id}")
            return True
        else:
            print(f"ERROR_CALL: Call dispatch failed or returned unexpected format. Response: {call_response}")
            return False
            
    except Exception as e:
        print(f"ERROR_CALL: Failed to initiate outbound call via OmniDimension to {to_number}")
        print(f"ERROR_CALL: Error details: {repr(e)}")
        return False

# Environment variables validation function
def validate_environment_variables():
    """Validate all required environment variables are set."""
    required_vars = [
        'OMNIDIM_API_KEY',
        'OMNIDIM_AGENT_ID', 
        'OMNIDIM_FROM_NUMBER',
        'TWILIO_ACCOUNT_SID',
        'TWILIO_AUTH_TOKEN',
        'TWILIO_WHATSAPP_NUMBER'
    ]
    
    missing_vars = []
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        print(f"ERROR: Missing required environment variables: {missing_vars}")
        return False
    
    print("DEBUG: All required environment variables are set")
    return True

# The following functions related to local call handling are no longer needed
# as OmniDimension will manage the conversation entirely.

# Removed @call_bp.route("/omnidim_callback", methods=["POST", "GET"])
# Removed async def omnidim_callback(): and its content as it's now in app.py
# The following functions related to local call handling are no longer needed
# as OmniDimension will manage the conversation entirely.
# async def generate_speech_from_text(text: str) -> str:
#     pass
# async def transcribe_speech_from_url(audio_url: str) -> str:
#     pass
# call_states = {} # No longer managing call state locally
# @call_bp.route("/audio/<filename>")
# async def serve_audio(filename):
#     pass
# @call_bp.route("/voice", methods=['POST'])
# async def voice_webhook():
#     pass
# @call_bp.route("/call/handle_input", methods=['POST'])
# async def handle_call_input():
#     pass
