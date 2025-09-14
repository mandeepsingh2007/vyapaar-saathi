import asyncio
import copy
from datetime import date
import logging
import os
import re
from threading import Thread
from multiprocessing import Manager
import json

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from flask import Flask, request, send_from_directory
from fuzzywuzzy import fuzz
from openai import OpenAI
from twilio.twiml.messaging_response import MessagingResponse
import twilio.rest
import omnidimension

from data_extractor import (
    extract_items_from_bill_image,
    extract_structured_data,
    transcribe_audio,
)
from supabase_client import (
    get_daily_sales_summary,
    get_low_stock_items,
    get_stock_levels,
    get_user_transactions_summary,
    save_transaction,
    supabase,
    update_stock_item,
    get_all_unique_user_ids_with_stock,
)
from weather_events_api import get_weather_forecast, get_festivals_from_llm

# FFmpeg path configuration
ffmpeg_bin_path = r"C:\Users\singh\Downloads\ffmpeg-8.0-essentials_build\ffmpeg-8.0-essentials_build\bin"
if ffmpeg_bin_path not in os.environ["PATH"]:
    os.environ["PATH"] += os.pathsep + ffmpeg_bin_path
    print(f"DEBUG: Added {ffmpeg_bin_path} to PATH for Flask app.")

load_dotenv()

# !! IMPORTANT !!
# Replace this with your actual public URL (e.g., from ngrok or your deployed server)
BASE_URL = "https://6eab3abb0f24.ngrok-free.app"

SHOPKEEPER_LOCATION = {"latitude": 28.7041, "longitude": 77.1025} # Default to Delhi, India
DEFAULT_LANGUAGE = "hi" # Define default language

OMNIDIM_FROM_NUMBER = os.getenv("OMNIDIM_FROM_NUMBER") # OmniDimension 'from' number for outbound calls
OMNIDIM_API_KEY = os.getenv("OMNIDIM_API_KEY")
OMNIDIM_FROM_NUMBER_ID = os.getenv("OMNIDIM_AGENT_ID")

client = OpenAI() # Re-initialize OpenAI client
omnidim_client = omnidimension.Client(OMNIDIM_API_KEY)
# Removed Google Generative AI client configuration
# genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
# gemini_model = genai.GenerativeModel("gemini-2.5-pro") # Removed Gemini model initialization

app = Flask(__name__)
scheduler = AsyncIOScheduler() # Initialize scheduler here

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER") # Twilio Voice enabled number
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OMNIDIM_AGENT_ID = os.getenv("OMNIDIM_AGENT_ID") 
OMNIDIM_AGENT_ID = os.getenv("OMNIDIM_FROM_NUMBER")# OmniDimension Agent ID

twilio_client = twilio.rest.Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)




# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

language_map = {"en": "en", "english": "en", "hi": "hi", "hindi": "hi",
                "pa": "pa", "punjabi": "pa", "gu": "gu", "gujarati": "gu",
                "ta": "ta", "tamil": "ta", "te": "te", "telugu": "te",
                "bn": "bn", "bengali": "bn", "mr": "mr", "marathi": "mr",
                "ur": "hi", "urdu": "hi"}

