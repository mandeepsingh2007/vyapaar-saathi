import os
from dotenv import load_dotenv
from openai import OpenAI
import json
from datetime import date # Import date
import base64 # Import base64 for image encoding

load_dotenv()

client = OpenAI()

def transcribe_audio(audio_file_path: str) -> dict:
    """
    Transcribes an audio file to text using OpenAI Whisper.
    Assumes the audio file is in a supported format (e.g., mp3, wav, m4a).
    """
    try:
        with open(audio_file_path, "rb") as audio_file:
            # Transcribe without specifying a target language first to detect the original language
            # Then, transcribe again with translation to English
            initial_transcript = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="verbose_json", # Changed to verbose_json to get language info
            )
            print(f"DEBUG_TRANSCRIPT: Type of initial_transcript: {type(initial_transcript)}")
            print(f"DEBUG_TRANSCRIPT: Content of initial_transcript: {initial_transcript}")

            detected_language = initial_transcript.language
            original_transcription = initial_transcript.text

            # Now, get the English translation
            english_translation_transcript = client.audio.translations.create(
                model="whisper-1",
                file=audio_file,
                response_format="text",
            )
            english_translation = english_translation_transcript

        return {
            "detected_language": detected_language,
            "original_transcription": original_transcription,
            "english_translation": english_translation
        }
    except Exception as e:
        print(f"Error during transcription: {e}")
        return {"detected_language": "en", "original_transcription": "", "english_translation": ""}

