import asyncio
import copy
from datetime import date, datetime, timedelta
import logging
import os
import re
import urllib.parse
from threading import Thread

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from flask import Flask, request, send_from_directory
from fuzzywuzzy import fuzz
from openai import OpenAI
from pydub import AudioSegment
import requests
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse
from twilio.twiml.voice_response import VoiceResponse
# import google.generativeai as genai # Removed Google Generative AI import

from call_handler import initiate_outbound_call
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
BASE_URL = "https://523af0195609.ngrok-free.app"

SHOPKEEPER_LOCATION = {"latitude": 28.7041, "longitude": 77.1025} # Default to Delhi, India
DEFAULT_LANGUAGE = "hi" # Define default language

client = OpenAI() # Re-initialize OpenAI client
# Removed Google Generative AI client configuration
# genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
# gemini_model = genai.GenerativeModel("gemini-2.5-pro") # Removed Gemini model initialization

app = Flask(__name__)
scheduler = AsyncIOScheduler() # Initialize scheduler here

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_WHATSAPP_NUMBER = os.getenv("TWILIO_WHATSAPP_NUMBER")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER") # Twilio Voice enabled phone number
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

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
        "network_error": "તમારો વૉઇસ નોટ ડાઉનલોડ કરતી વખતે નેટવર્ક ભૂલ થઈ: {error_msg}. કૃપા કરીને ફરી પ્રયાસ કરો.",
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
        "call_initiated": "{item_name} દੇ {quantity} {unit} લਈ {supplier_name} ({supplier_phone_number}) નੂં કਾલ કੀતા જા રਿહા હੈ। આરਡર દੀ ਪੁસ਼ટੀ હੋણ 'તੇ મੈં તੁਹਾનੂં सूचित કરૂંગો।",
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
        "low_stock_alert": "⚠️ কম স্টক সতর্কতা! নিম্নলিখিত আইটেমগুলি কম হচ্ছে:\n{low_stock_items_list}\nশীঘ্রই অর্ডার করার কথা ভাবুন।",
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
        "low_stock_alert": "⚠️ कमी स्टॉक अलर्ट! खालील वस्तू कमी होत आहेत:\n{low_stock_items_list}\nलवकरच ऑर्डर करण्याचा विचार करा।",
        "call_initiated": "{item_name} च्या {quantity} {unit} साठी {supplier_name} ({supplier_phone_number}) ला कॉल सुरू केला जात आहे. ऑर्डरची पुष्टी झाल्यावर मी तुम्हाला सूचित करेन.",
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

                mp3_file_path = audio_file_path.replace(".ogg", ".mp3")
                AudioSegment.from_file(audio_file_path).export(mp3_file_path, format="mp3")
                os.remove(audio_file_path)

                transcription_result = transcribe_audio(mp3_file_path)
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
                cost_price_per_unit = item.get("cost_price_per_unit") # Corrected key

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
        item_name = extracted_data.get("item_name")
        quantity = extracted_data.get("quantity")
        unit = extracted_data.get("unit", "pcs")
        supplier_name = extracted_data.get("supplier_name")

        if item_name and isinstance(quantity, (int, float)) and supplier_name:
            print(f"DEBUG_ORDER_CONFIRMATION: Received order confirmation for {quantity} {unit} of {item_name} from {supplier_name}.")
            
            supplier_phone_number = None
            supplier_item_cost_price = None

            # Find supplier details from SUPPLIERS dictionary
            for sup_name, sup_info in SUPPLIERS.items():
                if fuzz.ratio(supplier_name.lower(), sup_name.lower()) >= 80: # Fuzzy match for supplier name
                    supplier_phone_number = sup_info["phone"]
                    # Try to find cost price for the item from this supplier
                    for sup_item_name, item_details in sup_info["items"].items():
                        if fuzz.ratio(item_name.lower(), sup_item_name.lower()) >= 80 and unit.lower() == item_details["unit"].lower():
                            supplier_item_cost_price = item_details["price_per_unit"]
                            break
                    break

            if supplier_phone_number:
                call_initiated = await initiate_outbound_call(
                    to_number=supplier_phone_number,
                    item_name=item_name,
                    quantity=quantity,
                    unit=unit,
                    supplier_name=supplier_name,
                    user_id=sender_id,
                    detected_language="hi" # Force Hindi for outbound calls
                )

                if call_initiated:
                    reply_message = MESSAGES[detected_language]["call_initiated"].format(
                        item_name=item_name,
                        quantity=quantity,
                        unit=unit,
                        supplier_name=supplier_name,
                        supplier_phone_number=supplier_phone_number
                    )
                    await send_whatsapp_message(sender_id, reply_message)
                else:
                    print(f"ERROR: Failed to initiate call to {supplier_name} for {item_name}.")
                    reply_message = MESSAGES[detected_language]["order_failed_shopkeeper"].format(
                        item_name=item_name,
                        supplier_name=supplier_name,
                        reason="Failed to initiate call."
                    )
                    await send_whatsapp_message(sender_id, reply_message)
            else:
                reply_message = MESSAGES[detected_language]["item_not_found"].format(item_name=f"{item_name} (Supplier: {supplier_name})")
                await send_whatsapp_message(sender_id, reply_message)
        else:
            reply_message = MESSAGES[detected_language]["extract_fail"]
            await send_whatsapp_message(sender_id, reply_message)
    else:
        await send_whatsapp_message(sender_id, MESSAGES[detected_language]["extract_fail"])