MESSAGES = {
    "en": {
        "sale_success": "✅ Sale of ₹{amount:.2f} recorded for:\n{item_details}",
        "sale_success_no_item": "✅ Sale of ₹{amount:.2f} added to your records.",
        "expense_success": "✅ Expense of ₹{amount} ({item}) recorded.",
        "expense_success_no_item": "✅ Expense of ₹{amount} recorded.",
        "balance_inquiry": "📊 Your current digital khata balance is ₹{balance:.2f}.\n\nRecent Transactions:\n{transactions_summary}",
        "earnings_summary": "📈 Today's total sales: ₹{total_sales:.2f}.\n\nToday's transactions:\n{sales_details}",
        "extract_fail": "Could not extract structured data from your message. Please be more specific (e.g., 'Sold 1kg sugar for 50 rupees').",
        "transcribe_fail": "Could not understand your voice note. The audio quality might be poor or not in a supported language.",
        "download_fail": "Failed to download your voice note: {error_msg}. Please try again.",
        "network_error": "A network error occurred while downloading your voice note: {error_msg}. Please try again.",
        "file_error": "There was an issue with the voice note file: {error_msg}. Please try sending it again.",
        "unexpected_error": "An unexpected error occurred while processing your voice note. Please try again.",
        "no_voice_note": "Please send a voice note for transaction processing or a text message for balance inquiry.",
        "unsupported_media": "Unsupported media type: {media_type}. Please send a voice note or an image.",
        "no_transactions_found": "No recent transactions found.",
        "no_sales_found_today": "No sales found today.",
        "image_received_stock_update": "Image received! Processing for stock update...",
        "stock_update_success": "✅ Stock updated successfully for: {updates}.",
        "stock_update_fail": "❌ Failed to update stock from image. Error: {error_msg}",
        "file_download_error": "Failed to download your image: {error_msg}. Please try again.",
        "welcome": "Hello! I'm your inventory management bot. How can I help you?",
        "stock_updated": "Stock for {item_name} updated successfully. Current quantity: {current_quantity} {unit}.",
        "sale_recorded": "Sale of {quantity} {unit} of {item_name} recorded. Remaining stock: {current_quantity} {unit}. Total sale amount: {selling_amount}. Profit: {profit}.",
        "purchase_recorded": "Purchase of {quantity} {unit} of {item_name} recorded. New stock: {current_quantity} {unit}. Cost: {cost_price_per_unit} per {unit}.",
        "item_not_found": "'{item_name}' not found in stock. Would you like to add it?",
        "unknown_command": "I didn't understand that. Please try again or type 'help'.",
        "current_stock": "Current stock for {item_name}: {current_quantity} {unit}.",
        "all_stock_items": "Your current stock:\n{stock_list}",
        "daily_summary": "Daily Sales Summary for {date}:\nTotal Sales: {total_sales}\nTotal Profit: {total_profit}\n{sales_details}",
        "low_stock_alert": "⚠️ Low stock alert! The following items are running low:\n{low_stock_items_list}\nPlease consider ordering soon.",
        "call_initiated": "Initiating call to {supplier_name} at {supplier_phone_number} for {quantity} {unit} of {item_name}. I will notify you once the order is confirmed.",
        "order_confirmation_prompt": "Which item and supplier would you like to confirm an order for? Example: 'Order 10 kg rice from Supplier A'.",
        "order_confirmed_shopkeeper": "✅ Order for {quantity} {unit} of {item_name} from {supplier_name} confirmed. Expect delivery in 2 days.",
        "order_failed_shopkeeper": "❌ Failed to confirm order for {item_name} from {supplier_name}. Reason: {reason}"
    },
    "hi": {
        "sale_success": "✅ ₹{amount:.2f} की बिक्री दर्ज की गई:\n{item_details}",
        "sale_success_no_item": "✅ आपके डिजिटल खाते में ₹{amount:.2f} की बिक्री दर्ज की गई।",
        "expense_success": "✅ आपके डिजिटल खाते में ₹{amount} ({item}) का खर्च दर्ज किया गया।",
        "expense_success_no_item": "✅ आपके डिजिटल खाते में ₹{amount} का खर्च दर्ज किया गया।",
        "balance_inquiry": "📊 आपके डिजिटल खाते में वर्तमान शेष राशि ₹{balance:.2f} है।\n\nपिछले लेनदेन:\n{transactions_summary}",
        "earnings_summary": "📈 आज की कुल बिक्री: ₹{total_sales:.2f}।\n\nआज के लेनदेन:\n{sales_details}",
        "extract_fail": "आपके संदेश से जानकारी नहीं निकाली जा सकी। कृपया अधिक विशिष्ट रहें (उदाहरण के लिए, '50 रुपये में 1 किलो चीनी बेची')।",
        "transcribe_fail": "आपके वॉइस नोट को समझा नहीं जा सका। ऑडियो गुणवत्ता खराब हो सकती है या यह समर्थित भाषा में नहीं है।",
        "download_fail": "आपका वॉइस नोट डाउनलोड नहीं हो सका: {error_msg}। कृपया पुनः प्रयास करें।",
        "network_error": "आपका वॉइस नोट डाउनलोड करते समय नेटवर्क त्रुटि हुई: {error_msg}। कृपया पुनः प्रयास करें।",
        "file_error": "वॉइस नोट फ़ाइल में समस्या थी: {error_msg}। कृपया इसे फिर से भेजें।",
        "unexpected_error": "एक अप्रत्याशित त्रुटि हुई। कृपया पुनः प्रयास करें।",
        "no_voice_note": "कृपया लेनदेन के लिए एक वॉइस नोट भेजें या शेष राशि जानने के लिए टेक्स्ट मैसेज करें।",
        "unsupported_media": "असमर्थित मीडिया प्रकार: {media_type}। कृपया एक वॉइस नोट या एक इमेज भेजें।",
        "no_transactions_found": "कोई हालिया लेनदेन नहीं मिला।",
        "no_sales_found_today": "आज कोई बिक्री दर्ज नहीं की गई।",
        "image_received_stock_update": "छवि प्राप्त हुई! स्टॉक अपडेट के लिए प्रसंस्करण हो रहा है...",
        "stock_update_success": "✅ स्टॉक सफलतापूर्वक अपडेट किया गया: {updates}.",
        "stock_update_fail": "❌ छवि से स्टॉक अपडेट करने में विफल। त्रुटि: {error_msg}",
        "file_download_error": "आपकी छवि डाउनलोड नहीं हो सकी: {error_msg}। कृपया पुनः प्रयास करें।",
        "welcome": "नमस्ते! मैं आपकी इन्वेंट्री प्रबंधन बॉट हूँ। मैं आपकी कैसे मदद कर सकती हूँ?",
        "stock_updated": "{item_name} का स्टॉक सफलतापूर्वक अपडेट किया गया। वर्तमान मात्रा: {current_quantity} {unit}.",
        "sale_recorded": "{quantity} {unit} {item_name} की बिक्री दर्ज की गई। शेष स्टॉक: {current_quantity} {unit}. कुल बिक्री राशि: {selling_amount}. लाभ: {profit}.",
        "purchase_recorded": "{quantity} {unit} {item_name} की खरीद दर्ज की गई। नया स्टॉक: {current_quantity} {unit}. लागत: {cost_price_per_unit} प्रति {unit}.",
        "item_not_found": "'{item_name}' आइटम स्टॉक में नहीं मिला। क्या आप इसे जोड़ना चाहेंगे?",
        "unknown_command": "मुझे समझ नहीं आया। कृपया पुनः प्रयास करें या 'सहायता' टाइप करें।",
        "current_stock": "{item_name} का वर्तमान स्टॉक: {current_quantity} {unit}.",
        "all_stock_items": "आपका वर्तमान स्टॉक:\n{stock_list}",
        "daily_summary": "{date} के लिए दैनिक बिक्री सारांश:\nकुल बिक्री: {total_sales}\nकुल लाभ: {total_profit}\n{sales_details}",
        "low_stock_alert": "⚠️ कम स्टॉक चेतावनी! निम्नलिखित आइटम कम हो रहे हैं:\n{low_stock_items_list}\nजल्द ही ऑर्डर करने पर विचार करें।",
        "call_initiated": "नमस्ते, मैं गुप्ता किराना स्टोर से रमा बात कर रही हूँ। क्या मैं {supplier_name} से बात कर सकती हूँ?",
        "order_confirmation_prompt": "आप किस आइटम और आपूर्तिकर्ता के लिए ऑर्डर की पुष्टि करना चाहेंगे? उदाहरण: 'सप्लायर ए से 10 किलो चावल ऑर्डर करें'।",
        "order_confirmed_shopkeeper": "✅ {item_name} के {quantity} {unit} का {supplier_name} से ऑर्डर पुष्ट हो गया है। स्टॉक अपडेट कर दिया गया है। डिलीवरी 2 दिनों में अपेक्षित है।",
        "order_failed_shopkeeper": "❌ Failed to confirm order for {item_name} from {supplier_name}. Reason: {reason}"
    },
    "pa": { # Punjabi messages
        "sale_success": "✅ ਤੁਹਾਡੇ ਡਿਜੀਟਲ ਖਾਤੇ ਵਿੱਚ ₹{amount:.2f} ({item}) ਦੀ ਵਿਕਰੀ ਦਰਜ ਕੀਤੀ ਗਈ।",
        "sale_success_no_item": "✅ ਤੁਹਾਡੇ ਡਿਜੀਟਲ ਖਾਤੇ ਵਿੱਚ ₹{amount:.2f} ਦੀ ਵਿਕਰੀ ਦਰਜ ਕੀਤੀ ਗਈ।",
        "expense_success": "✅ ਤੁਹਾਡੇ ਡਿਜੀਟਲ ਖਾਤੇ ਵਿੱਚ ₹{amount} ({item}) ਦਾ ਖਰਚ ਦਰਜ ਕੀਤਾ ਗਿਆ।",
        "expense_success_no_item": "✅ ਤੁਹਾਡੇ ਡਿਜੀਟਲ ਖਾਤੇ ਵਿੱਚ ₹{amount} ਦਾ ਖਰਚ ਦਰਜ ਕੀਤਾ ਗਿਆ।",
        "balance_inquiry": "📊 ਤੁਹਾਡੇ ਡਿਜੀਟਲ ਖਾਤੇ ਵਿੱਚ ਵਰਤਮਾਨ ਬਕਾਇਆ ₹{balance:.2f} ਹੈ।\n\nਪਿਛਲੇ ਲੈਣ-ਦੇਣ:\n{transactions_summary}",
        "extract_fail": "ਤੁਹਾਡੇ ਸੁਨੇਹੇ ਤੋਂ ਜਾਣਕਾਰੀ ਨਹੀਂ ਕੱਢੀ ਜਾ ਸਕੀ। ਕਿਰਪਾ ਕਰਕੇ ਸਪਸ਼ਟ ਬੋਲੋ।",
        "transcribe_fail": "ਤੁਹਾਡੇ ਵੌਇਸ ਨੋਟ ਨੂੰ ਟ੍ਰਾਂਸਕ੍ਰਾਈਬ ਜਾਂ ਅਨੁਵਾਦ ਨਹੀਂ ਕੀਤਾ ਜਾ ਸਕਿਆ। ਆਡੀਓ ਗੁਣਵੱਤਾ ਖਰਾਬ ਹੋ ਸਕਦੀ ਹੈ।",
        "download_fail": "ਤੁਹਾਡਾ ਵੌਇਸ ਨੋਟ ਡਾਊਨਲੋਡ ਨਹੀਂ ਹੋ ਸਕਿਆ: {error_msg}। ਕਿਰਪਾ ਕਰਕੇ ਦੁਬਾਰਾ ਕੋਸ਼ਿਸ਼ ਕਰੋ।",
        "network_error": "ਤੁਹਾਡਾ ਵੌਇਸ नोट डाउनलोड करते समय नेटवर्क त्रुटि हुई: {error_msg}। कृपया पुनः प्रयास करें।",
        "file_error": "ਵੌਇਸ ਨੋਟ ਫਾਈਲ ਵਿੱਚ ਸਮੱਸਿਆ ਸੀ: {error_msg}। ਕਿਰਪਾ ਕਰਕੇ ਇਸਨੂੰ ਫਿਰ ਤੋਂ ਭੇਜੋ।",
        "unexpected_error": "ਤੁਹਾਡੇ ਵੌਇਸ ਨੋਟ ਨੂੰ ਪ੍ਰੋਸੈਸ ਕਰਦੇ ਸਮੇਂ ਇੱਕ ਅਣਕਿਆਸੀ ਗਲਤੀ ਹੋਈ। ਕਿਰਪਾ ਕਰਕੇ ਦੁਬਾਰਾ ਕੋਸ਼ਿਸ਼ ਕਰੋ।",
        "no_voice_note": "ਕਿਰਪਾ ਕਰਕੇ ਟ੍ਰਾਂਜੈਕਸ਼ਨ ਪ੍ਰੋਸੈਸਿੰਗ ਲਈ ਇੱਕ ਵੌਇਸ ਨੋਟ ਭੇਜੋ ਜਾਂ ਬਕਾਇਆ ਪੁੱਛਣ ਲਈ ਇੱਕ ਟੈਕਸਟ ਸੁਨੇਹਾ ਭੇਜੋ।",
        "unsupported_media": "ਅਸਮਰਥਿਤ ਮੀਡੀਆ ਕਿਸਮ: {media_type}। ਕਿਰਪਾ ਕਰਕੇ ਇੱਕ ਵੌਇਸ ਨੋਟ ਜਾਂ ਇੱਕ ਚਿੱਤਰ ਭੇਜੋ।",
        "no_transactions_found": "ਕੋਈ ਹਾਲੀਆ ਲੈਣ-ਦੇਣ ਨਹੀਂ ਮਿਲਿਆ।",
        "image_received_stock_update": "ਤਸਵੀਰ ਪ੍ਰਾਪਤ ਹੋਈ! ਸਟਾਕ ਅੱਪਡੇਟ ਲਈ ਪ੍ਰਕਿਰਿਆ ਕੀਤੀ ਜਾ ਰਹੀ ਹੈ...",
        "stock_update_success": "✅ ਸਟਾਕ ਸਫਲਤਾ੍ਪੂਰ੍ਵਕ ਅੱਪਡੇਟ ਕੀਤਾ ਗਿਆ: {updates}.",
        "stock_update_fail": "❌ ਤਸਵੀਰ ਤੋਂ ਸਟਾਕ ਅੱਪਡੇਟ ਕਰਨ ਵਿੱਚ ਅਸਫਲ। ਗਲਤੀ: {error_msg}",
        "file_download_error": "ਤੁਹਾਡੀ ਤਸਵੀਰ ਡਾਊਨਲੋਡ ਨਹੀਂ हੋ ਸਕੀ: {error_msg}। ਕਿਰਪਾ ਕਰਕੇ ਦੁਬਾਰਾ ਕੋਸ਼ਿਸ਼ ਕਰੋ।",
        "welcome": "ਸਤ ਸ੍ਰੀ ਅਕਾਲ! ਮੈਂ ਤੁਹਾਡਾ ਇਨਵੈਂਟਰੀ ਪ੍ਰਬੰਧਨ ਬੋਟ ਹਾਂ। ਮੈਂ ਤੁਹਾਡੀ ਕਿൽ ਮਦਦ ਕਰ ਸਕਦਾ ਹਾਂ?",
        "stock_updated": "{item_name} ਦਾ ਸਟਾਕ ਸਫਲਤਾਪੂਰਵਕ ਅੱਪਡੇਟ ਕੀਤਾ ਗਿਆ। ਮੌਜੂਦਾ ਮਾਤਰਾ: {current_quantity} {unit}.",
        "sale_recorded": "{quantity} {unit} {item_name} ਦੀ ਵਿਕਰੀ ਦਰਜ ਕੀਤੀ ਗਈ। ਬਾਕੀ ਸਟਾਕ: {current_quantity} {unit}. ਕੁੱਲ ਵਿਕਰੀ ਰਾਸ਼ੀ: {selling_amount}. ਲਾਭ: {profit}.",
        "purchase_recorded": "{quantity} {unit} {item_name} ਦੀ ਖਰੀਦ ਦਰਜ ਕੀਤੀ ਗਈ। ਨਵો ਸਟਾਕ: {current_quantity} {unit}. ਲਾਗਤ: {cost_price_per_unit} ਪ੍ਰਤੀ {unit}.",
        "item_not_found": "'{item_name}' ਸਟਾਕ ਵਿੱਚ नहीं मिलੀ. क्या आप इसे जोड़ना चाहेंगे?",
        "unknown_command": "ਮੈਨੂੰ ਸਮਝ ਨਹੀਂ ਆਇਆ। ਕਿਰਪਾ ਕਰਕੇ पुनः प्रयास करें या 'सहायता' टाइप करें।",
        "current_stock": "{item_name} ਦਾ ਮੌਜੂਦਾ ਸਟਾਕ: {current_quantity} {unit}.",
        "all_stock_items": "ਤੁਹਾਡਾ ਮੌਜੂਦਾ ਸਟਾਕ:\n{stock_list}",
        "daily_summary": "{date} ਲਈ ਰੋਜ਼ਾਨਾ ਵਿਕਰੀ ਸੰਖੇਪ:\nਕੁੱਲ ਵਿਕਰੀ: {total_sales}\nਕੁੱਲ ਲਾਭ: {total_profit}\n{sales_details}",
        "low_stock_alert": "⚠️ ਘੱਟ ਸਟਾਕ ਚੇਤਾਵਨੀ! ਹੇਠਾਂ ਦਿੱਤੀਆਂ ਚੀਜ਼ਾਂ ਘੱਟ ਹੋ ਰਹੀਆਂ ਹਨ:\n{low_stock_items_list}\nਜਲਦੀ ਹੀ ਆਰਡਰ ਕਰਨ ਬਾਰੇ ਵਿਚਾਰ ਕਰੋ।",
        "call_initiated": "{item_name} ਦੇ {quantity} {unit} ਲਈ {supplier_name} ({supplier_phone_number}) ਨੂੰ ਕਾਲ ਕੀਤਾ ਜਾ ਰਿਹਾ ਹੈ। ਆਰਡਰ ਦੀ ਪੁਸ਼ਟੀ ਹੋਣ 'ਤੇ ਮੈਂ ਤੁਹਾਨੂੰ सूचित करूंगा।",
        "order_confirmation_prompt": "आप किस आइटम और आपूर्तिकर्ता के लिए ऑर्डर की पुष्टि करना चाहेंगे? उदाहरण: 'सप्लायर ए से 10 किलो चावल ऑर्डर करें'।",
        "order_confirmed_shopkeeper": "✅ {item_name} के {quantity} {unit} का {supplier_name} से ऑर्डर पुष्ट हो गया है। स्टॉक अपडेट कर दिया गया है। डिलीवरी 2 दिनों में अपेक्षित है।",
        "order_failed_shopkeeper": "❌ Failed to confirm order for {item_name} from {supplier_name}. Reason: {reason}"
    },
    "gu": { # Gujarati messages
        "sale_success": "✅ તમારા ડિજિટલ ખાતામાં ₹{amount:.2f} ({item}) ની વેચાણ નોંધાઈ છે.",
        "sale_success_no_item": "✅ તમારા ડિજિટલ ખાતામાં ₹{amount:.2f} ની વેચાણ નોંધાઈ છે.",
        "expense_success": "✅ તમારા ડિજિટલ ખાતામાં ₹{amount} ({item}) નો ખર્ચ નોંધાયો છે.",
        "expense_success_no_item": "✅ તમારા ડિજિટલ ખાતામાં ₹{amount} નો ખર્ચ નોંધાયો છે.",
        "balance_inquiry": "📊 તમારા ડિજિટલ ખાતામાં વર્તમાન બેલેન્સ ₹{balance:.2f} છે।\n\nછેલ્લા વ્યવહારો:\n{transactions_summary}",
        "extract_fail": "તમારા વૉઇસ નોટમાંથી માહિતી કાઢી શકાઈ નથી. કૃપા કરીને સ્પષ્ટ બોલો.",
        "transcribe_fail": "તમારો વૉઇસ નોટ ટ્રાન્સક્રાઇબ અથવા અનુવાદ કરી શકાયું નથી. ઑડિયો ગુણવત્તા નબળી હોઈ શકે છે.",
        "download_fail": "તમારો વૉઇસ નોટ ડાઉનલોડ થઈ શક્યો નથી: {error_msg}. કૃપા કરીને ફરી પ્રયાસ કરો.",
        "network_error": "તમારો વૉઇસ નોટ ડાઉનલોડ કરતી વખતે નેટવर્ક ભૂલ થઈ: {error_msg}. કૃપા કરીને ફરી પ્રયાસ કરો.",
        "file_error": "વૉઇસ નૉટ ફાઇલમાં સમਸ્ય હતી: {error_msg}। કૃપા કરીને તેને ફરીથી મોકલો.",
        "unexpected_error": "તમારા વૉઇસ નોટ પર પ્રક્રિયા કરતી વખતે અણ્ધારી ભૂલ થઈ. કૃપા કરીને ફરી પ્રયાસ કરો.",
        "no_voice_note": "કૃપા કરીને વ્યવહાર પ્રક્રિયા માટે વૉઇસ નોટ મોકલો અથવા બેલેન્સ પૂછવા માટે ટેક્સ્ટ સંદેશ મોકલો.",
        "unsupported_media": "અસમર્થિત મીડિયા પ્રકાર: {media_type}। કૃપા કરીને એક વૉઇસ નોટ અથવા એક છબી મોકલો।",
        "no_transactions_found": "કોઈ તાજેતરના વ્યવહારો મળ્યા નથી।",
        "image_received_stock_update": "છબી પ્રાપ્ત થઈ! સ્ટોક અપડેટ માટે પ્રક્રિયા થઈ રહી છે...",
        "stock_update_success": "✅ સ્ટોક સફળતા્ਪੂૂર્વક અપડેટ થયો: {updates}.",
        "stock_update_fail": "❌ છબીમાંથી સ્ટોક અપડેટ કરવામાં નિષ્ફળ. ભૂલ: {error_msg}",
        "file_download_error": "તમારી છબી ડાઉનલોડ થઈ શકી નથી: {error_msg}. કૃપા કરીને ફરી પ્રયાસ કરો.",
        "welcome": "નમસ્તે! હું તમારી ઇન્વેન્ટ્રી મેનેજમેન્ટ બોટ છું. હું તમને કેવી રીતે મદદ કરી શકું?",
        "stock_updated": "{item_name} નો સ્ટોક સ્ફળતાપૂર્વક અપડેટ થયો. વર્તમાન જથ્થો: {current_quantity} {unit}.",
        "sale_recorded": "{quantity} {unit} {item_name} નું વેચાણ નોંધાઈ. બાકી સ્ટોક: {current_quantity} {unit}. કુલ વેચાણ રકમ: {selling_amount}. નફો: {profit}.",
        "purchase_recorded": "{quantity} {unit} {item_name} ની ખਰીદી નોંધાઈ. નવો સ્ટોક: {current_quantity} {unit}. ખર્ચ: {cost_price_per_unit} ਪ੍ਰਤੀ {unit}.",
        "item_not_found": "'{item_name}' સ્ટોક વਿੱਚ नहीं मिलੀ. क्या आप इसे जोड़ना चाहेंगे?",
        "unknown_command": "ਮੈਨੂੰ સਮઝ નਹੀં ਆયા. ਕਿਰਪਾ ਕਰਕੇ पुनः प्रयास करें या 'सहायता' टाइप करें।",
        "current_stock": "{item_name} દਾ ਮੌਜੂਦਾ ਸਟਾਕ: {current_quantity} {unit}.",
        "all_stock_items": "ਤੁਹਾਡਾ ਮੌਜੂਦਾ ਸਟਾਕ:\n{stock_list}",
        "daily_summary": "{date} ਲਈ ਰੋਜ਼ਾਨਾ ਵਿਕਰੀ ਸੰਖੇਪ:\nਕੁੱਲ ਵਿਕਰੀ: {total_sales}\nਕੁੱਲ ਲਾਭ: {total_profit}\n{sales_details}",
        "low_stock_alert": "⚠️ ਘੱਟ ਸਟਾਕ ਚੇਤਾਵਨੀ! ਹੇਠਾਂ ਦਿੱਤੀਆਂ ਚੀਜ਼ਾਂ ਘੱਟ ਹੋ ਰਹੀਆਂ ਹਨ:\n{low_stock_items_list}\nਜਲਦੀ ਹੀ ਆਰਡਰ ਕਰਨ ਬਾਰੇ ਵਿਚਾਰ ਕਰੋ।",
        "call_initiated": "{item_name} દੇ {quantity} {unit} લਈ {supplier_name} ({supplier_phone_number}) નੂં કਾલ કੀતા જા રਿહા હੈ। આરਡર દੀ ਪੁસ਼ટੀ ਹੋਣ 'ਤੇ ਮੈਂ ਤੁਹਾનੂੰ सूचित करूंगा।",
        "order_confirmation_prompt": "તમે કઈ વસ્તુ અને સપ્લાયર માટે ઓર્ડર કન્ફર્મ કરવા માંગો છો? ઉદાહરણ: 'સપ્લાયર A પાસેથી 10 કિલો ચોખાનો ઓર્ડર કરો'.",
        "order_confirmed_shopkeeper": "✅ {item_name} ના {quantity} {unit} માટે {supplier_name} પાસેથી ઓર્ડર કન્ફર્મ થયો છે. સ્ટોક અપડેટ કરવામાં આવ્યો છે. 2 દિવસમાં ડિલિવરી અપેક્ષિત છે.",
        "order_failed_shopkeeper": "❌ {item_name} ના {supplier_name} પાસેથી ઓર્ડર કન્ફર્મ કરવામાં નિષ્ફળ. કારણ: {reason}"
    },
    "te": { # Telugu messages
        "sale_success": "✅ మీ డిజిటల్ ఖాతాలో ₹{amount:.2f} ({item}) అమ్మకం జోడించబడింది.",
        "sale_success_no_item": "✅ మీ డిజిటల్ ఖాతాలో ₹{amount:.2f} అమ్మకం జోడించబడింది.",
        "expense_success": "✅ మీ డిజిటల్ ఖాతాలో ₹{amount} ({item}) ఖర్చు నమోదు చేయబడింది.",
        "expense_success_no_item": "✅ మీ డిజిటల్ ఖాతాలో ₹{amount} ఖర్చు నమోదు చేయబడింది.",
        "balance_inquiry": "📊 మీ డిజిటల్ ఖాతాలో ప్రస్తుత బ్యాలెన్స్ ₹{balance:.2f} ఉంది.\n\nతాజా లావాదేవీలు:\n{transactions_summary}",
        "extract_fail": "మీ వాయిస్ నోట్ నుండి డేటాను సేకరించలేము. దయచేసి స్పష్టంగా మాట్లాడండి.",
        "transcribe_fail": "మీ వాయిస్ నోట్‌ను లిప్యంతరీకరించడం లేదా అననువదించడం సాధయం కాలేదు. ఆడియో నాణ్యత తక్కువగా ఉండవచ్చు.",
        "download_fail": "మీ వాయిస్ నోట్‌ను డౌన్‌లోడ్ చేయడంలో విఫలమైంది: {error_msg}. దయచేసి మళ్లీ ప్రయత్నించండి.",
        "network_error": "మీ వాయిస్ నోట్‌ను డౌన్‌లోడ్ చేస్తున్నప్పుడు నెట్‌వర్క్ లోపం సంభవించింది: {error_msg}. దయచేసి మళ్లీ ప్రయత్నించండి.",
        "file_error": "వాయిస్ నోట్ ఫైల్‌లో సమస్య ఉంది: {error_msg}. దయచేసి దాన్ని మళ్లీ పంపండి.",
        "unexpected_error": "మీ వాయిస్ నోట్‌ను ప్రాసెస్ చేస్తున్నప్పుడు ఊహించని లోపం సంభవించింది. దయచేసి మళ్లీ ప్రయత్నించండి.",
        "no_voice_note": "దయచేసి లావాదేవీల ప్రాసెసింగ్ కోసం వాయిస్ నోట్ పంపండి లేదా బ్యాలెన్స్ విచారణ కోసం టెక్స్ట్ సందેశం పంపండి.",
        "unsupported_media": "మద్దతు లేని మీడియా రకం: {media_type}। దయచేసి వాయిస్ నోట్ లేదా చిత్రాన్ని పంపండి.",
        "no_transactions_found": "తాజా లావాదేవీలు ఏవీ కనుగొనబడలేదు.",
        "image_received_stock_update": "చిత్రం స్వీకరించబడింది! స్టాక్ అప్‌డేట్ కోసం ప్రాసెస్ చేయబడుతోంది...",
        "stock_update_success": "✅ స్టాక్ విజయవంతంగా నవీకరించబడింది: {updates}.",
        "stock_update_fail": "❌ చిత్రం నుండి స్టాక్‌ను నవీకరించడంలో విఫలమైంది. లోపం: {error_msg}",
        "file_download_error": "మీ చిత్రం డౌన్‌లోడ్ చేయడంలో విఫలమైంది: {error_msg}. దయచేసి మళ్లీ ప్రయత్నించండి.",
        "welcome": "నమస్కారం! నేను మీ ఇన్వెంటరీ నిర్వహణ బాట్ ని. నేను మీకు ఎలా సహాయం చేయగలను?",
        "stock_updated": "{item_name} నిల్వ విజయవంతంగా నవీకరించబడింది. ప్రస్తుత పరిమాణం: {current_quantity} {unit}.",
        "sale_recorded": "{quantity} {unit} {item_name} అమ్మకం నమోదు చేయబడింది. మిగిలిన నిల్వ: {current_quantity} {unit}. మొత్తం అమ్మకపు మొత్తం: {selling_amount}. లాభం: {profit}.",
        "purchase_recorded": "{quantity} {unit} {item_name} కొనుగోలు నమోదు చేయబడింది. కొత్త నిల్వ: {current_quantity} {unit}. ధర: {cost_price_per_unit} ప్రతి {unit}.",
        "item_not_found": "'{item_name}' నిల్వలో కనుగొనబడలేదు. మీరు దీన్ని జోడించాలనుకుంటున్నారా?",
        "unknown_command": "నాకు అర్థం కాలేదు. దయచేసి మళ్ళీ ప్రయత్నించండి లేదా 'సహాయం' అని టైప్ చేయండి.",
        "current_stock": "{item_name} ప్రస్తుత నిల్వ: {current_quantity} {unit}.",
        "all_stock_items": "మీ ప్రస్తుత నిల్వ:\n{stock_list}",
        "daily_summary": "{date} కోసం రోజువారీ అమ్మకాల సారాంశం:\nమొత్తం అమ్మకాలు: {total_sales}\nమొత్తం లాభం: {total_profit}\n{sales_details}",
        "low_stock_alert": "⚠️ తక్కువ నిల్వ హెచ్చరిక! క్రింది అంశాలు తక్కువగా ఉన్నాయి:\n{low_stock_items_list}\nత్వరలో ఆర్డర్ చేయాలని పరిగణించండి.",
        "call_initiated": "{item_name} యొక్క {quantity} {unit} కోసం {supplier_name} ({supplier_phone_number}) కు కాల్ ప్రారంభించబడుతోంది. ఆర్డర్ నిర్ధారించబడిన తర్వాత నేను మీకు తెలియజేస్తాను.",
        "order_confirmation_prompt": "మీరు ఏ వస్తੁవు మరియు సరఫరాదారు కోసం ఆర్డర్‌ను నిర్ధారించాలనుకుంటున్నారు? ఉదాహరణ: 'సప్లయర్ A నుండి 10 కిలોల బియ్యం ఆర్డర్ చేయండి'.",
        "order_confirmed_shopkeeper": "✅ {item_name} యొక్క {quantity} {unit} కొరకు {supplier_name} నుండి ఆర్డర్ నిర్ధారించబడింది. స్టాక్ అప్‌డేట్ చేయబడింది. 2 రోజులలో డెలివరీ ఆశించబడుతుంది.",
        "order_failed_shopkeeper": "❌ {item_name} యొక్క {supplier_name} నుండి ఆర్డర్‌ను నిర్ధారించడంలో విఫలమైంది. కారణం: {reason}"
    },
    "bn": { # Bengali messages
        "sale_success": "✅ আপনার ডিজিটাল অ্যাকাউন্টে ₹{amount:.2f} ({item}) বিক্রয় যোগ করা হয়েছে।",
        "sale_success_no_item": "✅ আপনার ডিজিটাল অ্যাকাউন্টে ₹{amount:.2f} বিক্রয় যোগ করা হয়েছে।",
        "expense_success": "✅ আপনার ডিজিটাল অ্যাকাউন্টে ₹{amount} ({item}) খরচ রেকর্ড করা হয়েছে।",
        "expense_success_no_item": "✅ আপনার ডিজিটাল অ্যাকাউন্টে ₹{amount} খরচ রেকর্ড করা হয়েছে।",
        "balance_inquiry": "📊 আপনার ডিজিটাল অ্যাকাউন্টের বর্তমান ব্যালেন্স ₹{balance:.2f}।\n\nসাম্প্রতিক লেনদেন:\n{transactions_summary}",
        "extract_fail": "আপনার ভয়েস নোট থেকে ডেটা এক্সট্র্যাক্ট করা যায়নি। অনুগ্রহ করে পরিষ্কার করে বলুন।",
        "transcribe_fail": "আপনার ভয়েস নোট ট্রান্সক্রাইব বা অনুবাদ করা যায়নি। অডিও গুণমান খারাপ হতে পারে।",
        "download_fail": "আপনার ভয়েস নোট ডাউনলোড করা যায়নি: {error_msg}। অনুগ্রহ করে আবার চেষ্টা করুন।",
        "network_error": "আপনার ভয়েস নোট ডাউনলোড করার সময় একটি নেটওয়ার্ক ত্রুটি হয়েছে: {error_msg}। অনুগ্রহ করে আবার চেষ্টা করুন।",
        "file_error": "ভয়েস নোট ফাইলটিতে একটি সমস্যা ছিল: {error_msg}। অনুগ্রহ করে এটি আবার পাঠান।",
        "unexpected_error": "আপনার ভয়েস নোট প্রক্রিয়া করার সময় একটি অপ্রত্যাশিত ত্রুটি ঘটেছে। অনুগ্রহ করে আবার চেষ্টা করুন।",
        "no_voice_note": "লেনদেন প্রক্রিয়াকরণের জন্য একটি ভয়েস নোট পাঠান অথবা ব্যালেন্স জানতে একটি পাঠ্য বার্তা পাঠান।",
        "unsupported_media": "অসমর্থিত মিডিয়া প্রকার: {media_type}। অনুগ্রহ করে একটি ভয়েস নোট বা একটি ছবি পাঠান।",
        "no_transactions_found": "কোনো সাম্প্রতিক লেনদেন পাওয়া যায়নি।",
        "image_received_stock_update": "ছবি গৃহীত হয়েছে! স্টক আপডেটের জন্য প্রক্রিয়া করা হচ্ছে...",
        "stock_update_success": "✅ স্টক সফলভাবে আপডেট করা হয়েছে: {updates}.",
        "stock_update_fail": "❌ ছবি থেকে স্টক আপডেট করতে ব্যর্থ হয়েছে। ত্রুটি: {error_msg}",
        "file_download_error": "আপনার ছবি ডাউনলোড করা যায়নি: {error_msg}। অনুগ্রহ করে আবার চেষ্টা করুন।",
        "low_stock_alert": "⚠️ কম স্টক সতর্কতা! নিম্নলিখিত আইটেমগুলি কম হচ্ছে:\n{low_stock_items_list}\nশীঘ্রই অর্ডার করার কথা ভাবুন।",
        "welcome": "নমস্কার! আমি আপনার ইনভেন্টরি ম্যানেজমেন্ট বট। আমি আপনাকে কিভাবে সাহায্য করতে পারি?",
        "stock_updated": "{item_name} এর স্টক সফলভাবে আপডেট হয়েছে। বর্তমান পরিমাণ: {current_quantity} {unit}.",
        "sale_recorded": "{quantity} {unit} {item_name} এর বিক্রয় রেকর্ড করা হয়েছে। অবশিষ্ট স্টক: {current_quantity} {unit}. মোট বিক্রয় পরিমাণ: {selling_amount}. লাভ: {profit}.",
        "purchase_recorded": "{quantity} {unit} {item_name} এর ক্রয় রেকর্ড করা হয়েছে। নতুন স্টক: {current_quantity} {unit}. খরচ: {cost_price_per_unit} প্রতি {unit}.",
        "item_not_found": "'{item_name}' স্টক পাওয়া যায়নি। আপনি কি এটি যোগ করতে চান?",
        "unknown_command": "আমি বুঝতে পারিনি। অনুগ্রহ করে আবার চেষ্টা করুন বা 'সাহায্য' টাইপ করুন।",
        "current_stock": "{item_name} এর বর্তমান স্টক: {current_quantity} {unit}.",
        "all_stock_items": "আপনার বর্তমান স্টক:\n{stock_list}",
        "daily_summary": "{date} এর জন্য দৈনিক বিক্রয় সারাংশ:\nমোট বিক্রয়: {total_sales}\nমোট লাভ: {total_profit}\n{sales_details}",
       
        "call_initiated": "{item_name} এর {quantity} {unit} এর জন্য {supplier_name} ({supplier_phone_number}) কে কল শুরু করা হচ্ছে। অর্ডার নিশ্চিত হওয়ার পরে আমি আপনাকে জানাবো।",
        "order_confirmation_prompt": "আপনি কোন আইটেম এবং সরবরাহকারীর জন্য অর্ডার নিশ্চিত করতে চান? উদাহরণ: 'সাপ্লায়ার এ থেকে 10 কেজি চাল অর্ডার করুন'.",
        "order_confirmed_shopkeeper": "✅ {item_name} এর {quantity} {unit} এর জন্য {supplier_name} থেকে অর্ডার নিশ্চিত হয়েছে। স্টক আপডেট করা হয়েছে। 2 দিনের মধ্যে ডেলিভারি আশা করা হচ্ছে।",
        "order_failed_shopkeeper": "❌ {item_name} এর {supplier_name} থেকে অর্ডার নিশ্চিত করা যায়নি। কারণ: {reason}"
    },
    "mr": { # Marathi messages
        "sale_success": "✅ तुमच्या डिजिटल खात्यात ₹{amount:.2f} ({item}) ची विक्री नोंदवली गेली आहे.",
        "sale_success_no_item": "✅ तुमच्या डिजिटल खात्यात ₹{amount:.2f} ची विक्री नोंदवली गेली आहे.",
        "expense_success": "✅ तुमच्या डिजिटल खात्यात ₹{amount} ({item}) चा खर्च नोंदवला गेला आहे.",
        "expense_success_no_item": "✅ तुमच्या डिजिटल खात्यात ₹{amount} चा खर्च नोंदवला गेला आहे.",
        "balance_inquiry": "📊 तुमच्या डिजिटल खात्यातील सध्याची शिल्लक ₹{balance:.2f} आहे.\n\nअलीकडील व्यवहार:\n{transactions_summary}",
        "extract_fail": "तुमच्या व्हॉइस नोटमधून डेटा काढता आला नाही. कृपया स्पष्ट बोला.",
        "transcribe_fail": "तुमचा व्हॉइस नोट ट्रान्सक्राइब किंवा अनुवादित करता आला नाही. ऑडिओ गुणवत्ता खराब असू शकते.",
        "download_fail": "तुमचे व्हॉइस नोट डाउनलोड करण्यात अयशस्वी: {error_msg}. कृपया पुन्हा प्रयत्न करा.",
        "network_error": "तुमचे व्हॉइस नोट डाउनलोड करताना नेटवर्क त्रुटि आली: {error_msg}. कृपया पुन्हा प्रयत्न करा.",
        "file_error": "व्हॉइस नोट फाइल्मध्ये समस्या होती: {error_msg}. कृपया ती पुन्हा पाठवा.",
        "unexpected_error": "तुमच्या व्हॉइस नोटवर प्रक्रिया करताना अनपेक्षित त्रुटी आली. कृपया पुन्हा प्रयत्न करा.",
        "no_voice_note": "कृपया व्यवहाराच्या प्रक्रियेसाठी व्हॉइस नोट पाठवा किंवा शिल्लक चौकशीसाठी मजकूर संदेश पाठवा.",
        "unsupported_media": "असमर्थित मीडिया प्रकार: {media_type}. कृपया व्हॉइस नोट किंवा इमेज पाठवा.",
        "no_transactions_found": "अलीकडील कोणतेही व्यवहार आढळले नाहीत.",
        "image_received_stock_update": "इमेज प्राप्त झाली! स्टॉक अद्ययावत करण्यासाठी प्रक्रिया सुरू आहे...",
        "stock_update_success": "✅ स्टॉक यशस्वीरित्या अद्ययावित झाला: {updates}.",
        "stock_update_fail": "❌ इमेजमधून स्टॉक अद्ययावित करण्यात अयशस्वी. त्रुटी: {error_msg}",
        "file_download_error": "तुमची इमेज डाउनलोड करण्यात अयशस्वी: {error_msg}. कृपया पुन्हा प्रयत्न करा.",
        "low_stock_alert": "⚠️ कमी स्टॉक अलर्ट! खालील वस्तू कमी होत आहेत:\n{low_stock_items_list}\nलवकरच ऑर्डर करण्याचा विचार करा।",
        "welcome": "नमस्कार! मी तुमचा इन्व्हेंटरी मॅनेजमेंट बॉट आहे. मी तुम्हाला कशी मदत करू शकतो?",
        "stock_updated": "{item_name} चा स्टॉक यशस्वीरित्या अद्ययावित झाला. सध्याची संख्या: {current_quantity} {unit}.",
        "sale_recorded": "{quantity} {unit} {item_name} ची विक्री नोंदवली गेली. उर्वरित स्टॉक: {current_quantity} {unit}. एकूण विक्री रक्कम: {selling_amount}. नफा: {profit}.",
        "purchase_recorded": "{quantity} {unit} {item_name} ची खरेदी नोंदवली गेली. नवीन स्टॉक: {current_quantity} {unit}. किंमत: {cost_price_per_unit} प्रति {unit}.",
        "item_not_found": "'{item_name}' स्टॉक मध्ये आढळले नाही. तुम्ही ते जोडू इच्छिता का?",
        "unknown_command": "मला समजले नाही. कृपया पुन्हा प्रयत्न करा किंवा 'मदत' टाइप करा.",
        "current_stock": "{item_name} चा वर्तमान स्टॉक: {current_quantity} {unit}.",
        "all_stock_items": "तुमचा वर्तमान स्टॉक:\n{stock_list}",
        "daily_summary": "{date} साठी दैनिक विक्री सारांश:\nएकूण विक्री: {total_sales}\nएकूण नफा: {total_profit}\n{sales_details}",
        
        "call_initiated": "{item_name} च्या {quantity} {unit} साठी {supplier_name} ({supplier_phone_number}) ला कॉल सुरू केला जात आहे. ऑर्डरची पुष्टी झाल्यावर मी तुम्हाला सूचित करेन।",
        "order_confirmation_prompt": "तुम्हाला कोणत्या वस्तू आणि पुरवठादारासाठी ऑर्डर निश्चित करायचा आहे? उदाहरण: 'पुरवठादार A कडून 10 किलो तांदूळ ऑर्डर करा'.",
        "order_confirmed_shopkeeper": "✅ {item_name} च्या {quantity} {unit} चा {supplier_name} कडून ऑर्डर निश्चित झाला आहे. स्टॉक अद्ययावित झाला आहे. 2 दिवसांत वितरण अपेक्षित आहे.",
        "order_failed_shopkeeper": "❌ {item_name} च्या {supplier_name} कडून ऑर्डर निश्चित करण्यात अयशस्वी. कारण: {reason}"
    }
}