def extract_structured_data(text: str, reference_date: date) -> dict:
    """
    Extracts structured data (date, type, amount/items_sold, item/description) from the given text
    using OpenAI's GPT model.
    If a date is not explicitly mentioned, it will use the reference_date.
    """
    # Format reference_date for the prompt
    formatted_reference_date = reference_date.strftime('%Y-%m-%d')

    # Define JSON examples as Python dictionaries, then convert to JSON strings
    sale_example_dict = {"date": "YYYY-MM-DD", "type": "sale", "items_sold": [{"item_name": "Item A", "quantity": 2.0, "unit": "kg", "selling_amount": 100.0}, {"item_name": "Item B", "quantity": 1.0, "unit": "pcs", "selling_amount": 50.0}]}
    expense_example_dict = {"date": "YYYY-MM-DD", "type": "expense", "amount": 50.00, "description": "Groceries"}
    purchase_example_dict = {"date": "YYYY-MM-DD", "type": "purchase", "items_purchased": [{"item_name": "Item C", "quantity": 5.0, "unit": "kg", "cost_price_per_unit": 200.0}, {"item_name": "Item D", "quantity": 10.0, "unit": "pcs", "cost_price_per_unit": 20.0}]}
    order_confirmation_example_dict = {"date": "YYYY-MM-DD", "type": "order_confirmation", "item_name": "Basmati Rice", "quantity": 10.0, "unit": "kg", "supplier_name": "Supplier A"}

    sale_example_json = json.dumps(sale_example_dict)
    expense_example_json = json.dumps(expense_example_dict)
    purchase_example_json = json.dumps(purchase_example_dict)
    order_confirmation_example_json = json.dumps(order_confirmation_example_dict)

    prompt = f"""
    From the following text, extract the transaction details.
    If a date is not explicitly mentioned, use today's date, which is {formatted_reference_date}.
    Determine the 'type' as either 'sale', 'purchase', 'expense', or 'order_confirmation'. If ambiguous, default to 'expense'.
    
    **ABSOLUTELY CRITICAL: For all numerical extractions (quantity, selling_amount, cost_price_per_unit), you MUST extract the EXACT numerical value as provided in the input text. DO NOT perform any rounding, approximation, or alteration. If the input is "eighty-five" or "पचासी", the extracted value MUST be 85.0. If the input is "one hundred ninety" or "एक सौ नब्बे", the extracted value MUST be 190.0. VERIFY ALL NUMBERS TWICE. For example, if the input is "1 kg rice at 85 rupees", the `selling_amount` for rice MUST be 85.0, not 80.0.**
    **For 'sale' transactions:**
    Extract `items_sold` which is a list of objects. Each object should have `item_name` (string, **in the language of the input text**), `quantity` (numeric, **CRITICAL AND ABSOLUTE: Extract the EXACT numerical quantity as spoken/written, no conversions or approximations. If 'half a kilo' is spoken, extract 0.5. If 'two hundred fifty grams' is spoken, extract 250.0.**), `unit` (string, **CRITICAL: MUST be in English ONLY and EXACTLY as spoken (e.g., 'kg', 'g', 'pcs', 'packet', 'dozen', 'litre', 'ml'). Do NOT perform unit conversions (e.g., if 'grams' is spoken, extract 'g' and the quantity in grams, not 'kg' and a converted quantity). NEVER use local language units like 'किलो' or 'लीटर'**), and `selling_amount` (numeric, **CRITICAL AND ABSOLUTE: Extract the EXACT monetary value. Convert any spelled-out numbers to digits. For example, if the input is "eighty-five rupees" or "पचासी रुपए", the `selling_amount` MUST be 85.0. If the input says "fifty rupees" or "पचास रुपए", the `selling_amount` MUST be 50.0. Do NOT misinterpret values; be extremely precise. VERIFY THE SELLING AMOUNT CAREFULLY AGAINST THE INPUT TEXT. For example, if 'एक सौ नब्बे रुपए' is spoken, `selling_amount` must be 190.0, not 199.0**).
    If the unit is not explicitly mentioned, default to 'pcs'.
    For example:
    ```json
    {{"date": "YYYY-MM-DD", "type": "sale", "items_sold": [{{"item_name": "Item A", "quantity": 2.0, "unit": "kg", "selling_amount": 100.0}}, {{"item_name": "Item B", "quantity": 1.0, "unit": "pcs", "selling_amount": 50.0}}]}}
    ```

    **For 'purchase' transactions:**
    Extract `items_purchased` which is a list of objects. Each object should have `item_name` (string, **in the language of the input text**), `quantity` (numeric, **CRITICAL AND ABSOLUTE: Extract the EXACT numerical quantity as spoken/written. Do NOT perform unit conversions.**), `unit` (string, **CRITICAL: MUST be in English ONLY and EXACTLY as spoken (e.g., 'kg', 'g', 'pcs', 'packet', 'dozen', 'litre', 'ml'). Do NOT perform unit conversions. NEVER use local language units like 'किलो' or 'लीटर'**), and `cost_price_per_unit` (numeric, **CRITICAL AND ABSOLUTE: Extract the EXACT monetary value. VERIFY THE COST PRICE CAREFULLY AGAINST THE INPUT TEXT.**).
    If the unit is not explicitly mentioned, default to 'pcs'.
    For example:
    ```json
    {{"date": "YYYY-MM-DD", "type": "purchase", "items_purchased": [{{"item_name": "Item C", "quantity": 5.0, "unit": "kg", "cost_price_per_unit": 200.0}}, {{"item_name": "Item D", "quantity": 10.0, "unit": "pcs", "cost_price_per_unit": 20.0}}]}}
    ```

    **For 'expense' transactions:**
    Extract the total 'amount' (numeric) and a brief 'description' (string) of the expense (e.g., "Groceries").
    
    **For 'order_confirmation' transactions:**
    Extract `item_name` (string, **in the language of the input text**), `quantity` (numeric, **CRITICAL AND ABSOLUTE: Extract the EXACT numerical quantity**), `unit` (string, **CRITICAL: MUST be in English ONLY and EXACTLY as spoken (e.g., 'kg', 'g', 'pcs')**), and `supplier_name` (string, **the name of the supplier**).
    If the unit is not explicitly mentioned, default to 'pcs'.
    For example:
    ```json
    {{"date": "YYYY-MM-DD", "type": "order_confirmation", "item_name": "Basmati Rice", "quantity": 10.0, "unit": "kg", "supplier_name": "Supplier A"}}
    ```

    Return the data as a JSON object with the following structure:
    
    **If 'type' is 'sale':**
    {sale_example_json}
    
    **If 'type' is 'purchase':**
    {purchase_example_json}

    **If 'type' is 'expense':**
    {expense_example_json}

    **If 'type' is 'order_confirmation':**
    {order_confirmation_example_json}
    
    Text: "{text}"
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o",  # Upgraded to GPT-4o for better extraction accuracy
            messages=[
                {"role": "system", "content": "You are a helpful assistant designed to extract structured transaction data from text. Pay extreme attention to extracting the exact numerical values for quantities and prices. Double-check all numbers against the input text."},
                {"role": "user", "content": prompt}
            ],
            response_format={"type": "json_object"}
        )
        content = response.choices[0].message.content
        print(f"DEBUG_EXTRACTOR: Raw GPT response content: {content}")
        # In case the content is wrapped in markdown, although response_format="json_object" should prevent it
        if content.startswith("```json") and content.endswith("```"):
            json_string = content[7:-3].strip()
            print(f"DEBUG_EXTRACTOR: JSON string after stripping markdown: {json_string}")
        else:
            json_string = content.strip()
            print(f"DEBUG_EXTRACTOR: JSON string (no markdown stripped): {json_string}")
        
        extracted_data = json.loads(json_string)
        print(f"DEBUG_EXTRACTOR: Extracted data: {extracted_data}")
        return extracted_data
    except json.JSONDecodeError as e:
        print(f"ERROR_EXTRACTOR: JSON Decode Error: {e}")
        print(f"ERROR_EXTRACTOR: GPT response content (on error): {content}")
        return {}
    except Exception as e:
        print(f"ERROR_EXTRACTOR: Error during data extraction: {e}")
        return {}


def encode_image(image_path):
    """Encodes an image file to a base64 string."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")

