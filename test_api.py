import requests
import json

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json",
})

print("Testing API endpoints...")

try:
    print("1. Testing data-api...")
    r = session.get("https://data-api.polymarket.com/activity?user=0x3C278146F5FfB35C15A17a3f3b97b09cDB9E555b&limit=1", timeout=5)
    print("Data-api status:", r.status_code)
except Exception as e:
    print("Data-api error:", type(e).__name__)

try:
    print("2. Testing gamma-api...")
    r = session.get("https://gamma-api.polymarket.com/events?limit=1", timeout=5)
    print("Gamma-api status:", r.status_code)
except Exception as e:
    print("Gamma-api error:", type(e).__name__)

try:
    print("3. Testing clob API...")
    r = session.get("https://clob.polymarket.com/markets", timeout=5)
    print("Clob status:", r.status_code)
except Exception as e:
    print("Clob error:", type(e).__name__)