SUPPLIERS = {
    "Supplier A": {
        "phone": "+919971129359",
        "items": {
            "चावल": {"price_per_unit": 45.0, "unit": "kg"},
            "आटा": {"price_per_unit": 30.0, "unit": "kg"},
            "सूजी": {"price_per_unit": 40.0, "unit": "kg"},
            "राजमा": {"price_per_unit": 120.0, "unit": "kg"},
            "मूंग दाल": {"price_per_unit": 90.0, "unit": "kg"},
            "उड़द दाल": {"price_per_unit": 100.0, "unit": "kg"},
        }
    },
    "Supplier B": {
        "phone": "+919988776655",
        "items": {
            "चावल": {"price_per_unit": 47.0, "unit": "kg"},
            "आटा": {"price_per_unit": 28.0, "unit": "kg"},
            "सूजी": {"price_per_unit": 42.0, "unit": "kg"},
            "राजमा": {"price_per_unit": 118.0, "unit": "kg"},
            "मूंग दाल": {"price_per_unit": 92.0, "unit": "kg"},
            "उड़द दाल": {"price_per_unit": 98.0, "unit": "kg"},
        }
    },
    "Supplier C": {
        "phone": "+917788990011",
        "items": {
            "चावल": {"price_per_unit": 46.0, "unit": "kg"},
            "आटा": {"price_per_unit": 31.0, "unit": "kg"},
            "सूजी": {"price_per_unit": 39.0, "unit": "kg"},
            "राजमा": {"price_per_unit": 125.0, "unit": "kg"},
            "मूंग दाल": {"price_per_unit": 88.0, "unit": "kg"},
            "उड़द दाल": {"price_per_unit": 105.0, "unit": "kg"},
        }
    }
}