async def check_low_stock_and_alert():
    print("DEBUG_SCHEDULER: Running low stock check...")
    try:
        response = await asyncio.to_thread(supabase.from_("stock_items").select("user_id").execute)
        if response.data:
            unique_user_ids = list(set([item['user_id'] for item in response.data]))
            print(f"DEBUG_SCHEDULER: Found unique user IDs: {unique_user_ids}")
        else:
            unique_user_ids = []
            print("DEBUG_SCHEDULER: No user IDs found in stock_items table.")

        for user_id in unique_user_ids:
            print(f"DEBUG_SCHEDULER: Checking low stock for user_id: {user_id}")
            low_stock_items = await get_low_stock_items(user_id)
            if low_stock_items:
                low_stock_messages = []
                print(f"DEBUG_SCHEDULER: Low stock items found for {user_id}: {low_stock_items}")
                for item in low_stock_items:
                    alert_item_name = item.get('item_name', 'Unknown Item')
                    alert_message_lang = 'hi' # Default to Hindi for now

                    # Define translated terms based on alert_message_lang
                    if alert_message_lang == 'hi':
                        min_text = "न्यूनतम"
                        cheapest_text = "सबसे सस्ता"
                        call_text = "कॉल"
                    else: # Default to English
                        min_text = "Min"
                        cheapest_text = "Cheapest"
                        call_text = "Call"

                    if bool(re.search(r'[a-zA-Z]', alert_item_name)) and alert_message_lang == 'hi':
                        alert_item_name = await _translate_text_to_target_language(alert_item_name, alert_message_lang)

                    supplier_info = await find_cheapest_supplier_for_item(alert_item_name, item['unit'])
                    supplier_details = ""
                    if supplier_info:
                        supplier_name = supplier_info["supplier_name"]
                        price = supplier_info["price_price_per_unit"]
                        supplier_phone = supplier_info["phone"]
                        supplier_details = f" ({cheapest_text}: {supplier_name}, ₹{price:.2f}/{item['unit']}, {call_text}: {supplier_phone})"

                    low_stock_messages.append(f"• {alert_item_name}: {item['quantity']} {item['unit']} ({min_text}: {item['min_quantity_threshold']} {item['unit']}){supplier_details}")
                
                alert_message = MESSAGES[alert_message_lang]["low_stock_alert"].format(
                    low_stock_items_list="\n".join(low_stock_messages))
                print(f"DEBUG_SCHEDULER: Prepared alert message for {user_id}: {alert_message}")
                await send_whatsapp_message(user_id, alert_message)
                print(f"DEBUG_SCHEDULER: Sent low stock alert to {user_id}.")
            else:
                print(f"DEBUG_SCHEDULER: No low stock items found for {user_id}.")
    except Exception as e:
        print(f"ERROR_SCHEDULER: Error during low stock check: {e}")

# Dictionary to store call states (e.g., conversation history, order details)
# In a production environment, this would be stored in a database.
call_states = {}

async def generate_speech_from_text(text: str, voice: str = "nova", model: str = "tts-1", is_ssml: bool = False) -> str:
    """Generates speech from text using OpenAI's TTS API and returns the audio file path."""
    try:
        # Ensure the audio directory exists
        audio_dir = "./audio"
        os.makedirs(audio_dir, exist_ok=True)

        # Generate a unique filename to avoid conflicts
        unique_id = f"{asyncio.current_task().get_name()}_{os.urandom(4).hex()}"
        speech_file_path = os.path.join(audio_dir, f"response_{unique_id}.mp3")

        input_text = text

        with client.audio.speech.with_streaming_response.create(
            model=model,
            voice=voice,
            input=input_text
        ) as response:
            response.stream_to_file(speech_file_path)
        print(f"DEBUG_TTS: Generated speech to {speech_file_path} (SSML: {is_ssml})")
        return speech_file_path
    except Exception as e:
        print(f"ERROR_TTS: Failed to generate speech from text '{text}': {e}")
        return "" # Return empty string on failure

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

