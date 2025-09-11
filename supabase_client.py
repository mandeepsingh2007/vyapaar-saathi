import os
from dotenv import load_dotenv
from supabase import create_client, Client
from datetime import datetime, timezone, date
import asyncio # Import asyncio

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("Supabase URL and Key must be set in the .env file")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

__all__ = ['save_transaction', 'get_user_transactions_summary', 'update_stock_item', 'get_stock_levels', 'get_daily_sales_summary', 'get_low_stock_items', 'save_order_confirmation']

# --- Unit Conversion Helpers (Centralized in Supabase Client) ---
def _convert_to_base_unit(value: float, unit: str) -> float:
    """Converts a quantity to a common base unit (grams for weight, milliliters for volume) for internal consistency."""
    unit_lower = unit.lower()
    if unit_lower == 'kg':
        return value * 1000.0
    elif unit_lower == 'g':
        return value
    # Add more unit conversions as needed (e.g., litres to ml)
    return value # For pcs, packets, etc., no conversion, treat as base

def _convert_from_base_unit(value_in_base: float, target_unit: str) -> float:
    """Converts a quantity from the base unit back to the target unit."""
    target_unit_lower = target_unit.lower()
    if target_unit_lower == 'kg':
        return value_in_base / 1000.0
    elif target_unit_lower == 'g':
        return value_in_base
    # Add more unit conversions as needed
    return value_in_base # For pcs, packets, etc., no conversion

# --- End Unit Conversion Helpers ---

async def save_transaction(transaction_data: dict, user_id: str) -> dict:
    """
    Saves the extracted transaction data to the Supabase database for a specific user.
    """
    try:
        # Ensure the date is in 'YYYY-MM-DD' format if not already
        if 'date' in transaction_data and not isinstance(transaction_data['date'], str):
            transaction_data['date'] = transaction_data['date'].strftime('%Y-%m-%d')

        response = await asyncio.to_thread(supabase.table("transactions").insert({
            "transaction_date": transaction_data.get("date"),
            "transaction_type": transaction_data.get("type"),
            "amount": transaction_data.get("amount"),
            "item": transaction_data.get("item"),
            "user_id": user_id # New: Save user_id
        }).execute)
        print("Transaction saved successfully:", response.data)
        return response.data
    except Exception as e:
        print(f"Error saving transaction to Supabase: {e}")
        return {}

def get_total_balance(user_id: str) -> float:
    """
    Calculates the total balance from the 'transactions' table for a specific user.
    Sales are added, expenses are subtracted.
    """
    try:
        response = supabase.table("transactions").select("transaction_type, amount").eq("user_id", user_id).execute()
        
        total_balance = 0.0
        if response.data:
            for record in response.data:
                amount = float(record["amount"])
                if record["transaction_type"].lower() == "sale":
                    total_balance += amount
                elif record["transaction_type"].lower() == "expense":
                    total_balance -= amount
        
        print(f"Total balance calculated for user {user_id}: {total_balance}")
        return total_balance
    except Exception as e:
        print(f"Error fetching total balance from Supabase: {e}")
        return 0.0

def get_user_transactions_summary(user_id: str, limit: int = 5) -> tuple[float, list[dict]]:
    """
    Calculates the total balance and fetches a summary of recent transactions for a specific user.
    Sales are added, expenses are subtracted.
    Returns a tuple: (total_balance, recent_transactions_list)
    """
    try:
        response = supabase.table("transactions") \
                           .select("transaction_date, transaction_type, amount, item") \
                           .eq("user_id", user_id) \
                           .order("created_at", desc=True) \
                           .limit(limit) \
                           .execute()
        
        total_balance = 0.0
        recent_transactions = []

        # Fetch all transactions to calculate total balance
        all_transactions_response = supabase.table("transactions") \
                                        .select("transaction_type, amount") \
                                        .eq("user_id", user_id) \
                                        .execute()

        if all_transactions_response.data:
            for record in all_transactions_response.data:
                amount = float(record["amount"])
                if record["transaction_type"].lower() == "sale":
                    total_balance += amount
                elif record["transaction_type"].lower() == "expense":
                    total_balance -= amount

        # Process recent transactions for summary
        if response.data:
            for record in response.data:
                # Format date to YYYY-MM-DD for consistency
                record["transaction_date"] = record["transaction_date"].split('T')[0] # Assuming date comes with time
                recent_transactions.append(record)
        
        print(f"Summary for user {user_id}: Total balance {total_balance}, {len(recent_transactions)} recent transactions.")
        return total_balance, recent_transactions
    except Exception as e:
        print(f"Error fetching transaction summary from Supabase: {e}")
        return 0.0, []