class DownloadError(Exception):
    pass

async def find_cheapest_supplier_for_item(item_name: str, item_unit: str) -> dict | None:
    cheapest_supplier = None
    min_price = float('inf')

    # Normalize the item_name and item_unit for better matching
    normalized_item_name = item_name.lower().strip()
    normalized_item_unit = item_unit.lower().strip()

    print(f"DEBUG_SUPPLIER: Searching for cheapest supplier for '{normalized_item_name}' ({normalized_item_unit}).")

    for supplier_name, supplier_info in SUPPLIERS.items():
        for supplier_item_name, item_details in supplier_info["items"].items():
            normalized_supplier_item_name = supplier_item_name.lower().strip()
            supplier_item_unit = item_details["unit"].lower().strip()

            # Use fuzzy matching for item names
            name_score = max(fuzz.token_sort_ratio(normalized_item_name, normalized_supplier_item_name),
                             fuzz.partial_ratio(normalized_item_name, normalized_supplier_item_name))
            
            # Consider a high threshold for fuzzy matching to ensure accuracy
            if name_score >= 80 and normalized_item_unit == supplier_item_unit: # Also ensure units match
                price = item_details["price_per_unit"]
                print(f"DEBUG_SUPPLIER: Found match with {supplier_name} for '{supplier_item_name}' (score: {name_score}). Price: {price} {supplier_item_unit}")

                if price < min_price or (price == min_price and supplier_info["phone"] == "+919971129359"):
                    min_price = price
                    cheapest_supplier = {
                        "supplier_name": supplier_name,
                        "phone": supplier_info["phone"],
                        "item_name": supplier_item_name,
                        "price_per_unit": price,
                        "unit": supplier_item_unit
                    }
    
    if cheapest_supplier:
        print(f"DEBUG_SUPPLIER: Cheapest supplier found: {cheapest_supplier['supplier_name']} for {cheapest_supplier['item_name']} at ₹{cheapest_supplier['price_per_unit']}/{cheapest_supplier['unit']}")
    else:
        print(f"DEBUG_SUPPLIER: No cheapest supplier found for '{normalized_item_name}' ({normalized_item_unit}).")

    return cheapest_supplier