@app.route("/twilio_voice_webhook", methods=['POST'])
async def twilio_voice_webhook():
    voice_response = VoiceResponse()

    # Extract call context from URL parameters
    item_name = request.args.get("item_name")
    quantity = request.args.get("quantity")
    unit = request.args.get("unit")
    supplier_name = request.args.get("supplier_name")
    user_id = request.args.get("user_id")
    detected_language = request.args.get("detected_language")
    call_sid = request.form.get('CallSid')

    print(f"DEBUG_TWILIO_WEBHOOK: Received Twilio Voice webhook for CallSid: {call_sid}")
    print(f"DEBUG_TWILIO_WEBHOOK: Call context: item_name={item_name}, quantity={quantity}, unit={unit}, supplier_name={supplier_name}, user_id={user_id}, detected_language={detected_language}")

    # Initialize conversation history for this call_sid if it doesn't exist
    if call_sid not in call_states:
        call_states[call_sid] = {
            "conversation_history": [
                {"role": "system", "content": f"""
                You are a friendly, helpful, and polite AI assistant representing Gupta Kirana Store. 
                Your goal is to confirm orders, delivery times, and prices. 
                Speak naturally and politely in Hindi. Be concise and clear. 
                If you don't get all the details, explain what's missing and politely ask the supplier to confirm.
                
                Conversation flow:
                1. First say: 'नमस्ते, मैं गुप्ता किराना स्टोर से रमा बात कर रही हूँ। क्या मैं {supplier_name} से बात कर सकती हूँ?'
                2. If the right person is on the line, ask: 'बहुत-बहुत धन्यवाद। मैं आपको {item_name} के स्टॉक के बारे में पूछने के लिए कॉल कर रही हूँ।'
                3. Then ask: 'हमारे पास {item_name} की कमी हो रही है। क्या आपके पास अभी {quantity} {unit} {item_name} उपलब्ध है? और क्या आप मुझे {item_name} का वर्तमान मूल्य प्रति {unit} बता सकते हैं?'
                4. If the item is available and price is confirmed, say: 'बहुत अच्छा। कृपया हमारे लिए {quantity} {unit} {item_name} बुक कर दें। क्या आप डिलीवरी का अनुमानित समय बता सकते हैं?'
                5. If item is not available or price is too high, say: 'ठीक है, समझने के लिए धन्यवाद। मैं दुकान के मालिक को सूचित कर दूँगी।'
                6. If supplier confirms booking, say: 'बहुत-बहुत धन्यवाद! हम डिलीवरी का इंतजार करेंगे। नमस्कार।'
                
                Important notes:
                - Only speak one or two sentences at a time
                - Wait for the supplier's response before continuing
                - Be natural and conversational
                - If you don't understand, politely ask for clarification
                - If there's silence, ask if they can hear you
                - If the call is breaking up, ask them to speak up or call back
                - Always be polite and professional
                """}
            ],
            "order_details": {
                "item_name": item_name,
                "quantity": quantity,
                "unit": unit,
                "supplier_name": supplier_name,
                "user_id": user_id,
                "detected_language": detected_language,
                "confirmed_price": None,
                "confirmed_delivery_time": None,
                "order_confirmed_status": False,
                "conversation_stage": "initial_greeting",
                "retry_count": 0
            }
        }
    
    # Get the current call state
    call_state = call_states[call_sid]
    
    # Only send the initial greeting if we're at the start of the conversation
    if call_state["order_details"]["conversation_stage"] == "initial_greeting":
        # First message to the supplier
        initial_message = MESSAGES[detected_language]["call_initiated"].format(
            supplier_name=supplier_name
        )
        call_state["conversation_history"].append({"role": "assistant", "content": initial_message})
        
        # Wrap the initial message in SSML to introduce a pause
        ssml_initial_message = f"<speak>{initial_message}<break time=\"1s\"/></speak>"
        speech_file_path = await generate_speech_from_text(ssml_initial_message, voice="nova", is_ssml=True)
        
        # Update conversation stage
        call_state["order_details"]["conversation_stage"] = "waiting_for_response"
    else:
        # This is a subsequent message in the conversation
        # Get the last assistant message to repeat it
        last_messages = [msg for msg in call_state["conversation_history"] if msg["role"] == "assistant"]
        if last_messages:
            last_message = last_messages[-1]["content"]
            speech_file_path = await generate_speech_from_text(f"<speak>{last_message}<break time=\"1s\"/></speak>", voice="nova", is_ssml=True)
    
    # Always include a gather to listen for the supplier's response
    if speech_file_path:
        audio_url = f"{BASE_URL}/audio/{os.path.basename(speech_file_path)}"
        voice_response.play(url=audio_url)
        print(f"DEBUG_TWILIO_WEBHOOK: Playing generated audio from {audio_url}")
    elif 'initial_message' in locals():
        print("ERROR_TWILIO_WEBHOOK: TTS generation failed. Falling back to <Say>.")
        voice_response.say(initial_message, voice='Polly.Aditi', language='hi-IN')
    
    # Add a small pause before gathering input
    voice_response.pause(length=1)
    
    # Gather speech from the supplier with increased timeout and better error handling
    gather_action_url = f"{BASE_URL}/handle_twilio_speech?call_sid={call_sid}"
    voice_response.gather(
        input='speech',
        timeout=10,  # Increased from 5 to 10 seconds
        speechTimeout='auto',
        action=gather_action_url,
        method='POST',
        actionOnEmptyResult=True,  # Important to handle cases where no speech is detected
        enhanced=True,  # Use enhanced speech recognition
        speechModel='phone_call',  # Optimize for phone call quality
        language='hi-IN'  # Specify Hindi language for better recognition
    )
    
    # If no input is received, redirect back to the same URL to try again
    query_string = request.query_string.decode()
    voice_response.redirect(
        method='POST',
        url=f"{BASE_URL}/twilio_voice_webhook?{query_string}"
    )
    
    print(f"DEBUG_TWILIO_WEBHOOK: TwiML response: {str(voice_response)}")
    return str(voice_response)

