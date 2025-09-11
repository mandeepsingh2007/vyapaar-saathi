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
    """Fetches weather forecast from Open-Meteo."""
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "current": "temperature_2m,weather_code",
        "timezone": "auto",
        "forecast_days": 1
    }
    try:
        # Run the synchronous requests.get call in a separate thread
        response = await asyncio.to_thread(requests.get, WEATHER_API_URL, params=params)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"ERROR_WEATHER_API: Failed to fetch weather data: {e}")
        return {}

# Removed web scraping function
# async def get_indian_festivals_from_web() -> list[dict]:
#     """Fetches Indian festivals from drikpanchang.com using web scraping."""
#     try:
#         # Construct the URL for the current month's festivals
#         current_date = datetime.now()
#         month_name = current_date.strftime("%B").lower()
#         year = current_date.year
#         url = f"https://www.drikpanchang.com/festivals/festivals-list.html?year={year}&month={month_name}"
        
#         print(f"DEBUG_WEB_SCRAPING: Fetching festivals from: {url}")
#         response = await asyncio.to_thread(requests.get, url)
#         response.raise_for_status()
        
#         soup = BeautifulSoup(response.content, 'html.parser')
        
#         festivals = []
#         # Find the main table containing festival data
#         festival_table = soup.find("table", class_="dpTable festivalTable")
        
#         if festival_table:
#             rows = festival_table.find_all("tr")
#             for row in rows[1:]:
#                 cols = row.find_all("td")
#                 if len(cols) >= 3:
#                     date_str = cols[0].get_text(strip=True)
#                     festival_name = cols[2].get_text(strip=True)
#                     
#                     # Basic parsing, might need refinement depending on website structure changes
#                     festivals.append({"name": festival_name, "date": date_str})
#         
#         print(f"DEBUG_WEB_SCRAPING: Scraped festivals: {festivals}")
#         return festivals
#     except requests.exceptions.RequestException as e:
#         print(f"ERROR_WEB_SCRAPING: Failed to fetch festivals from web: {e}")
#         return []
#     except Exception as e:
#         print(f"ERROR_WEB_SCRAPING: Error during web scraping: {e}")
#         return []

async def get_festivals_from_llm(days_in_advance: int = 60) -> list[dict]:
    """Uses OpenAI LLM to identify prominent upcoming Indian festivals."""
    try:
        current_date = datetime.now()
        current_date_str = current_date.strftime("%Y-%m-%d")
        prompt = f"""List major and well-known Indian festivals happening *strictly in the future* within the next {days_in_advance} days from {current_date_str}. 
Respond with a JSON array of objects, where each object has 'name' (string) and 'date' (string, YYYY-MM-DD format). 
If the exact year is not known, infer the current year ({current_date.year}). 
If a festival spans multiple days, provide the start date. 
If no festivals are found, return an empty array: []. 
Example: [{{"name": "Diwali", "date": "2025-10-20"}}, {{"name": "Holi", "date": "2026-03-15"}}]"""

        print(f"DEBUG_LLM_FESTIVALS: Prompt sent to LLM: {prompt}") # Added logging for the prompt

        response = await asyncio.to_thread(
            client.chat.completions.create,
            model="gpt-3.5-turbo-0125", # Changed model for potentially better structured output
            messages=[
                {"role": "system", "content": "You are a helpful assistant that provides information about Indian festivals."},
                {"role": "user", "content": prompt}
            ],
            temperature=1.0, # Increased temperature for more comprehensive response
            max_tokens=500 # Increased max_tokens for more comprehensive response
        )
        
        llm_response_content = response.choices[0].message.content.strip()
        print(f"DEBUG_LLM_FESTIVALS: Raw LLM response: {llm_response_content}")

        # Remove markdown code block if present
        if llm_response_content.startswith("```json") and llm_response_content.endswith("```"):
            llm_response_content = llm_response_content[len("```json\n"):-len("\n```")].strip()
            print(f"DEBUG_LLM_FESTIVALS: Stripped markdown response: {llm_response_content}")

        festivals = []
        try:
            parsed_response = json.loads(llm_response_content)
            if isinstance(parsed_response, list):
                # Filter out past festivals and ensure date format
                for festival in parsed_response:
                    if "name" in festival and "date" in festival:
                        try:
                            festival_date = datetime.strptime(festival["date"], "%Y-%m-%d").date()
                            if festival_date >= current_date.date(): # Only include future or current day festivals
                                festivals.append({"name": festival["name"], "date": festival["date"]})
                            else:
                                print(f"DEBUG_LLM_FESTIVALS: Filtering out past festival: {festival['name']} on {festival['date']}")
                        except ValueError:
                            print(f"WARNING_LLM_FESTIVALS: Could not parse date for {festival['name']}: {festival['date']}. Skipping date filtering for this entry.")
                            # If date parsing fails, include it but without filtering by date
                            festivals.append({"name": festival["name"], "date": festival["date"]})
            else:
                print(f"WARNING_LLM_FESTIVALS: LLM response was not a list: {llm_response_content}")
        except json.JSONDecodeError as e:
            print(f"ERROR_LLM_FESTIVALS: Failed to parse LLM JSON response: {e}. Raw response: {llm_response_content}")
            # Fallback to simple regex parsing if JSON fails (less reliable) - this should be a last resort
            festival_pattern = re.compile(r""""name":\s*"([^"]+)""", re.IGNORECASE)
            date_pattern = re.compile(r""""date":\s*"([^"]+)""", re.IGNORECASE)
            
            names = festival_pattern.findall(llm_response_content)
            dates = date_pattern.findall(llm_response_content)
            
            for i in range(min(len(names), len(dates))):
                try:
                    festival_date = datetime.strptime(dates[i], "%Y-%m-%d").date()
                    if festival_date >= current_date.date():
                        festivals.append({"name": names[i], "date": dates[i]}) # Ensure date is YYYY-MM-DD
                    else:
                        print(f"DEBUG_LLM_FESTIVALS: Filtering out past festival (regex): {names[i]} on {dates[i]}")
                except ValueError:
                    print(f"WARNING_LLM_FESTIVALS: Could not parse date for {names[i]}: {dates[i]} via regex. Skipping date filtering.")
                    festivals.append({"name": names[i], "date": dates[i]}) # Add without date filtering if parsing fails

        print(f"DEBUG_LLM_FESTIVALS: Parsed and filtered festivals: {festivals}")
        return festivals
    except Exception as e:
        print(f"ERROR_LLM_FESTIVALS: Failed to get festivals from LLM: {e}")
        return []

if __name__ == "__main__":
    async def test_get_festivals_from_llm():
        print("Running test for get_festivals_from_llm...")
        festivals = await get_festivals_from_llm()
        if festivals:
            print("Festivals found:")
            for f in festivals:
                print(f"  - {f.get('name', 'N/A')} on {f.get('date', 'N/A')}")
        else:
            print("No festivals found.")

    asyncio.run(test_get_festivals_from_llm())