from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
import requests

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2), retry=retry_if_exception_type(DownloadError))
def download_media_with_retry(media_url: str, file_path: str):
    print(f"Attempting to download media from: {media_url}")
    response = requests.get(media_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN), timeout=10)
    print(f"Download response status code: {response.status_code}")
    response.raise_for_status()

    if not response.content:
        raise DownloadError(f"Downloaded content from {media_url} is empty.")

    with open(file_path, 'wb') as f:
        f.write(response.content)
    print(f"Media downloaded successfully to {file_path}")

async def send_whatsapp_message(to_number: str, message_body: str):
    if to_number == TWILIO_WHATSAPP_NUMBER:
        print(f"DEBUG: Skipping sending message to self ({to_number}). Message: {message_body}")
        return
    try:
        print(f"DEBUG: Attempting to send WhatsApp message to {to_number} from {TWILIO_WHATSAPP_NUMBER}. Message: {message_body}")
        message = await asyncio.to_thread(
            twilio_client.messages.create,
            to=to_number,
            from_=TWILIO_WHATSAPP_NUMBER,
            body=message_body
        )
        print(f"DEBUG: WhatsApp message sent successfully. SID: {message.sid}")
    except Exception as e:
        print(f"ERROR: Failed to send WhatsApp message to {to_number}: {e}")