@app.route("/handle_twilio_speech", methods=['POST'])
async def handle_twilio_speech():
    voice_response = VoiceResponse()
    call_sid = request.args.get('call_sid')
    speech_result = request.form.get('SpeechResult')
    
    print(f"DEBUG_TWILIO_SPEECH: Received speech for CallSid: {call_sid}. SpeechResult: {speech_result}")

    if call_sid not in call_states:
        print(f"ERROR_TWILIO_SPEECH: Call SID {call_sid} not found in call_states")
        voice_response.say("Sorry, an application error occurred. Please call back later.", voice='Polly.Aditi', language='hi-IN')
        voice_response.hangup()
        return str(voice_response)

    current_call_state = call_states[call_sid]
    conversation_history = current_call_state["conversation_history"]
    order_details = current_call_state["order_details"]
    
    # Reset retry count if we got a response
    order_details['retry_count'] = 0
    
    # If no speech was detected, handle it gracefully
    if not speech_result or speech_result.strip() == '':
        print("WARNING_TWILIO_SPEECH: No speech detected or empty response")
        
        # Increment retry count
        order_details['retry_count'] = order_details.get('retry_count', 0) + 1
        
        # If we've retried too many times, end the call
        if order_details['retry_count'] > 2:
            response_text = "माफ़ कीजिए, हम आपको सुन नहीं पा रहे हैं। कृपया बाद में कॉल करें। धन्यवाद।"
            voice_response.say(response_text, voice='Polly.Aditi', language='hi-IN')
            voice_response.hangup()
            return str(voice_response)
        
        # Otherwise, ask the supplier to speak again
        response_text = "माफ़ कीजिए, मैंने आपको स्पष्ट रूप से नहीं सुना। क्या आप कृपया फिर से बोल सकते हैं?"
        conversation_history.append({"role": "assistant", "content": response_text})
        
        # Generate speech from text
        speech_file_path = await generate_speech_from_text(response_text, voice="nova")
        
        # Add the speech to the response
        if speech_file_path:
            audio_url = f"{BASE_URL}/audio/{os.path.basename(speech_file_path)}"
            voice_response.play(url=audio_url)
            print(f"DEBUG_TWILIO_SPEECH: Playing generated audio from {audio_url}")
        else:
            print("WARNING_TWILIO_SPEECH: TTS generation failed. Falling back to default TTS.")
            voice_response.say(response_text, voice='Polly.Aditi', language='hi-IN')
        
        # Add a small pause before next action
        voice_response.pause(length=1)
        
        if order_details.get("order_confirmed_status", False):
            # If order is confirmed, end the call gracefully
            voice_response.say("धन्यवाद, आपका दिन शुभ हो।", voice='Polly.Aditi', language='hi-IN')
            voice_response.hangup()
            # Clean up call state after a short delay
            async def cleanup_call_state():
                await asyncio.sleep(5)  # Wait for the call to end
                if call_sid in call_states:
                    del call_states[call_sid]
                    print(f"DEBUG_TWILIO_SPEECH: Cleaned up call state for {call_sid}")
            asyncio.create_task(cleanup_call_state())
            return str(voice_response)
        else:
            # Continue the conversation
            gather_action_url = f"{BASE_URL}/handle_twilio_speech?call_sid={call_sid}"
            voice_response.gather(
                input='speech',
                timeout=10,
                speechTimeout='auto',
                action=gather_action_url,
                method='POST',
                actionOnEmptyResult=True,
                enhanced=True,
                speechModel='phone_call',
                language='hi-IN'
            )
            # Add a redirect as a fallback in case gather fails
            redirect_params = {
                'item_name': order_details['item_name'],
                'quantity': order_details['quantity'],
                'unit': order_details['unit'],
                'supplier_name': order_details['supplier_name'],
                'user_id': order_details['user_id'],
                'detected_language': order_details['detected_language']
            }
            encoded_redirect = urllib.parse.urlencode(redirect_params)
            voice_response.redirect(
                method='POST',
                url=f"{BASE_URL}/twilio_voice_webhook?{encoded_redirect}"
            )
        return str(voice_response)

    # Process the speech result
    if speech_result:
        # Add user's speech to conversation history
        conversation_history.append({"role": "user", "content": speech_result})
        
        try:
            # Get response from OpenAI
            openai_response_text = await get_openai_response(conversation_history)
            text_to_speak = openai_response_text.strip()
            
            # Check for empty response
            if not text_to_speak:
                print("ERROR_TWILIO_SPEECH: OpenAI returned an empty response.")
                text_to_speak = "माफ़ कीजिए, कुछ तकनीकी समस्या आई है। कृपया थोड़ी देर बाद कोशिश करें।"
            
            # Add assistant's response to conversation history
            conversation_history.append({"role": "assistant", "content": text_to_speak})
            
            # Generate speech from text
            speech_file_path = await generate_speech_from_text(text_to_speak, voice="nova")
            
            # Check if this is an order confirmation
            confirmation_phrases = ["order confirmed", "confirmed the order", "बुक कर दें", "हां बुक कर दो", 
                                 "ठीक है, बुक कर दो", "डिलीवरी का इंतजार करेंगे", "ऑर्डर की पुष्टि हो गई है"]
            order_confirmed = any(phrase in text_to_speak.lower() for phrase in confirmation_phrases)
            
            if order_confirmed:
                order_details["order_confirmed_status"] = True
                print("DEBUG_TWILIO_SPEECH: Detected order confirmation phrase.")
                
                # Send WhatsApp confirmation (in background, don't wait for it to complete)
                async def send_whatsapp_confirmation():
                    try:
                        print("DEBUG_TWILIO_SPEECH: Order confirmed. Sending WhatsApp...")
                        whatsapp_message = MESSAGES["hi"]["order_confirmed_shopkeeper"].format(
                            item_name=order_details["item_name"], 
                            quantity=order_details["quantity"], 
                            unit=order_details["unit"], 
                            supplier_name=order_details["supplier_name"]
                        )
                        await send_whatsapp_message(order_details["user_id"], whatsapp_message)
                        print("DEBUG_TWILIO_SPEECH: WhatsApp confirmation sent successfully")
                    except Exception as e:
                        print(f"ERROR_TWILIO_SPEECH: Failed to send WhatsApp: {str(e)}")
                        # If WhatsApp fails, we'll just log it and continue with the call
                
                # Start WhatsApp sending in the background
                asyncio.create_task(send_whatsapp_confirmation())
                
                # Ask for delivery time
                delivery_prompt = "कृपया डिलीवरी का समय बताएं। कितने दिनों में डिलीवरी हो सकती है?"
                text_to_speak = f"{text_to_speak} {delivery_prompt}"
                speech_file_path = await generate_speech_from_text(text_to_speak, voice="nova")
                
                # Don't mark as confirmed yet, wait for delivery time confirmation
                order_confirmed = False
            
            # Add the speech to the response
            if speech_file_path:
                audio_url = f"{BASE_URL}/audio/{os.path.basename(speech_file_path)}"
                voice_response.play(url=audio_url)
                print(f"DEBUG_TWILIO_SPEECH: Playing generated audio from {audio_url}")
            else:
                print("WARNING_TWILIO_SPEECH: TTS generation failed. Falling back to default TTS.")
                voice_response.say(text_to_speak, voice='Polly.Aditi', language='hi-IN')
            
            # Add a small pause before next action
            voice_response.pause(length=1)
            
            if order_confirmed:
                # After getting delivery time, confirm and end the call
                voice_response.say("धन्यवाद, आपका ऑर्डर कन्फर्म हो गया है। आपको एक कन्फर्मेशन व्हाट्सएप पर भेज दिया गया है। आपका दिन शुभ हो।", 
                                 voice='Polly.Aditi', 
                                 language='hi-IN')
                voice_response.hangup()
                
                # Clean up call state after a short delay
                async def cleanup_call_state():
                    await asyncio.sleep(5)  # Wait for the call to end
                    if call_sid in call_states:
                        del call_states[call_sid]
                        print(f"DEBUG_TWILIO_SPEECH: Cleaned up call state for {call_sid}")
                
                asyncio.create_task(cleanup_call_state())
            else:
                # Continue the conversation
                gather_action_url = f"{BASE_URL}/handle_twilio_speech?call_sid={call_sid}"
                voice_response.gather(
                    input='speech',
                    timeout=10,
                    speechTimeout='auto',
                    action=gather_action_url,
                    method='POST',
                    actionOnEmptyResult=True,
                    enhanced=True,
                    speechModel='phone_call',
                    language='hi-IN'
                )
                
                # Add a redirect as a fallback in case gather fails
                params = {
                    'item_name': order_details['item_name'],
                    'quantity': order_details['quantity'],
                    'unit': order_details['unit'],
                    'supplier_name': order_details['supplier_name'],
                    'user_id': order_details['user_id'],
                    'detected_language': order_details['detected_language']
                }
                encoded_params = urllib.parse.urlencode(params)
                voice_response.redirect(
                    method='POST',
                    url=f"{BASE_URL}/twilio_voice_webhook?{encoded_params}"
                )
            
        except Exception as e:
            print(f"ERROR_TWILIO_SPEECH: Error processing OpenAI response: {str(e)}")
            error_msg = "माफ़ कीजिए, कुछ तकनीकी समस्या आई है। कृपया थोड़ी देर बाद कोशिश करें।"
            voice_response.say(error_msg, voice='Polly.Aditi', language='hi-IN')
            voice_response.hangup()
            
            # Clean up call state on error
            if call_sid in call_states:
                del call_states[call_sid]
                print(f"DEBUG_TWILIO_SPEECH: Call state cleaned up for {call_sid} due to error")
            if any(phrase in text_to_speak.lower() for phrase in confirmation_phrases):
                order_details["order_confirmed_status"] = True
                print("DEBUG_TWILIO_SPEECH: Detected order confirmation phrase.")
                
                # Send WhatsApp confirmation
                try:
                    print("DEBUG_TWILIO_SPEECH: Order confirmed. Sending WhatsApp and ending the call.")
                    whatsapp_message = MESSAGES["hi"]["order_confirmed_shopkeeper"].format(
                        item_name=order_details["item_name"], 
                        quantity=order_details["quantity"], 
                        unit=order_details["unit"], 
                        supplier_name=order_details["supplier_name"]
                    )
                    await send_whatsapp_message(order_details["user_id"], whatsapp_message)
                    
                    # Add confirmation to the response
                    text_to_speak += " आपको एक कन्फर्मेशन व्हाट्सएप पर भेज दिया गया है। धन्यवाद!"
                    speech_file_path = await generate_speech_from_text(text_to_speak, voice="nova")
                except Exception as e:
                    print(f"ERROR_TWILIO_SPEECH: Failed to send WhatsApp: {str(e)}")
                    text_to_speak += " लेकिन व्हाट्सएप कन्फर्मेशन भेजने में समस्या आई। कृपया मैन्युअल रूप से चेक करें।"

            speech_file_path = await generate_speech_from_text(text_to_speak, voice="nova")
            if speech_file_path:
                voice_response.play(url=f"{BASE_URL}/audio/{os.path.basename(speech_file_path)}")
            else:
                voice_response.say(text_to_speak, voice='Polly.Aditi', language='hi-IN')
            
            voice_response.hangup()
            if call_sid in call_states: 
                del call_states[call_sid]
            return str(voice_response)
            
    else: # No speech was detected by <Gather>
        text_to_speak = "माफ़ कीजिए, मुझे आपकी आवाज़ सुनाई नहीं दी। क्या आप दोहरा सकते हैं?"
        conversation_history.append({"role": "assistant", "content": text_to_speak})

    # --- This block now runs for ALL continuing conversation turns ---
    ssml_response = f"<speak>{text_to_speak}<break time=\"1s\"/></speak>"
    speech_file_path = await generate_speech_from_text(ssml_response, voice="nova", is_ssml=True)
    
    if speech_file_path:
        voice_response.play(url=f"{BASE_URL}/audio/{os.path.basename(speech_file_path)}")
    else:
        voice_response.say(text_to_speak, voice='Polly.Aditi', language='hi-IN')

    # *** FIX: Always re-gather to keep the conversation going ***
    action_url = f"{BASE_URL}/handle_twilio_speech?call_sid={call_sid}"
    voice_response.gather(input='speech', speechTimeout='auto', action=action_url, method='POST')
    
    print(f"DEBUG_TWILIO_SPEECH: TwiML response (Continue): {str(voice_response)}")
    return str(voice_response)