# New functions for stock management

async def update_stock_item(user_id: str, item_name: str, quantity_delta: float, unit: str = "pcs", cost_price_per_unit: float | None = None) -> dict:
    """Updates or inserts a stock item for a user, handling fractional quantities and units.
    
    Args:
        user_id: The WhatsApp sender ID.
        item_name: The name of the item.
        quantity_delta: The amount to add or subtract from the stock quantity. Positive for purchase, negative for sale.
        unit: The unit of the quantity (e.g., kg, g, dozen, pcs). This is the unit of `quantity_delta`.
        cost_price_per_unit: The cost price per unit of the item (optional).

    Returns:
        The updated or newly created stock item record.
    """
    quantity_delta = float(quantity_delta)

    # Try to find an existing item with the same user_id, item_name, and unit
    # It's crucial to search by item_name and the *stored* unit to ensure consistency.
    print(f"Searching for existing item: user_id={user_id}, item_name={item_name}")
    response = await asyncio.to_thread(supabase.from_('stock_items').select('*').eq('user_id', user_id).eq('item_name', item_name).execute)
    print(f"Supabase search response: {response.data}")
    existing_item = None
    # Find the best matching item, prioritizing exact unit match
    for item in response.data:
        if item['unit'].lower() == unit.lower(): # Exact unit match
            existing_item = item
            break
    if existing_item is None and response.data: # Fallback to first item if unit doesn't match (should be rare with good data extraction)
        existing_item = response.data[0]
        print(f"DEBUG_STOCK: No exact unit match for '{unit}' for item '{item_name}'. Using existing item with unit '{existing_item['unit']}'.")

    if existing_item:
        # Convert existing quantity to base unit
        existing_quantity_in_base = _convert_to_base_unit(existing_item['quantity'], existing_item['unit'])
        # Convert incoming delta to base unit
        delt_in_base = _convert_to_base_unit(quantity_delta, unit)

        # Calculate new quantity in base unit
        new_quantity_in_base = existing_quantity_in_base + delt_in_base

        # Convert new quantity back to the *original stock item's unit* for storage
        new_quantity_for_storage = _convert_from_base_unit(max(0.0, new_quantity_in_base), existing_item['unit'])

        update_data = {
            'quantity': new_quantity_for_storage,
            'last_updated': datetime.now(timezone.utc).isoformat()
        }
        # Only update cost_price_per_unit if it's explicitly provided (for purchases)
        if cost_price_per_unit is not None:
            update_data['cost_price_per_unit'] = cost_price_per_unit
        
        print(f"Updating existing item {existing_item['id']} with {update_data}")
        response = await asyncio.to_thread(supabase.from_('stock_items').update(update_data).eq('id', existing_item['id']).execute)
        print(f"Supabase update response: {response.data}")
    else:
        # Insert new item. For sales, quantity_delta will be negative, so new item should start at 0 quantity.
        # This ensures we don't add negative stock if a sale of a non-existent item is processed.
        initial_quantity_for_storage = _convert_from_base_unit(max(0.0, _convert_to_base_unit(quantity_delta, unit)), unit) # Convert to base and back to ensure consistency
        if quantity_delta < 0: # If it was a sale and item not found, set quantity to 0.
            initial_quantity_for_storage = 0.0

        insert_data = {
            'user_id': user_id,
            'item_name': item_name,
            'quantity': initial_quantity_for_storage,
            'unit': unit,
            'last_updated': datetime.now(timezone.utc).isoformat()
        }
        if cost_price_per_unit is not None:
            insert_data['cost_price_per_unit'] = cost_price_per_unit
            
        print(f"Inserting new item: {insert_data}")
        response = await asyncio.to_thread(supabase.from_('stock_items').insert(insert_data).execute)
        print(f"Supabase insert response: {response.data}")
    
    if response.data:
        return response.data[0]
    else:
        print(f"Error: Failed to update/insert stock item. Error: {response.error}")
        raise Exception(f"Failed to update/insert stock item: {response.error}")