@app.route("/whatsapp", methods=["POST"])
async def whatsapp_webhook():
    sender_id = request.form.get('From', '')
    message_body = request.form.get('Body', '')
    media_url = request.form.get('MediaUrl0', None)
    media_content_type = request.form.get('MediaContentType0', None)
    current_date = date.today()

    print(f"DEBUG_WEBHOOK: Received message. MediaUrl0: {media_url}, MediaContentType0: {media_content_type}")

    detected_language = 'en'
    original_transcription = ""
    english_translation = ""

    should_return_early = False

    balance_keywords = ["balance", "account", "kitna", "total", "shilak", "rupai", "money", "खाता", "कितने पैसे हैं", "how much money", "kitni rakam hai", "शिल्लक", "रक्कम"]
    earnings_keywords = ["kamai", "earnings", "profit", "aaj kii", "today's", "कितनी कमाई हुई", "आज की कमाई", "फायदा", "कमई", "how much did you earn today", "how much you earn today", "total sales today", "total earned today", "आज कमई", "कमई", "aaj ki kamai", "how much today earnings"]

    cleaned_original_transcription = ""
    cleaned_english_translation = ""

    if media_url:
        if media_content_type and 'audio' in media_content_type:
            try:
                audio_file_path = f"./temp_audio_{sender_id.replace(':', '_')}.ogg"
                download_media_with_retry(media_url, audio_file_path)

                from pydub import AudioSegment  # Ensure AudioSegment is imported

                mp3_file_path = audio_file_path.replace(".ogg", ".mp3")
                try:
                    AudioSegment.from_file(audio_file_path).export(mp3_file_path, format="mp3")
                except Exception as e:
                    print(f"Error converting audio file: {e}")
                    raise
                finally:
                    if os.path.exists(audio_file_path):
                        os.remove(audio_file_path)

                transcription_result = transcribe_audio(mp3_file_path)
                if os.path.exists(mp3_file_path):
                    os.remove(mp3_file_path)
               

                original_transcription = transcription_result["original_transcription"]
                english_translation = transcription_result["english_translation"]
                detected_language = language_map.get(transcription_result["detected_language"], 'en')
                print(f"Detected language: {detected_language}")
                print(f"Original Transcription: {original_transcription}")
                print(f"English Translation: {english_translation}")

                cleaned_original_transcription = re.sub(r'[^\w\s]', '', original_transcription, flags=re.IGNORECASE | re.UNICODE).lower().strip() if original_transcription else ''
                cleaned_english_translation = re.sub(r'[^\w\s]', '', english_translation, flags=re.IGNORECASE | re.UNICODE).lower().strip() if english_translation else ''

            except Exception as e:
                print(f"Error during voice note processing: {e}")
                reply_message = MESSAGES[detected_language]["file_error"].format(error_msg=str(e))
                await send_whatsapp_message(sender_id, reply_message)
                should_return_early = True
                original_transcription = ""
                english_translation = ""

        elif media_content_type and 'image' in media_content_type:
            try:
                image_file_path = f"./temp_image_{sender_id.replace(':', '_')}.jpg"
                download_media_with_retry(media_url, image_file_path)

                await send_whatsapp_message(sender_id, MESSAGES[detected_language]["image_received_stock_update"])

                extracted_bill_data = extract_items_from_bill_image(image_file_path)
                os.remove(image_file_path)

                bill_type = extracted_bill_data.get("bill_type", "unknown")
                extracted_items = extracted_bill_data.get("items", [])
                detected_language_from_bill = extracted_bill_data.get("detected_language", 'en')

                detected_language = language_map.get(detected_language_from_bill, 'en')
                print(f"DEBUG: Detected language from bill: {detected_language_from_bill} -> Normalized: {detected_language}")

                print(f"DEBUG: Extracted bill type: {bill_type}")
                print(f"DEBUG: Extracted items: {extracted_items}")

                if extracted_items:
                    update_messages = []

                    if bill_type == "unknown":
                        if any(item.get("cost_price_per_unit") is not None for item in extracted_items):
                            bill_type = "purchase"
                        elif any(item.get("selling_price_per_unit") is not None for item in extracted_items):
                            reply_message = MESSAGES[detected_language]["stock_update_fail"].format(error_msg="Sales via image are not supported. Please send sales details (item name, quantity, selling price) via voice note or text.")
                            await send_whatsapp_message(sender_id, reply_message)
                            should_return_early = True

                    if bill_type == "purchase":
                        total_bill_expense = 0.0
                        for item in extracted_items:
                            item_name = item.get("item_name")
                            quantity = item.get("quantity")
                            unit = item.get("unit", "pcs")
                            cost_price_per_unit = item.get("cost_price_per_unit")

                            if item_name and isinstance(quantity, (int, float)) and cost_price_per_unit is not None:
                                print(f"DEBUG: Processing purchase item: {item_name}, quantity={float(quantity)} {unit}, cost_price_per_unit={cost_price_per_unit}")
                                await update_stock_item(sender_id, item_name, float(quantity), unit, cost_price_per_unit)
                                update_messages.append(f"{item_name}: {quantity} {unit}")

                                quantity_for_expense_calc = float(quantity)

                                total_bill_expense += quantity_for_expense_calc * float(cost_price_per_unit)

                        if update_messages:
                            if total_bill_expense > 0:
                                expense_data = {
                                    "date": current_date.strftime('%Y-%m-%d'),
                                    "type": "expense",
                                    "amount": total_bill_expense,
                                    "item": f"Stock purchase via bill ({len(update_messages)} items)"
                                }
                                await save_transaction(expense_data, sender_id)
                                update_messages.append(f"Total expense of ₹{total_bill_expense:.2f} recorded.")

                            reply_message = MESSAGES[detected_language]["stock_update_success"].format(updates="\n".join(update_messages))
                            await send_whatsapp_message(sender_id, reply_message)
                            should_return_early = True
                        else:
                            reply_message = MESSAGES[detected_language]["stock_update_fail"].format(error_msg="Could not extract any valid items with cost prices from the purchase bill.")
                            await send_whatsapp_message(sender_id, reply_message)
                            should_return_early = True

                    elif bill_type == "sale":
                        reply_message = MESSAGES[detected_language]["stock_update_fail"].format(error_msg="Sales bills cannot be processed via image. Please send sales information via voice note or text (item name, quantity, selling price).")
                        await send_whatsapp_message(sender_id, reply_message)
                        should_return_early = True

                    else:
                        reply_message = MESSAGES[detected_language]["stock_update_fail"].format(error_msg="Could not extract any valid items or clear pricing information from the image to determine bill type.")
                        await send_whatsapp_message(sender_id, reply_message)
                        should_return_early = True

                else:
                    reply_message = MESSAGES[detected_language]["stock_update_fail"].format(error_msg="Could not extract any items from the image.")
                    await send_whatsapp_message(sender_id, reply_message)
                    should_return_early = True

            except Exception as e:
                print(f"Error during image processing: {e}")
                print(f"DEBUG: Type of exception e: {type(e)}")
                print(f"DEBUG: Content of exception e: {e}")
                reply_message = MESSAGES[detected_language]["stock_update_fail"].format(error_msg=str(e))
                await send_whatsapp_message(sender_id, reply_message)
                should_return_early = True
        else:
            print(f"Unsupported media type received: {media_content_type}")
            reply_message = MESSAGES[detected_language]["unsupported_media"].format(media_type=media_content_type)
            await send_whatsapp_message(sender_id, reply_message)
            should_return_early = True

    if should_return_early:
        return str(MessagingResponse())

    if message_body and not should_return_early:
        original_transcription = message_body
        english_translation = message_body
        print(f"Received text message: {message_body}")
        detected_language = 'en'
        cleaned_original_transcription = re.sub(r'[^\w\s]', '', original_transcription, flags=re.IGNORECASE | re.UNICODE).lower().strip() if original_transcription else ''
        cleaned_english_translation = re.sub(r'[^\w\s]', '', english_translation, flags=re.IGNORECASE | re.UNICODE).lower().strip() if english_translation else ''

    if should_return_early:
        return str(MessagingResponse())

    if not should_return_early:
        print(f"DEBUG_APP: should_return_early at start of main processing block: {should_return_early}")

        if detected_language != 'en' and english_translation:
            text_for_extraction = english_translation
            print(f"DEBUG: Using English translation for extraction (prioritized due to non-English detected language): {text_for_extraction}")
        elif original_transcription:
            text_for_extraction = original_transcription
            print(f"DEBUG: Using original transcription for extraction (default or English detected): {text_for_extraction}")
        elif english_translation:
            text_for_extraction = english_translation
            print(f"DEBUG: Using English translation for extraction (fallback): {text_for_extraction}")
        else:
            text_for_extraction = ""
            print("DEBUG: No text available for extraction.")

        is_balance_inquiry = any(keyword in cleaned_original_transcription or keyword in cleaned_english_translation for keyword in balance_keywords)
        is_earnings_inquiry = any(keyword in cleaned_original_transcription or keyword in cleaned_english_translation for keyword in earnings_keywords)

        if is_earnings_inquiry:
            today_sales, today_sales_transactions = await get_daily_sales_summary(sender_id, current_date)

            sales_details_list = []
            if today_sales_transactions:
                for txn in today_sales_transactions:
                    txn_item = txn.get("item", "N/A")
                    txn_amount = f'{txn.get("amount", 0.0):.2f}'
                    sales_details_list.append(f"• {txn_item}: ₹{txn_amount}")
                sales_details_str = "\n".join(sales_details_list)
            else:
                sales_details_str = MESSAGES[detected_language].get("no_sales_found_today", "No sales found today.")

            reply_message = MESSAGES[detected_language]["earnings_summary"].format(
                total_sales=today_sales,
                sales_details=sales_details_str
            )
            await send_whatsapp_message(sender_id, reply_message)
            should_return_early = True

        elif is_balance_inquiry:
            total_balance, recent_transactions = await get_user_transactions_summary(sender_id)

            transactions_summary_list = []
            for txn in recent_transactions:
                txn_date = txn.get("transaction_date", "N/A")
                txn_type = txn.get("transaction_type", "N/A").capitalize()
                txn_amount = f'{txn.get("amount", "N/A"):.2f}' if isinstance(txn.get("amount"), (int, float)) else "N/A"
                txn_item = txn.get("item", "")

                formatted_txn_amount = txn_amount
                formatted_txn_date = txn_date

                summary_line = f"• Date: {formatted_txn_date}, Type: {txn_type}, Amount: ₹{formatted_txn_amount}"
                if txn_item:
                    summary_line += f" ({txn_item})"
                transactions_summary_list.append(summary_line)

            transactions_summary_str = "\n".join(transactions_summary_list) if transactions_summary_list else MESSAGES[detected_language].get("no_transactions_found", "No recent transactions found.")

            reply_message = MESSAGES[detected_language]["balance_inquiry"].format(
                balance=total_balance,
                transactions_summary=transactions_summary_str
            )

            await send_whatsapp_message(sender_id, reply_message)
            should_return_early = True

        elif (english_translation or original_transcription):
            if detected_language != 'en' and english_translation:
                text_for_extraction = english_translation
                print(f"DEBUG: Using English translation for extraction (prioritized due to non-English detected language): {text_for_extraction}")
            elif original_transcription:
                text_for_extraction = original_transcription
                print(f"DEBUG: Using original transcription for extraction (default or English detected): {text_for_extraction}")
            elif english_translation:
                text_for_extraction = english_translation
                print(f"DEBUG: Using English translation for extraction (fallback): {text_for_extraction}")
            else:
                text_for_extraction = ""
                print("DEBUG: No text available for extraction.")

            if not text_for_extraction:
                print("ERROR_APP: No valid text available for extraction after selection logic.")
                await send_whatsapp_message(sender_id, MESSAGES[detected_language]["extract_fail"])
                should_return_early = True
                return str(MessagingResponse()) # Return early

            print(f"DEBUG: Text for structured data extraction: {text_for_extraction}")
            try:
                raw_extracted_content = extract_structured_data(text_for_extraction, current_date)
                print(f"DEBUG_APP: Raw extracted content (from data_extractor): {raw_extracted_content}")
                extracted_data = copy.deepcopy(raw_extracted_content)
                print(f"DEBUG_APP: Extracted structured data (after direct deep copy): {extracted_data}")
            except Exception as e:
                print(f"ERROR_APP: Exception during data extraction or type checking: {e}")
                print(f"ERROR_APP: Type of exception: {type(e)}")
                extracted_data = {}
                should_return_early = True

            if isinstance(extracted_data, dict) and extracted_data:
                await _process_transaction_sync(
                    extracted_data,
                    sender_id,
                    detected_language,
                    current_date,
                    original_transcription,
                    english_translation
                )
            else:
                await send_whatsapp_message(sender_id, MESSAGES[detected_language]["extract_fail"])
                should_return_early = True
        else:
            await send_whatsapp_message(sender_id, MESSAGES[detected_language]["transcribe_fail"])
            should_return_early = True

    print("DEBUG_APP: whatsapp_webhook function completing.")
    return str(MessagingResponse())

async def _translate_text_to_target_language(text: str, target_language: str) -> str:
    try:
        prompt = f"""Translate the following text into {target_language}. Respond only with the translated text.
Text: {text}"""
        response = client.chat.completions.create(
            model="gpt-3.5-turbo-0125",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that translates text."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.0
        )
        translated_text = response.choices[0].message.content.strip()
        print(f"DEBUG_TRANSLATION: Original: '{text}', Translated to {target_language}: '{translated_text}'")
        return translated_text
    except Exception as e:
        print(f"ERROR_TRANSLATION: Error translating text: {e}")
        return text

