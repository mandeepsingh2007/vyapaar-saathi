import requests
import os
from datetime import datetime, timedelta
import asyncio
# import holidays # Removed holidays import
from openai import OpenAI # Import OpenAI client
import json # Import json module
import re # Import re module
from dotenv import load_dotenv # Import load_dotenv

WEATHER_API_URL = "https://api.open-meteo.com/v1/forecast"

load_dotenv() # Load environment variables

client = OpenAI() # Initialize OpenAI client for this module

async def get_weather_forecast(latitude: float, longitude: float) -> dict:
    """Fetches a 14-day weather forecast from Open-Meteo."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        # CORRECTED: Simplified the request to ensure API compatibility.
        "daily": ["temperature_2m_max", "temperature_2m_min"],
        "timezone": "Asia/Kolkata",
        "forecast_days": 14
    }
    try:
        print("DEBUG_WEATHER_API: Attempting to fetch weather data...")
        response = await asyncio.to_thread(requests.get, WEATHER_API_URL, params=params)
        response.raise_for_status()  # This will raise an error if the status is 4xx or 5xx
        weather_data = response.json()
        
        # ADDED: This will print the raw weather data to your terminal
        print(f"DEBUG_WEATHER_API: Successfully fetched weather data: {weather_data}")
        
        return weather_data
        
    except requests.exceptions.RequestException as e:
        print(f"ERROR_WEATHER_API: Failed to fetch weather data: {e}")
        # Also print the response text if available, for more detailed errors
        if e.response is not None:
            print(f"ERROR_WEATHER_API: Response body: {e.response.text}")
        return {}

async def get_weather_based_recommendations(forecast_data: dict) -> list[dict]:
    """Uses an LLM to generate sales recommendations based on a 30-day weather forecast."""
    if not forecast_data or "daily" not in forecast_data:
        return []

    daily = forecast_data["daily"]
    summary = (
        f"Weather forecast for the next 30 days:\n"
        f"- Dates: from {daily['time'][0]} to {daily['time'][-1]}\n"
        f"- Max temperatures will range from {min(daily['temperature_2m_max'])}°C to {max(daily['temperature_2m_max'])}°C.\n"
        f"- There will be days with significant precipitation (rain).\n"
        "Analyze the trends (e.g., heatwaves, rainy periods, temperature drops)."
    )

    prompt = f"""
    You are a retail expert advising a small grocery store (kirana store) in India.
    Based on the following 30-day weather summary, suggest 5-7 specific products the shopkeeper should stock up on.

    Weather Summary:
    {summary}

    For each product, provide the reason for the suggestion and estimate the sales potential increase as 'Low', 'Medium', or 'High'.
    Respond ONLY with a valid JSON array of objects. Each object must have three keys: "item", "reason", and "potential".
    Example format:
    [
      {{
        "item": "Cold Drinks & Ice Cream",
        "reason": "Expected heatwave in the second week will increase demand for cooling products.",
        "potential": "High"
      }},
      {{
        "item": "Instant Noodles & Soup Packets",
        "reason": "Rainy days forecasted for the last week will lead to more people staying in and wanting comfort food.",
        "potential": "Medium"
      }}
    ]
    """

    try:
        print("DEBUG_LLM_WEATHER: Requesting weather-based recommendations from LLM.")
        response = await asyncio.to_thread(
            client.chat.completions.create,
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful retail expert for Indian grocery stores."},
                {"role": "user", "content": prompt}
            ],
            # Note: response_format is helpful but not a guarantee, so robust parsing is still good.
            # For this model, we'll parse the text content.
            temperature=0.6,
        )
        
        response_content = response.choices[0].message.content
        recommendations = json.loads(response_content)
        
        # CORRECTED: The AI returns a list directly, so we just return it.
        return recommendations

    except Exception as e:
        print(f"ERROR_LLM_WEATHER: Failed to get weather recommendations: {e}")
        return []

def get_festivals_from_llm(days_in_advance: int = 60) -> list[dict]:
    """
    Returns a filtered list of major Indian festivals for the remainder of 2025.
    This function now uses a static, reliable list instead of an LLM call.
    """
    print("DEBUG_FESTIVALS: Using reliable static festival list.")
    
    all_2025_festivals = [
        {"name": "Navratri", "date": "2025-09-22"},
        {"name": "Durga Puja", "date": "2025-09-28"},
        {"name": "Dussehra", "date": "2025-10-02"},
        {"name": "Karwa Chauth", "date": "2025-10-10"},
        {"name": "Dhanteras", "date": "2025-10-18"},
        {"name": "Diwali", "date": "2025-10-20"},
        {"name": "Govardhan Puja", "date": "2025-10-21"},
        {"name": "Bhai Dooj", "date": "2025-10-22"},
        {"name": "Chhath Puja", "date": "2025-10-27"},
        {"name": "Guru Nanak Jayanti", "date": "2025-11-05"},
    ]

    from datetime import date, timedelta

    upcoming_festivals = []
    today = date.today()
    end_date = today + timedelta(days=days_in_advance)

    for festival in all_2025_festivals:
        festival_date = date.fromisoformat(festival["date"])
        if today <= festival_date <= end_date:
            upcoming_festivals.append(festival)
            
    print(f"DEBUG_FESTIVALS: Found upcoming festivals: {upcoming_festivals}")
    return upcoming_festivals

GOOGLE_PLACES_API_URL_NEW = "https://places.googleapis.com/v1/places:searchNearby"

async def get_local_venues(latitude: float, longitude: float, radius: int = 1000) -> list:
    """Fetches potential event venues using the Google Places API (New) within a 1km radius."""
    
    # --- Paste your API Key directly here ---
    google_api_key = "AIzaSyDAECx2vafWoEVtCFC3MBbGCDZlBs_beLM"

    if not google_api_key or "YOUR_GOOGLE_API_KEY_HERE" in google_api_key:
        print("ERROR: Please replace 'YOUR_GOOGLE_API_KEY_HERE' with your actual Google API key.")
        return []

    url = "https://places.googleapis.com/v1/places:searchNearby"
    
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": google_api_key,
        "X-Goog-FieldMask": "places.displayName,places.types,places.primaryTypeDisplayName"
    }

    # Combined places of worship into one type for efficiency
    google_place_types = ["community_center", "stadium", "wedding_venue", "park", "place_of_worship"]
    unique_venues = {}

    for place_type in google_place_types:
        data = {
            "includedTypes": [place_type],
            "maxResultCount": 3, # Limit results per type
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": latitude, "longitude": longitude},
                    "radius": float(radius)
                }
            }
        }
        
        try:
            response = await asyncio.to_thread(requests.post, url, headers=headers, json=data)
            response.raise_for_status()
            
            response_data = response.json()
            print(f"DEBUG_GOOGLE_PLACES: Raw API response for {place_type}: {response_data}")

            for place in response_data.get("places", []):
                name = place.get("displayName", {}).get("text")
                primary_type = place.get("primaryTypeDisplayName", {}).get("text", place_type)
                if name and name not in unique_venues:
                    unique_venues[name] = {"name": name, "type": primary_type}

        except requests.exceptions.HTTPError as e:
            print(f"ERROR_GOOGLE_PLACES: HTTP error for type {place_type}: {e.response.status_code} - {e.response.text}")
        except Exception as e:
            print(f"ERROR_GOOGLE_PLACES: An unexpected error occurred for type {place_type}: {e}")

    print(f"DEBUG_LOCAL_VENUES: Found venues: {list(unique_venues.values())}")
    return list(unique_venues.values())


if __name__ == "__main__":
    async def run_all_tests():
        print("\n=== Testing get_festivals_from_llm ===")
        festivals = await get_festivals_from_llm()
        if festivals:
            print("Festivals found:")
            for f in festivals:
                print(f"  - {f.get('name', 'N/A')} on {f.get('date', 'N/A')}")
        else:
            print("No festivals found.")

        print("\n=== Testing get_local_venues ===")
        latitude = 28.64548759778835   # 👈 Your latitude
        longitude = 77.10445555765777  # 👈 Your longitude
        venues = await get_local_venues(latitude, longitude, radius=1000)  # 1 km radius
        if venues:
            print("Nearby venues found within 1 km:")
            for v in venues:
                print(f"  - {v['name']} ({v['type']})")
        else:
            print("No venues found nearby.")

    asyncio.run(run_all_tests())