async def get_stock_levels(user_id: str) -> list[dict]:
    """Retrieves all stock items, their quantities, and units for a given user."""
    response = await asyncio.to_thread(supabase.from_("stock_items").select("item_name, quantity, unit, cost_price_per_unit, min_quantity_threshold").eq("user_id", user_id).order("item_name").execute)
    if response.data:
        return response.data
    else:
        print(f"Error: Failed to retrieve stock levels. Error: {response.error}")
        return []

async def get_daily_sales_summary(user_id: str, target_date: date) -> tuple[float, list[dict]]:
    """
    Retrieves the total sales amount and a list of sales transactions for a specific user and date.
    """
    try:
        # Convert target_date to string for Supabase query
        formatted_date = target_date.strftime('%Y-%m-%d')
        
        response = await asyncio.to_thread(supabase.table("transactions") \
                                        .select("item, amount") \
                                        .eq("user_id", user_id) \
                                        .eq("transaction_type", "sale") \
                                        .eq("transaction_date", formatted_date) \
                                        .order("created_at", desc=True) \
                                        .execute)
        
        total_sales = 0.0
        sales_transactions = []

        if response.data:
            for record in response.data:
                amount = float(record["amount"])
                total_sales += amount
                sales_transactions.append(record)

        print(f"Daily sales summary for user {user_id} on {formatted_date}: Total sales {total_sales}, {len(sales_transactions)} transactions.")
        return total_sales, sales_transactions
    except Exception as e:
        print(f"Error fetching daily sales summary from Supabase: {e}")
        return 0.0, []

async def get_low_stock_items(user_id: str) -> list[dict]:
    """Retrieves items for a user that are below their specified min_quantity_alert."""
    all_stock_items = await get_stock_levels(user_id)
    low_stock_items = []
    for item in all_stock_items:
        # Ensure both quantity and min_quantity_alert are numeric for comparison
        current_quantity = float(item.get('quantity', 0))
        min_alert = float(item.get('min_quantity_threshold', 0))
        if current_quantity <= min_alert:
            low_stock_items.append(item)
    print(f"DEBUG_STOCK: Found {len(low_stock_items)} low stock items for user {user_id}.")
    return low_stock_items

async def save_order_confirmation(
    user_id: str,
    item_name: str,
    quantity: float,
    unit: str,
    cost_price_per_unit: float,
    supplier_name: str,
) -> dict:
    """Saves an order confirmation to Supabase, updating stock and recording a purchase transaction."""
    try:
        # 1. Update stock item (increase quantity)
        updated_stock = await update_stock_item(user_id, item_name, quantity, unit, cost_price_per_unit)
        print(f"DEBUG_SUPABASE_ORDER: Stock updated for {item_name}: {updated_stock}")

        # 2. Record a purchase transaction
        transaction_data = {
            "date": datetime.now(timezone.utc).strftime('%Y-%m-%d'),
            "type": "purchase",
            "amount": quantity * cost_price_per_unit,
            "item": f"{item_name} ({quantity} {unit}) from {supplier_name}"
        }
        saved_transaction = await save_transaction(transaction_data, user_id)
        print(f"DEBUG_SUPABASE_ORDER: Purchase transaction saved: {saved_transaction}")

        return {"status": "success", "stock": updated_stock, "transaction": saved_transaction}
    except Exception as e:
        print(f"ERROR_SUPABASE_ORDER: Failed to save order confirmation: {e}")
        raise Exception(f"Failed to save order confirmation: {e}")