async def _process_transaction_sync(extracted_data: dict, sender_id: str, detected_language: str, current_date: date, original_transcription: str, english_translation: str):
    print(f"DEBUG_APP: Entering synchronous transaction processing block with data: {extracted_data}")
    transaction_type = extracted_data.get("type", "").lower()
    print(f"DEBUG: Identified transaction type: {transaction_type}")

    profit_keywords = ["profit", "मुनाफा", "labh", "फायda", "profitability", "लाभ"]
    should_show_profit = any(keyword in original_transcription.lower() for keyword in profit_keywords) or \
                         any(keyword in english_translation.lower() for keyword in profit_keywords)

    if transaction_type == "sale":
        sales_summary_messages = []
        unprocessed_items_messages = []
        total_sales_amount = 0.0
        total_profit = 0.0

        items_sold = extracted_data.get("items_sold", [])
        if items_sold:
            stock_levels = await get_stock_levels(sender_id)
            stock_map = {f"{item['item_name']}-{item['unit']}": item for item in stock_levels}
            print(f"DEBUG_STOCK: Current stock_levels: {stock_levels}")
            print(f"DEBUG_STOCK: Current stock_map keys: {list(stock_map.keys())}")

            target_stock_language = "hi"

            for item in items_sold:
                item_name = item.get("item_name")
                quantity = item.get("quantity")
                unit = item.get("unit", "pcs")
                selling_amount = item.get("selling_amount")

                print(f"DEBUG_ITEM_PROCESSING: Processing extracted item: {{'item_name': '{item_name}', 'quantity': {quantity}, 'unit': '{unit}', 'selling_amount': {selling_amount}}}")

                if item_name and isinstance(quantity, (int, float)) and isinstance(selling_amount, (int, float)):
                    stock_item_name_for_lookup = item_name

                    contains_latin = bool(re.search(r'[a-zA-Z]', item_name))

                    if contains_latin and target_stock_language == "hi":
                        translated_item_name = await _translate_text_to_target_language(item_name, target_stock_language)
                        stock_item_name_for_lookup = translated_item_name
                        print(f"DEBUG: Extracted item name '{item_name}' contains Latin characters and target stock language is Hindi. Translated to '{translated_item_name}' for stock lookup.")
                    elif detected_language != target_stock_language:
                        translated_item_name = await _translate_text_to_target_language(item_name, target_stock_language)
                        stock_item_name_for_lookup = translated_item_name
                        print(f"DEBUG: Incoming item '{item_name}' (detected_lang={detected_language}) translated to '{translated_item_name}' (target_lang={target_stock_language}) for stock lookup.")
                    else:
                        print(f"DEBUG: Incoming item '{item_name}' (detected_lang={detected_language}) is already in target stock language ({target_stock_language}) or no translation needed. No translation performed.")

                    best_match_item_name = None
                    best_match_score = 0.0

                    print(f"DEBUG_FUZZY: Starting fuzzy match for '{stock_item_name_for_lookup}'")
                    for stock_item_obj in stock_levels:
                        item_name_in_stock = stock_item_obj['item_name']
                        current_score = max(fuzz.token_sort_ratio(stock_item_name_for_lookup.lower(), item_name_in_stock.lower()),
                                            fuzz.partial_ratio(stock_item_name_for_lookup.lower(), item_name_in_stock.lower()))
                        print(f"DEBUG_FUZZY: Comparing '{stock_item_name_for_lookup}' with stock item '{item_name_in_stock}'. Score: {current_score}")

                        if current_score > best_match_score:
                            best_match_score = current_score
                            best_match_item_name = item_name_in_stock

                    print(f"DEBUG_FUZZY: Best fuzzy match for '{stock_item_name_for_lookup}': '{best_match_item_name}' with score {best_match_score}")

                    if best_match_item_name and best_match_score >= 40:
                        original_lookup_name_before_fuzzy = stock_item_name_for_lookup
                        stock_item_name_for_lookup = best_match_item_name
                        print(f"DEBUG_FUZZY: Fuzzy match successful. Using '{stock_item_name_for_lookup}' (originally '{original_lookup_name_before_fuzzy}') for stock lookup.")
                    else:
                        print(f"DEBUG_FUZZY: No strong fuzzy match found for '{stock_item_name_for_lookup}' (score: {best_match_score}). Proceeding with original lookup name.")
                        pass

                    print(f"DEBUG_ITEM_MATCH: Attempting to find final stock item for '{stock_item_name_for_lookup}' with effective unit '{unit}'")
                    stock_key = f"{stock_item_name_for_lookup}-{unit}"

                    final_stock_item = None
                    if stock_key in stock_map:
                        final_stock_item = stock_map[stock_key]
                        print(f"DEBUG_ITEM_MATCH: Found exact match in stock_map for key: '{stock_key}'. Item: {final_stock_item}")
                    else:
                        print(f"DEBUG_ITEM_MATCH: No exact match in stock_map for key: '{stock_key}'. Attempting fuzzy unit match.")
                        for existing_stock_key, existing_stock_item in stock_map.items():
                            if fuzz.token_sort_ratio(stock_item_name_for_lookup.lower(), existing_stock_item['item_name'].lower()) >= 80:
                                final_stock_item = existing_stock_item
                                stock_key = existing_stock_key
                                unit = existing_stock_item['unit']
                                print(f"DEBUG_ITEM_MATCH: Found fuzzy item name match with stock item '{existing_stock_item['item_name']}' (unit: '{existing_stock_item['unit']}'). Using this item.")
                                break

                    print(f"DEBUG_FINAL_ITEM_CHECK: final_stock_item before not found check: {final_stock_item}")

                    if not final_stock_item:
                        debug_info = f"Original extracted: {item_name}"
                        if detected_language != target_stock_language:
                            debug_info += f", Translated (if applicable): {original_lookup_name_before_fuzzy if 'original_lookup_name_before_fuzzy' in locals() else item_name}"
                        if best_match_item_name:
                            debug_info += f", Fuzzy matched (if applicable): {best_match_item_name}"
                        debug_info += f", Unit: {unit}"
                        unprocessed_items_messages.append(f"'{item_name}' is not in stock. ({debug_info}). Sale not recorded.")
                        continue

                    if final_stock_item and 'unit' in final_stock_item:
                        unit = final_stock_item['unit']
                        print(f"DEBUG_UNIT: Final effective unit for transaction '{item_name}': '{unit}'. (Original extracted unit: '{item.get('unit', 'pcs')}')")


                    delta_for_update = -float(quantity)

                    await update_stock_item(sender_id, stock_item_name_for_lookup, delta_for_update, unit, None)

                    sale_data = {
                        "date": extracted_data.get("date", current_date.strftime('%Y-%m-%d')),
                        "type": "sale",
                        "amount": selling_amount,
                        "item": f"{stock_item_name_for_lookup} ({quantity} {item.get('unit', 'pcs')})"
                    }
                    await save_transaction(sale_data, sender_id)

                    total_sales_amount += selling_amount

                    cost_price_per_unit = None
                    if final_stock_item.get("cost_price_per_unit") is not None:
                        cost_price_per_unit = float(final_stock_item["cost_price_per_unit"])

                    profit_for_item = 0.0
                    if cost_price_per_unit is not None:
                        profit_for_item = selling_amount - (float(quantity) * cost_price_per_unit)
                        total_profit += profit_for_item

                    if should_show_profit and cost_price_per_unit is not None:
                        sales_summary_messages.append(f"{final_stock_item['item_name']}: ₹{selling_amount:.2f} (Profit: ₹{profit_for_item:.2f})")
                    else:
                        sales_summary_messages.append(f"{final_stock_item['item_name']}: ₹{selling_amount:.2f}")

            print(f"DEBUG_REPLY: sales_summary_messages: {sales_summary_messages}")
            print(f"DEBUG_REPLY: unprocessed_items_messages: {unprocessed_items_messages}")

            final_reply_parts = []
            if sales_summary_messages:
                success_item_summary = "\n".join(sales_summary_messages)
                item_details = f"Total Profit: ₹{total_profit:.2f}\n{success_item_summary}" if should_show_profit and total_profit > 0 else success_item_summary
                success_message = MESSAGES[detected_language]["sale_success"].format(amount=total_sales_amount, item_details=item_details)
                final_reply_parts.append(success_message)

            if unprocessed_items_messages:
                error_message = "❌ Some items were not processed:\n" + "\n".join(unprocessed_items_messages)
                final_reply_parts.append(error_message)

            print(f"DEBUG_REPLY: final_reply_parts before join: {final_reply_parts}")
            if final_reply_parts:
                reply_message = "\n\n".join(final_reply_parts)
                await send_whatsapp_message(sender_id, reply_message)
            else:
                await send_whatsapp_message(sender_id, MESSAGES[detected_language]["extract_fail"])
        else:
            await send_whatsapp_message(sender_id, MESSAGES[detected_language]["extract_fail"])

    elif transaction_type == "purchase":
        purchase_summary_messages = []
        total_purchase_expense = 0.0

        items_purchased = extracted_data.get("items_purchased", [])
        if items_purchased:
            for item in items_purchased:
                item_name = item.get("item_name")
                quantity = item.get("quantity")
                unit = item.get("unit", "pcs")
                cost_price_per_unit = item.get("cost_price_per_unit")

                if item_name and isinstance(quantity, (int, float)) and cost_price_per_unit is not None:
                    await update_stock_item(sender_id, item_name, float(quantity), unit, cost_price_per_unit)
                    purchase_summary_messages.append(f"{item_name}: {quantity} {unit} @ ₹{cost_price_per_unit:.2f}/{unit}")

                    total_purchase_expense += float(quantity) * float(cost_price_per_unit)

            if purchase_summary_messages:
                if total_purchase_expense > 0:
                    expense_data = {
                        "date": extracted_data.get("date", current_date.strftime('%Y-%m-%d')),
                        "type": "expense",
                        "amount": total_purchase_expense,
                        "item": f"Stock purchase ({len(purchase_summary_messages)} items)"
                    }
                    await save_transaction(expense_data, sender_id)
                    purchase_summary_messages.append(f"Total expense of ₹{total_purchase_expense:.2f} recorded.")

                reply_message = MESSAGES[detected_language]["stock_update_success"].format(updates="\n".join(purchase_summary_messages))
                await send_whatsapp_message(sender_id, reply_message)
            else:
                await send_whatsapp_message(sender_id, MESSAGES[detected_language]["extract_fail"])
        else:
            await send_whatsapp_message(sender_id, MESSAGES[detected_language]["extract_fail"])

    elif transaction_type == "expense":
        amount = extracted_data.get("amount")
        description = extracted_data.get("description", "")
        if isinstance(amount, (int, float)):
            expense_data = {
                "date": extracted_data.get("date", current_date.strftime('%Y-%m-%d')), "type": "expense",
                "amount": amount, "item": description
            }
            await save_transaction(expense_data, sender_id)

            if description:
                reply_message = MESSAGES[detected_language]["expense_success"].format(amount=amount, item=description)
            else:
                reply_message = MESSAGES[detected_language]["expense_success_no_item"].format(amount=amount)
            await send_whatsapp_message(sender_id, reply_message)
        else:
            await send_whatsapp_message(sender_id, MESSAGES[detected_language]["extract_fail"])

    elif transaction_type == "order_confirmation":
        # CORRECTED: This block now handles a list of items for a single supplier.
        items_to_order = extracted_data.get("items_to_order", [])
        supplier_name = extracted_data.get("supplier_name")

        if items_to_order and supplier_name:
            print(f"DEBUG_ORDER_CONFIRMATION: Received order confirmation for {len(items_to_order)} items from {supplier_name}.")
            
            supplier_phone_number = None
            for sup_name, sup_info in SUPPLIERS.items():
                if fuzz.ratio(supplier_name.lower(), sup_name.lower()) >= 80:
                    supplier_phone_number = sup_info["phone"]
                    break

            if supplier_phone_number:
                # Format the list of items into a single string for the call agent
                order_details_list = [f"{item.get('quantity', 0)} {item.get('unit', 'pcs')} {item.get('item_name', '')}" for item in items_to_order]
                order_details_str = ", ".join(order_details_list)
                
                from call_handler import initiate_outbound_call
                call_initiated = await initiate_outbound_call(
                    to_number=supplier_phone_number,
                    order_details=order_details_str, # Pass the full order string
                    supplier_name=supplier_name,
                    user_id=sender_id
                )

                if call_initiated:
                    reply_message = f"Calling {supplier_name} to place the following order:\n- {', '.join(order_details_list)}"
                    await send_whatsapp_message(sender_id, reply_message)
                else:
                    reply_message = MESSAGES[detected_language]["order_failed_shopkeeper"].format(
                        item_name=f"{len(items_to_order)} items",
                        supplier_name=supplier_name,
                        reason="Failed to initiate call."
                    )
                    await send_whatsapp_message(sender_id, reply_message)
            else:
                await send_whatsapp_message(sender_id, f"Supplier '{supplier_name}' not found.")
        else:
            await send_whatsapp_message(sender_id, MESSAGES[detected_language]["extract_fail"])
    else:
        await send_whatsapp_message(sender_id, MESSAGES[detected_language]["extract_fail"])

# Dictionary to store call states (e.g., conversation history, order details)
# In a production environment, this would be stored in a database.
# Removed global call_states
# Removed call_states = None



