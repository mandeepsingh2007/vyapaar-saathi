import os
from twilio.rest import Client as TwilioRestClient # Renamed for clarity in this file
import asyncio
from openai import OpenAI
from flask import request
from supabase_client import save_order_confirmation, update_stock_item, save_transaction
from fuzzywuzzy import fuzz
# Removed from omnidimension import Client as OmniDimensionClient
from datetime import date
import urllib.parse # For URL encoding call context

# Load environment variables
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER") # This is the Twilio Voice enabled number
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
# Removed OMNIDIM_API_KEY = os.getenv("OMNIDIM_API_KEY")
# Removed OMNIDIM_FROM_NUMBER_ID = os.getenv("OMNIDIM_FROM_NUMBER_ID") # New: OmniDimension 'from' number ID for outbound calls

twilio_rest_client = TwilioRestClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
openai_client = OpenAI()
# Removed omnidimension_client = OmniDimensionClient(OMNIDIM_API_KEY)

# Removed OMNIDIM_AGENT_ID = 31765  # Your OmniDimension Agent ID

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

async def initiate_outbound_call(to_number: str, item_name: str, quantity: float, unit: str, supplier_name: str, user_id: str, detected_language: str) -> bool:
    """Initiates an outbound call to a supplier using Twilio Voice.

    Args:
        to_number: The supplier's phone number in E.164 format.
        item_name: The name of the item to order.
        quantity: The quantity of the item to order.
        unit: The unit of the item (e.g., "kg", "pcs").
        supplier_name: The name of the supplier.
        user_id: The WhatsApp ID of the shopkeeper to notify.
        detected_language: The language detected for the user.

    Returns:
        True if the call was initiated successfully, False otherwise.
    """
    try:
        print(f"DEBUG_CALL: Initiating outbound call to {to_number} using Twilio Voice.")
        
        # Prepare call context to be passed to the Twilio Voice webhook
        call_context_params = {
            "item_name": item_name,
            "quantity": str(quantity),
            "unit": unit,
            "supplier_name": supplier_name,
            "user_id": user_id,
            "detected_language": detected_language
        }
        # Encode context as URL parameters
        encoded_context = urllib.parse.urlencode(call_context_params)

        # The URL Twilio will request when the call connects
        # This needs to be your ngrok URL + the new webhook endpoint
        # For now, we'll use a placeholder. You'll need to provide your ngrok URL.
        # IMPORTANT: Replace `YOUR_NGROK_URL` with your actual ngrok public URL.
        # Example: twilio_voice_webhook_url = f"https://your-ngrok-subdomain.ngrok.io/twilio_voice_webhook?{encoded_context}"
        twilio_voice_webhook_url = f"https://523af0195609.ngrok-free.app/twilio_voice_webhook?{encoded_context}"

        call = await asyncio.to_thread(
            twilio_rest_client.calls.create,
            to=to_number,
            from_=TWILIO_PHONE_NUMBER, # Use the Twilio Voice enabled number
            url=twilio_voice_webhook_url # Webhook for Twilio to get TwiML instructions
        )

        print(f"DEBUG_CALL: Twilio Call initiated successfully. Call SID: {call.sid}")
        return True
            
    except Exception as e:
        print(f"ERROR_CALL: Failed to initiate outbound call via Twilio to {to_number}: {e}")
        return False

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