async def get_all_unique_user_ids_with_stock() -> list[str]:
    """Retrieves all unique user_ids that have stock items."""
    try:
        response = await asyncio.to_thread(supabase.from_("stock_items").select("user_id").execute)
        if response.data:
            # Extract unique user_ids from the response
            unique_user_ids = list(set([item['user_id'] for item in response.data]))
            print(f"DEBUG_SUPABASE: Retrieved unique user IDs with stock: {unique_user_ids}")
            return unique_user_ids
        else:
            print("DEBUG_SUPABASE: No user IDs found in stock_items table.")
            return []
    except Exception as e:
        print(f"ERROR_SUPABASE: Error retrieving unique user IDs with stock: {e}")
        return []

if __name__ == "__main__":
    print("--- Simulating Supabase Save and Balance for a User ---")
    # Use a dummy user ID for testing
    test_user_id = "whatsapp:+1234567890"

    # Example transaction data
    example_data1 = {
        "date": "2023-10-27",
        "type": "expense",
        "amount": 45.50,
        "item": "Dinner with friends"
    }
    asyncio.run(save_transaction(example_data1, test_user_id))

    example_data2 = {
        "date": "2023-10-26",
        "type": "sale",
        "amount": 120.00,
        "item": "Sold old laptop"
    }
    asyncio.run(save_transaction(example_data2, test_user_id))

    print("\n--- Testing Total Balance ---")
    balance = get_total_balance(test_user_id)
    print(f"Current account balance for {test_user_id}: ₹{balance:.2f}")

    print("\n--- Testing User Transactions Summary ---")
    total_bal, recent_txns = asyncio.run(get_user_transactions_summary(test_user_id))
    print(f"Total Balance: ₹{total_bal:.2f}")
    print("Recent Transactions:")
    for txn in recent_txns:
        print(f"  Date: {txn['transaction_date']}, Type: {txn['transaction_type']}, Amount: {txn['amount']}, Item: {txn['item']}")

    print("\n--- Testing Stock Update ---")
    # Add 10 kg of Rice
    asyncio.run(update_stock_item(test_user_id, "rice", 10, "kg", 50.00))
    # Sell 2 kg of Rice
    asyncio.run(update_stock_item(test_user_id, "rice", -2, "kg"))
    # Add 5 pcs of Pen with cost price
    asyncio.run(update_stock_item(test_user_id, "pen", 5, "pcs", 10.00))

    stock = asyncio.run(get_stock_levels(test_user_id))
    print("\n--- Current Stock Levels ---")
    for item in stock:
        print(f"  Item: {item['item_name']}, Qty: {item['quantity']} {item['unit']}, Cost: {item.get('cost_price_per_unit', 'N/A')}")

    print("\n--- Testing Daily Sales Summary ---")
    today = date.today()
    total_sales, sales_txns = asyncio.run(get_daily_sales_summary(test_user_id, today))
    print(f"Today's Total Sales: ₹{total_sales:.2f}")
    print("Today's Sales Transactions:")
    for txn in sales_txns:
        print(f"  Item: {txn['item']}, Amount: {txn['amount']}")

    print("\n--- Testing Low Stock Items ---")
    # To test low stock, you might need to manually set min_quantity_threshold for some items in your Supabase table
    low_stock = asyncio.run(get_low_stock_items(test_user_id))
    print("Low Stock Items:")
    for item in low_stock:
        print(f"  Item: {item['item_name']}, Qty: {item['quantity']} {item['unit']}, Min Threshold: {item['min_quantity_threshold']}")

    print("\n--- Testing Get All Unique User IDs With Stock ---")
    unique_users = asyncio.run(get_all_unique_user_ids_with_stock())
    print(f"Unique users with stock: {unique_users}")