async def transcribe_speech_from_url(audio_url: str) -> str:
    """Transcribes speech from an audio URL using OpenAI's Whisper API."""
    try:
        temp_audio_path = f"./temp_call_audio_{os.urandom(4).hex()}.wav"
        print(f"DEBUG_WHISPER: Downloading audio from {audio_url} to {temp_audio_path}")
        response = requests.get(audio_url, stream=True)
        response.raise_for_status()
        with open(temp_audio_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
        print(f"DEBUG_WHISPER: Downloaded audio to {temp_audio_path}")

        with open(temp_audio_path, "rb") as audio_file:
            transcription = await asyncio.to_thread(
                client.audio.transcriptions.create,
                model="whisper-1",
                file=audio_file
            )
        os.remove(temp_audio_path)
        print(f"DEBUG_WHISPER: Transcription result: {transcription.text}")
        return transcription.text
    except Exception as e:
        print(f"ERROR_WHISPER: Failed to transcribe speech from {audio_url}: {e}")
        return ""

async def get_openai_response(conversation_history: list, temperature: float = 0.5, max_tokens: int = 150) -> str:
    """Gets a response from OpenAI's GPT model."""
    try:
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model="gpt-3.5-turbo",
            messages=conversation_history,
            temperature=temperature,
            max_tokens=max_tokens
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"ERROR_OPENAI_RESPONSE: Failed to get OpenAI response: {e}")
        return "I'm sorry, I'm having trouble understanding right now. Please try again."

@app.route("/audio/<filename>")
def serve_audio(filename):
    print(f"DEBUG: Serving audio file: {filename}")
    try:
        return send_from_directory('audio', filename)
    except Exception as e:
        print(f"ERROR: Failed to serve audio file {filename}: {str(e)}")
        return ("Audio file not found", 404)

@app.route("/omnidim_post_call_webhook", methods=['POST'])
async def omnidim_post_call_webhook():
    try:
        payload = request.get_json()
        print("DEBUG_OMNIDIM_WEBHOOK: Received post-call webhook from OmniDimension.")
        print(f"DEBUG_OMNIDIM_WEBHOOK: Payload: {json.dumps(payload, indent=2)}")

        # Here you can process the payload as needed.
        # For example, you can save the call summary, conversation, or extracted data to your database.
        # The 'payload' dictionary will contain:
        # - 'callSummary': A brief overview of the call.
        # - 'fullConversation': The complete transcript with timestamps.
        # - 'sentimentAnalysis': Analysis of customer mood.
        # - 'extractedInformation': Key data points you configured for extraction.

        # Example: Accessing parts of the payload
        call_summary = payload.get("callSummary")
        full_conversation = payload.get("fullConversation")
        extracted_info = payload.get("extractedInformation")

        if call_summary:
            print(f"Call Summary: {call_summary}")
        if full_conversation:
            print(f"Full Conversation Length: {len(full_conversation)} characters")
        if extracted_info:
            print(f"Extracted Information: {extracted_info}")

        # You might want to get the original metadata from the call dispatch
        # This would typically be passed back in the webhook payload or via a call ID lookup
        # For now, we'll just acknowledge the receipt.

        return {"status": "success", "message": "Post-call data received"}, 200
    except Exception as e:
        print(f"ERROR_OMNIDIM_WEBHOOK: Error processing OmniDimension post-call webhook: {str(e)}")
        return {"status": "error", "message": str(e)}, 500

# Function to run the scheduler in its own event loop
def _run_scheduler():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.call_soon_threadsafe(scheduler.start)
    loop.run_forever()

# Shut down the scheduler when the Flask app stops
@app.teardown_appcontext
def shutdown_scheduler(exception=None):
    if scheduler.running:
        scheduler.shutdown()
        print("Scheduler for low stock alerts has been shut down.")

async def generate_local_insights():
    """
    Generates detailed, actionable insights for shopkeepers by combining 14-day weather forecasts,
    upcoming festivals, and current inventory levels into a single, efficient LLM call.
    """
    if os.environ.get('DISABLE_INSIGHTS_ALERTS') == 'true':
        print("DEBUG_INSIGHTS: Alerts and insights generation is disabled.")
        return
    
    print("DEBUG_INSIGHTS: Generating local insights...")
    unique_user_ids = await get_all_unique_user_ids_with_stock()

    # --- Step 1: Fetch Global Data Once ---
    latitude = SHOPKEEPER_LOCATION["latitude"]
    longitude = SHOPKEEPER_LOCATION["longitude"]
    weather_data = await get_weather_forecast(latitude, longitude)
    festivals_data = get_festivals_from_llm(days_in_advance=90)

    # --- Step 2: Process All Insights For Each User ---
    for user_id in unique_user_ids:
        print(f"DEBUG_INSIGHTS: Generating insights for user: {user_id}")
        
        stock_levels = await get_stock_levels(user_id)
        stock_list_str = ", ".join([item['item_name'].lower() for item in stock_levels]) if stock_levels else "कोई आइटम स्टॉक में नहीं है।"

        # --- Step 3: Create a Hindi-focused, Concise, and Structured Prompt ---
        weather_summary = "मौसम का पूर्वानुमान अभी उपलब्ध नहीं है।"
        if weather_data and "daily" in weather_data:
            daily = weather_data['daily']
            weather_summary = (
                f"अगले 14 दिनों का मौसम पूर्वानुमान:\n"
                f"- तापमान: न्यूनतम {min(daily['temperature_2m_min'])}°C से अधिकतम {max(daily['temperature_2m_max'])}°C तक।"
            )

        festival_summary = "अगले 90 दिनों में कोई बड़े त्योहार नहीं हैं।"
        if festivals_data:
            festival_list = [f"{f['name']} ({f['date']})" for f in festivals_data]
            festival_summary = "आगामी प्रमुख त्योहार: " + ", ".join(festival_list)

        # CORRECTED: The prompt is now much stricter and demands separate JSON keys.
        master_prompt = f"""
        You are a Retail Expert for Indian kirana stores. Your goal is to provide actionable advice in HINDI.

        **Data Provided:**
        1.  **14-Day Weather Forecast:** {weather_summary}
        2.  **Upcoming Festivals:** {festival_summary}
        3.  **Current Inventory:** {stock_list_str}

        **Your Tasks (in Hindi):**
        1.  **Opportunities:** Identify 2 key sales opportunities, one for weather and one for festivals.
        2.  **Recommendations:** Provide two separate lists of recommendations:
            - A list named `weather_recommendations` with 2 products based ONLY on the weather.
            - A list named `festival_recommendations` with 2-3 products based ONLY on the festivals.
        
        **Output Format:**
        Respond ONLY with a valid JSON object. The root object MUST have three keys: "opportunities", "weather_recommendations", and "festival_recommendations".
        All string values MUST BE IN HINDI, except for "potential" and "action".

        Example:
        {{
          "opportunities": [
            "बढ़ती गर्मी के कारण ठंडे पेय पदार्थों की मांग बढ़ेगी।",
            "दिवाली के कारण मिठाई बनाने की सामग्री की भारी मांग।"
          ],
          "weather_recommendations": [
            {{
              "action": "Procure",
              "item": "नींबू पानी मिक्स",
              "reason": "गर्मी के दिनों में राहत के लिए।",
              "potential": "High"
            }}
          ],
          "festival_recommendations": [
            {{
              "action": "Promote",
              "item": "घी और बेसन",
              "reason": "दिवाली की मिठाइयों के लिए आवश्यक।",
              "potential": "High"
            }}
          ]
        }}
        """

        try:
            # --- Step 4: Make One Efficient LLM Call for Insights ---
            response = await asyncio.to_thread(
                client.chat.completions.create,
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are an expert for Indian grocery stores. You MUST reply with a valid JSON object where all string values are in Hindi, except for 'action' and 'potential'."},
                    {"role": "user", "content": master_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=0.5,
            )
            
            insights_json = json.loads(response.choices[0].message.content)
            opportunities = insights_json.get("opportunities", [])
            # CORRECTED: Get both lists from the new JSON structure
            weather_recs = insights_json.get("weather_recommendations", [])
            festival_recs = insights_json.get("festival_recommendations", [])
            
            # --- Step 5: Format the Insightful Response for WhatsApp ---
            final_message_parts = []
            if opportunities:
                final_message_parts.append("📈 **बिक्री के अवसर**")
                for opp in opportunities:
                    final_message_parts.append(f"• {opp}")

            # CORRECTED: Separate loops for weather and festival recommendations
            if weather_recs:
                final_message_parts.append("\n🌦️ **मौसम आधारित सिफारिशें**")
                for rec in weather_recs:
                    action_text = "🟢 प्रचार करें" if rec.get('action') == 'Promote' else "📦 खरीदें"
                    item = rec.get("item", "N/A")
                    potential = rec.get("potential", "N/A")
                    reason = rec.get("reason", "N/A")
                    final_message_parts.append(f"• **{action_text}: {item}** (संभावना: {potential})\n  - *कारण: {reason}*")

            if festival_recs:
                final_message_parts.append("\n🎉 **त्योहार आधारित सिफारिशें**")
                for rec in festival_recs:
                    action_text = "🟢 प्रचार करें" if rec.get('action') == 'Promote' else "📦 खरीदें"
                    item = rec.get("item", "N/A")
                    potential = rec.get("potential", "N/A")
                    reason = rec.get("reason", "N/A")
                    final_message_parts.append(f"• **{action_text}: {item}** (संभावना: {potential})\n  - *कारण: {reason}*")
            
            # --- Step 6: Add Low-Stock Alert to the Same Message ---
            low_stock_items = await get_low_stock_items(user_id)
            if low_stock_items:
                final_message_parts.append("\n⚠️ **कम स्टॉक की चेतावनी!**")
                for item in low_stock_items:
                    item_name = item.get('item_name', 'N/A')
                    quantity = item.get('quantity', 0)
                    unit = item.get('unit', '')
                    final_message_parts.append(f"• **{item_name}**: केवल {quantity} {unit} बचा है।")
                final_message_parts.append("*इन वस्तुओं को जल्द ही फिर से ऑर्डर करने पर विचार करें।*")

            # --- Step 7: Send the Final, Combined Message ---
            if final_message_parts:
                final_message_body = "\n".join(final_message_parts)
                await send_whatsapp_message(user_id, final_message_body)
            else:
                print(f"DEBUG_INSIGHTS: No actionable insights generated for {user_id}.")

        except Exception as e:
            print(f"ERROR_INSIGHTS: Failed to generate insights for user {user_id}: {e}")




if __name__ == "__main__":
    # This ensures the scheduler runs only once, even with Flask's reloader.
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        # Initialize a Manager for shared state between processes
        manager = Manager()
        call_states = manager.dict() # Use Manager dictionary for process-safe state
        
    # Initialize and start the scheduler when the app starts
        if not scheduler.running:
            scheduler.add_job(generate_local_insights, 'interval', seconds=30, id='generate_insights_job', replace_existing=True)
        # Removed the low stock alert scheduler job
        # scheduler.add_job(check_low_stock_and_alert, 'interval', seconds=30, id='check_low_stock_and_alert', replace_existing=True)
        scheduler_thread = Thread(target=_run_scheduler)
        scheduler_thread.daemon = True
        scheduler_thread.start()
        print("Scheduler for local insights has been started in a separate thread.")

    # Run the Flask app
    # Using debug=True is helpful for development as it provides detailed errors
    # and automatically reloads the server when you make code changes.
    # Make sure to set debug=False for a production environment.
    app.run(debug=True, host="0.0.0.0", port=5000)