def extract_items_from_bill_image(image_file_path: str) -> dict:
    """Extracts item names and quantities from a bill image using OpenAI Vision API.

    Args:
        image_file_path: The path to the bill image file.

    Returns:
        A dictionary containing the bill type and a list of extracted items, 
        each with 'item_name', 'quantity', 'unit', 'num_packets', 
        'cost_price_per_unit', and 'selling_price_per_unit'.
        Example: {"bill_type": "purchase", "items": [{'item_name': 'Milk', 'quantity': 2.0, 'unit': 'kg', 'num_packets': 1, 'cost_price_per_unit': 50.0, 'selling_price_per_unit': null}]}
    """
    try:
        base64_image = encode_image(image_file_path)
        
        # Define the JSON example as a Python dictionary, then convert to JSON string
        bill_example_dict = {"bill_type": "purchase", "items": [{"item_name": "Milk", "quantity": 2.0, "unit": "kg", "num_packets": 1, "cost_price_per_unit": 50.0, "selling_price_per_unit": None}, {"item_name": "Bread", "quantity": 1.0, "unit": "packet", "num_packets": 2, "cost_price_per_unit": None, "selling_price_per_unit": 30.0}]}
        bill_example_json = json.dumps(bill_example_dict)
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"From this bill photo, first determine the primary language of the text on the bill, returning its ISO 639-1 code (e.g., 'en' for English, 'hi' for Hindi, 'pa' for Punjabi, 'gu' for Gujarati, 'ta' for Tamil, 'te' for Telugu, 'bn' for Bengali, 'mr' for Marathi). If the language is ambiguous or not one of these, default to 'en'. Then, determine if it is a 'purchase invoice' (from a supplier) or a 'sales receipt' (to a customer). If ambiguous, categorize as 'unknown'. To classify, look for keywords like \"invoice\", \"purchase\", \"supplier\" for purchases, or \"receipt\", \"sale\", \"customer\" for sales. **Crucially, if there is a column of prices on the far right and the items listed are typical inventory for a shopkeeper, interpret these prices as `cost_price_per_unit` and classify the bill as a 'purchase' invoice.** If prices are listed in a way that clearly indicates what the shopkeeper sold items for, assume they are **selling_price_per_unit** and the bill is a 'sale' receipt. Then, extract the item names, their precise quantities (including fractional values like 0.5 for 1/2), their corresponding units (e.g., kg, g, dozen, pcs, packet), the number of packets/items (the standalone number in a separate column), the **cost price per unit** (if written on the bill, often next to the item or quantity, and typically on purchase invoices). **IMPORTANT: If a total price is given for a quantity (e.g., '500 g Rajma, ₹80'), calculate the `cost_price_per_unit` as the total price divided by the quantity. For gram units, ensure `cost_price_per_unit` is truly per gram (e.g., for '500 g Rajma, ₹80', `cost_price_per_unit` should be 0.16).** And the **selling price per unit** (if written on the bill, often next to the item or quantity, and typically on sales receipts). **Prioritize quantity and unit that are found together next to the item name (e.g., '1 Kg' for 'बासमती चावल').** If there is a separate column of numbers (like the column 2, 3, 1, 4, 2, 1 in a provided image), interpret those numbers as the 'num_packets'. If a quantity, unit, num_packets, cost_price_per_unit, or selling_price_per_unit is not explicitly mentioned or found for an item, assume a quantity of 1, a unit of \"pcs\", num_packets of 1, and `cost_price_per_unit`/`selling_price_per_unit` as null. If an item is unclear, **do not include it** in the output. Provide the output strictly as a JSON object with a top-level key 'detected_language' (string), 'bill_type' (string: \"purchase\", \"sale\", or \"unknown\") and another top-level key 'items' which is an array of objects. Each object in the 'items' array should have: 'item_name' (string), 'quantity' (numeric, e.g., 2.0 or 0.5), 'unit' (string), 'num_packets' (integer), 'cost_price_per_unit' (numeric or null), and 'selling_price_per_unit' (numeric or null). Example: {bill_example_json}"},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]
                },
            ],
            max_tokens=1000
        )
        content = response.choices[0].message.content
        # The content might be wrapped in markdown code block, so we need to extract the JSON string
        if content.startswith("```json") and content.endswith("```"):
            json_string = content[7:-3].strip()
        else:
            json_string = content.strip()
        
        extracted_data = json.loads(json_string)
        return extracted_data
    except json.JSONDecodeError as e:
        print(f"JSON Decode Error: {e}")
        print(f"Raw API response content: {content}") # Print raw content for debugging
        return {"bill_type": "unknown", "items": [], "detected_language": "en"}
    except Exception as e:
        print(f"Error extracting items from bill image: {e}")
        return {"bill_type": "unknown", "items": [], "detected_language": "en"}