@app.route("/audio/<filename>")
def serve_audio(filename):
    print(f"DEBUG: Serving audio file: {filename}")
    try:
        return send_from_directory('audio', filename)
    except Exception as e:
        print(f"ERROR: Failed to serve audio file {filename}: {str(e)}")
        return ("Audio file not found", 404)

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
    """Generates local insights and personalized recommendations for all shopkeepers with stock, including weather-based, festival-based, and low-stock alerts."""
    print("DEBUG_INSIGHTS: Generating local insights...")
    unique_user_ids_raw = await get_all_unique_user_ids_with_stock()
    unique_user_ids = unique_user_ids_raw # Corrected: unique_user_ids_raw already contains strings

    # Fetch global data once
    latitude = SHOPKEEPER_LOCATION["latitude"]
    longitude = SHOPKEEPER_LOCATION["longitude"]
    weather_data = await get_weather_forecast(latitude, longitude)
    festivals_data = await get_festivals_from_llm(days_in_advance=60) # Increased days_in_advance to 60 for more festivals

    for user_id in unique_user_ids:
        print(f"DEBUG_INSIGHTS: Generating insights for user: {user_id}")
        insights = []
        recommendations = []
        all_recommended_item_names = set() # To keep track of all recommended items

        stock_levels = await get_stock_levels(user_id)
        stock_map = {item['item_name'].lower(): item for item in stock_levels}

        # --- Weather-based Insights and Recommendations ---
        if weather_data:
            current_temp = weather_data["current"]["temperature_2m"]
            print(f"DEBUG_WEATHER: Current temperature: {current_temp}°C") # Added debug log for current temperature

            if current_temp > 30:
                insights.append("It's hot today!")

                # Get generic weather-based item suggestions from LLM
                generic_weather_recommendation_prompt = f"""Based on hot weather, suggest 2-3 popular items (not necessarily from any specific inventory) that a shopkeeper should promote. 
Respond as a comma-separated list of item names only, with no additional text."""
                llm_generic_weather_recommendation_response = await asyncio.to_thread(
                    client.chat.completions.create,
                    model="gpt-3.5-turbo-0125", # Changed model
                    messages=[
                        {"role": "system", "content": "You are a helpful assistant that suggests popular items based on weather conditions."},
                        {"role": "user", "content": generic_weather_recommendation_prompt}
                    ],
                    temperature=1.0,
                    max_tokens=100
                )
                generic_weather_recommended_items_str = llm_generic_weather_recommendation_response.choices[0].message.content.strip()
                print(f"DEBUG_LLM_RECOMMENDATION: LLM suggested generic items for hot weather: {generic_weather_recommended_items_str}")

                if generic_weather_recommended_items_str:
                    for item_name in [item.strip() for item in generic_weather_recommended_items_str.split(',')]:
                        all_recommended_item_names.add(item_name.lower())
                
                # Original inventory-specific recommendations (if still desired in primary recs)
                weather_recommendation_prompt = f"""Based on hot weather, suggest 2-3 specific items from the following inventory that a shopkeeper should promote. 
Inventory: {', '.join(stock_map.keys())}.
Respond as a comma-separated list of item names only, with no additional text. If no relevant items, respond with 'None'."""

                print(f"DEBUG_LLM_RECOMMENDATION: Prompt sent to LLM for hot weather: {weather_recommendation_prompt}") # Added logging for the prompt

                llm_weather_recommendation_response = await asyncio.to_thread(
                    client.chat.completions.create,
                    model="gpt-3.5-turbo-0125", # Changed model
                    messages=[
                        {"role": "system", "content": "You are a helpful assistant that suggests inventory items based on weather conditions."},
                        {"role": "user", "content": weather_recommendation_prompt}
                    ],
                    temperature=1.0, 
                    max_tokens=100
                )
                weather_recommended_items_str = llm_weather_recommendation_response.choices[0].message.content.strip()
                
                print(f"DEBUG_LLM_RECOMMENDATION: LLM recommended for hot weather: {weather_recommended_items_str}")

                if weather_recommended_items_str and weather_recommended_items_str.lower() != 'none':
                    weather_recommended_items_list = [item.strip().lower() for item in weather_recommended_items_str.split(',')]
                    found_weather_recommendations = False
                    for recommended_item_name in weather_recommended_items_list:
                        matched_item = next((item_name for item_name in stock_map if fuzz.token_sort_ratio(recommended_item_name, item_name) >= 70), None)
                        if matched_item:
                            item_details = stock_map[matched_item]
                            suggested_price = item_details.get("cost_price_per_unit", 0) * 1.20 # 20% markup for weather items
                            recommendations.append(f"Suggest selling {item_details['item_name']} for ₹{suggested_price:.2f} per {item_details['unit']} due to hot weather.")
                            # all_recommended_item_names.add(item_details['item_name'].lower()) # Already added above for generic
                            found_weather_recommendations = True
                    if not found_weather_recommendations:
                        # Ensure at least one recommendation is added for weather even if specific inventory items aren't matched
                        recommendations.append(f"Consider stocking popular items like {weather_recommended_items_str if weather_recommended_items_str else 'cold drinks and ice cream'} due to hot weather.")
                else:
                    recommendations.append("Consider stocking cold drinks and ice cream due to hot weather.")
                print("DEBUG_WEATHER_RECOMMENDATIONS_GENERATED: Weather-based recommendations added.") # Debug log

            elif current_temp < 15:
                insights.append("It's chilly!")

                # Get generic weather-based item suggestions from LLM
                generic_weather_recommendation_prompt = f"""Based on chilly weather, suggest 2-3 popular items (not necessarily from any specific inventory) that a shopkeeper should promote. 
Respond as a comma-separated list of item names only, with no additional text."""
                llm_generic_weather_recommendation_response = await asyncio.to_thread(
                    client.chat.completions.create,
                    model="gpt-3.5-turbo-0125", # Changed model
                    messages=[
                        {"role": "system", "content": "You are a helpful assistant that suggests popular items based on weather conditions."},
                        {"role": "user", "content": generic_weather_recommendation_prompt}
                    ],
                    temperature=1.0,
                    max_tokens=100
                )
                generic_weather_recommended_items_str = llm_generic_weather_recommendation_response.choices[0].message.content.strip()
                print(f"DEBUG_LLM_RECOMMENDATION: LLM suggested generic items for chilly weather: {generic_weather_recommended_items_str}")

                if generic_weather_recommended_items_str:
                    for item_name in [item.strip() for item in generic_weather_recommended_items_str.split(',')]:
                        all_recommended_item_names.add(item_name.lower())

                # Original inventory-specific recommendations (if still desired in primary recs)
                weather_recommendation_prompt = f"""Based on chilly weather, suggest 2-3 specific items from the following inventory that a shopkeeper should promote. 
Inventory: {', '.join(stock_map.keys())}.
Respond as a comma-separated list of item names only, with no additional text. If no relevant items, respond with 'None'."""

                print(f"DEBUG_LLM_RECOMMENDATION: Prompt sent to LLM for chilly weather: {weather_recommendation_prompt}") # Added logging for the prompt

                llm_weather_recommendation_response = await asyncio.to_thread(
                    client.chat.completions.create,
                    model="gpt-3.5-turbo-0125", # Changed model
                    messages=[
                        {"role": "system", "content": "You are a helpful assistant that suggests inventory items based on weather conditions."},
                        {"role": "user", "content": weather_recommendation_prompt}
                    ],
                    temperature=1.0, 
                    max_tokens=100
                )
                weather_recommended_items_str = llm_weather_recommendation_response.choices[0].message.content.strip()
                print(f"DEBUG_LLM_RECOMMENDATION: LLM recommended for chilly weather: {weather_recommended_items_str}")

                if weather_recommended_items_str and weather_recommended_items_str.lower() != 'none':
                    weather_recommended_items_list = [item.strip().lower() for item in weather_recommended_items_str.split(',')]
                    found_weather_recommendations = False
                    for recommended_item_name in weather_recommended_items_list:
                        matched_item = next((item_name for item_name in stock_map if fuzz.token_sort_ratio(recommended_item_name, item_name) >= 70), None)
                        if matched_item:
                            item_details = stock_map[matched_item]
                            suggested_price = item_details.get("cost_price_per_unit", 0) * 1.15 # 15% markup for weather items
                            recommendations.append(f"Suggest selling {item_details['item_name']} for ₹{suggested_price:.2f} per {item_details['unit']} due to chilly weather.")
                            # all_recommended_item_names.add(item_details['item_name'].lower()) # Already added above for generic
                            found_weather_recommendations = True
                    if not found_weather_recommendations:
                        # Ensure at least one recommendation is added for weather even if specific inventory items aren't matched
                        recommendations.append(f"Consider stocking popular items like {weather_recommended_items_str if weather_recommended_items_str else 'warm beverages and comfort food'} due to chilly weather.")
                else:
                    recommendations.append("Consider stocking warm beverages and comfort food due to chilly weather.")
                print("DEBUG_WEATHER_RECOMMENDATIONS_GENERATED: Weather-based recommendations added.") # Debug log

        # --- Festival-based Insights and Recommendations ---
        if festivals_data:
            for festival in festivals_data[:5]: # Limit to 5 festivals
                festival_name = festival.get("name")
                festival_date_info = festival.get("date", "")
                insights.append(f"Upcoming festival: {festival_name} (around {festival_date_info}).")

                # Get generic festival-based item suggestions from LLM
                generic_festival_recommendation_prompt = f"""For the upcoming festival '{festival_name}' (around {festival_date_info}), suggest 2-3 generic items (not necessarily from any specific inventory) that a shopkeeper should promote. 
Consider items that are commonly used as ingredients for sweets, special dishes, traditional offerings, or festive decorations during Indian festivals. 
Respond as a comma-separated list of item names only, with no additional text."""
                llm_generic_festival_recommendation_response = await asyncio.to_thread(
                    client.chat.completions.create,
                    model="gpt-3.5-turbo-0125", # Changed model
                    messages=[
                        {"role": "system", "content": "You are a helpful assistant that suggests popular items for Indian festivals."},
                        {"role": "user", "content": generic_festival_recommendation_prompt}
                    ],
                    temperature=1.0,
                    max_tokens=100
                )
                generic_festival_recommended_items_str = llm_generic_festival_recommendation_response.choices[0].message.content.strip()
                print(f"DEBUG_LLM_RECOMMENDATION: LLM suggested generic items for {festival_name}: {generic_festival_recommended_items_str}")

                if generic_festival_recommended_items_str:
                    for item_name in [item.strip() for item in generic_festival_recommended_items_str.split(',')]:
                        all_recommended_item_names.add(item_name.lower())

                # Original inventory-specific recommendations (if still desired in primary recs)
                inventory_items = ", ".join(stock_map.keys())
                recommendation_prompt = f"""For the upcoming festival '{festival_name}' (around {festival_date_info}), suggest 2-3 specific items from the following inventory that a shopkeeper should promote. 
Consider items that are commonly used as ingredients for sweets, special dishes, or other relevant preparations during Indian festivals.
Inventory: {inventory_items}.
Respond as a comma-separated list of item names only, with no additional text. If no relevant items, respond with 'None'."""
                
                print(f"DEBUG_LLM_RECOMMENDATION: Prompt sent to LLM for {festival_name}: {recommendation_prompt}") # Added logging for the prompt

                llm_recommendation_response = await asyncio.to_thread(
                    client.chat.completions.create,
                    model="gpt-3.5-turbo-0125", # Changed model
                    messages=[
                        {"role": "system", "content": "You are a helpful assistant that suggests inventory items for festivals."},
                        {"role": "user", "content": recommendation_prompt}
                    ],
                    temperature=1.0, 
                    max_tokens=100
                )

                recommended_items_str = llm_recommendation_response.choices[0].message.content.strip()

                print(f"DEBUG_LLM_RECOMMENDATION: LLM recommended for {festival_name}: {recommended_items_str}")

                if recommended_items_str and recommended_items_str.lower() != 'none':
                    recommended_items_list = [item.strip().lower() for item in recommended_items_str.split(',')]
                    found_recommendations = False
                    for recommended_item_name in recommended_items_list:
                        matched_item = next((item_name for item_name in stock_map if fuzz.token_sort_ratio(recommended_item_name, item_name) >= 70), None)
                        if matched_item:
                            item_details = stock_map[matched_item]
                            suggested_price = item_details.get("cost_price_per_unit", 0) * 1.15 # 15% markup for festival items
                            recommendations.append(f"Promote {item_details['item_name']} for {festival_name} at ₹{suggested_price:.2f} per {item_details['unit']}.")
                            all_recommended_item_names.add(item_details['item_name'].lower())
                            found_recommendations = True
                    if not found_recommendations:
                        recommendations.append(f"Consider stocking items like {recommended_items_str} for the upcoming {festival_name}.")
                else:
                    recommendations.append(f"Consider stocking general festival items for the upcoming {festival_name}.")
        
        # --- Low Stock Alert (Integrated) ---
        critical_low_stock_items = []
        for item_name, item_details in stock_map.items():
            if item_details.get("quantity", 0) < item_details.get("min_quantity_threshold", 0):
                critical_low_stock_items.append(item_details)

        recommended_items_for_stocking_consideration = set()
        for recommended_item_name in all_recommended_item_names:
            recommended_items_for_stocking_consideration.add(recommended_item_name)
        
        low_stock_alert_message = ""
        low_stock_message_parts = []

        if critical_low_stock_items:
            print(f"DEBUG_INSIGHTS: Found critical low stock items for {user_id}: {critical_low_stock_items}")
            low_stock_message_parts.append("⚠️ **कम स्टॉक चेतावनी!** निम्नलिखित आइटम कम हो रहे हैं:") # Bold for emphasis
            for item in critical_low_stock_items:
                item_name = item['item_name']
                quantity = item['quantity']
                unit = item['unit']
                min_threshold = item['min_quantity_threshold']

                cheapest_supplier_info = await find_cheapest_supplier_for_item(item_name, unit)
                supplier_details = ""
                if cheapest_supplier_info:
                    supplier_name = cheapest_supplier_info["supplier_name"]
                    price = cheapest_supplier_info["price_per_unit"]
                    supplier_phone = cheapest_supplier_info["phone"]
                    supplier_details = f" (सबसे सस्ता: {supplier_name}, ₹{price:.2f}/{unit}, कॉल: {supplier_phone})"
                low_stock_message_parts.append(f"• {item_name}: {quantity} {unit} (न्यूनतम: {min_threshold} {unit}){supplier_details}")
            low_stock_message_parts.append("जल्द ही ऑर्डर करने पर विचार करें।") # Closing sentence
        
        if recommended_items_for_stocking_consideration:
            if low_stock_message_parts: # Add a separator if there are critical low stock items
                low_stock_message_parts.append("\n") 
            low_stock_message_parts.append("✨ **विचार करने के लिए अनुशंसित आइटम:**") # New section title
            
            # Prioritize items not in stock, then add other recommended items, limit to 5
            not_in_stock_items = []
            in_stock_items = []
            for rec_item_name in recommended_items_for_stocking_consideration:
                if rec_item_name in stock_map:
                    item_details = stock_map[rec_item_name]
                    if item_details.get("quantity", 0) <= 0: # Treat zero quantity as not in stock for recommendation purposes
                        not_in_stock_items.append(rec_item_name)
                    else:
                        in_stock_items.append(rec_item_name)
                else:
                    not_in_stock_items.append(rec_item_name)
            
            # Combine and limit the list
            final_recommended_items = not_in_stock_items + in_stock_items
            for rec_item_name in final_recommended_items[:5]: # Limit to 5 items
                if rec_item_name in stock_map:
                    item_details = stock_map[rec_item_name]
                    status = "(आपके स्टॉक में)" if item_details.get("quantity", 0) > 0 else "(स्टॉक में नहीं/कम)"
                    low_stock_message_parts.append(f"• {item_details['item_name']} {status}")
                else:
                    low_stock_message_parts.append(f"• {rec_item_name} (आपके स्टॉक में नहीं)")

        if low_stock_message_parts:
            low_stock_alert_message = "\n".join(low_stock_message_parts)

        # --- Construct and Send Final Message ---
        final_message_parts = []
        if insights:
            final_message_parts.append("--- Local Insights ---")
            final_message_parts.extend([f"- {insight}" for insight in insights])
        if recommendations:
            final_message_parts.append("\n--- Recommendations for you ---")
            final_message_parts.extend([f"- {rec}" for rec in recommendations])
        
        # Append low stock alert after insights and recommendations
        if low_stock_alert_message:
            if final_message_parts: # Add a separator if there are previous messages
                final_message_parts.append("\n") 
            final_message_parts.append("--- Stocking Suggestions ---") # Changed section title
            final_message_parts.append(low_stock_alert_message)

        final_message_body = "\n".join(final_message_parts)

        if final_message_body:
            await send_whatsapp_message(user_id, final_message_body)
        else:
            print(f"DEBUG_INSIGHTS: No specific local insights or recommendations generated for {user_id} at this time.")

if __name__ == "__main__":
    # Initialize and start the scheduler when the app starts
    if not scheduler.running:
        scheduler.add_job(generate_local_insights, 'interval', seconds=30)
        # Removed the low stock alert scheduler job
        # scheduler.add_job(check_low_stock_and_alert, 'interval', seconds=30, id='check_low_stock_and_alert', replace_existing=True)
        scheduler_thread = Thread(target=_run_scheduler)
        scheduler_thread.daemon = True
        scheduler_thread.start()
        print("Scheduler for low stock alerts has been started in a separate thread.")

    # Run the Flask app
    # Using debug=True is helpful for development as it provides detailed errors
    # and automatically reloads the server when you make code changes.
    # Make sure to set debug=False for a production environment.
    app.run(debug=True, host="0.0.0.0", port=5000)