if __name__ == "__main__":
    # Example usage (you would replace this with actual audio input)
    # For testing, you can create a dummy audio file or use a pre-recorded one.
    # transcribe_audio currently requires a file path, so we'll simulate the text input.

    print("--- Simulating Data Extraction ---")
    today = date.today()

    # Example 1: Clear expense
    text1 = "I bought coffee for 5 dollars today."
    extracted_data1 = extract_structured_data(text1, today)
    print(f"Text: '{text1}'")
    print(f"Extracted Data: {extracted_data1}")

    # Example 2: Sale with specific date
    text2 = "On October 26th, I sold a book for 25 euros."
    extracted_data2 = extract_structured_data(text2, today)
    print(f"Text: '{text2}'")
    print(f"Extracted Data: {extracted_data2}")

    # Example 3: Ambiguous type, no date
    text3 = "Paid 15 for lunch."
    extracted_data3 = extract_structured_data(text3, today)
    print(f"Text: '{text3}'")
    print(f"Extracted Data: {extracted_data3}")

    # Example 4: Regional language (simulated, as transcription would handle this)
    # In a real scenario, transcribe_audio would process the actual audio.
    # For demonstration, we'll assume the transcription result is in English.
    simulated_regional_text = "आज मैंने 100 रुपये का दूँध खरीदा।" # Hindi for "Today I bought milk for 100 rupees."
    # If Whisper correctly transcribes and translates this, it might be "Today I bought milk for 100 rupees."
    # Then, the extraction would work on the English text.
    print(f"\n--- Simulating Regional Language Transcription and Extraction ---")
    print(f"Simulated Regional Text (input to Whisper): '{simulated_regional_text}'")
    simulated_transcribed_text = "Today I bought milk for 100 rupees."
    extracted_data4 = extract_structured_data(simulated_transcribed_text, today)
    print(f"Simulated Transcribed Text (output from Whisper): '{simulated_transcribed_text}'")
    print(f"Extracted Data: {extracted_data4}")
