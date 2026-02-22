import requests
import json
import time
from whale_backtester import fetch_whale_trades, calculate_slippage_pnl

def fetch_top_whales_from_leaderboard(limit=5):
    headers = {"User-Agent": "Mozilla/5.0"}
    url = f"https://data-api.polymarket.com/v1/leaderboard?limit={limit}&timePeriod=MONTH&orderBy=PNL"
    
    print(f"1. Fetching Top {limit} Whales from Leaderboard (MONTHLY PNL)...")
    
    try:
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        
        whales = []
        # JSON response might be a list directly or inside 'data' or similar
        items = data if isinstance(data, list) else data.get('data', [])
        
        if not items and isinstance(data, dict):
            # sometimes items are inside results or leaderboard
            items = data.get('results', []) or data.get('leaderboard', [])
            
        for item in items:
            addr = item.get('proxyWallet') or item.get('address')
            name = item.get('userName', 'Unknown')
            if addr:
                whales.append((addr, name))
                
        return whales
        
    except Exception as e:
        print(f"Error fetching leaderboard: {e}")
        try:
             print("Response format was:", data.keys() if 'data' in locals() else "No data")
        except:
             pass
        return []

if __name__ == '__main__':
    print("--- 🐋 WHALE LEADERBOARD BACKTESTING INITIALIZED ---")
    whales = fetch_top_whales_from_leaderboard(limit=5)
    
    if not whales:
        print("Failed to find whales from leaderboard.")
        exit(1)
        
    print(f"\nFound {len(whales)} Top Whales! Starting backtest...\n")
    
    for idx, (addr, name) in enumerate(whales):
        print("="*60)
        print(f" 🏆 WHALE #{idx+1}: {name} ({addr})")
        print("="*60)
        
        # Fetch their trades
        trades = fetch_whale_trades(addr, limit=50) # Increased to 50
        
        if trades:
            # Run simulation with standard 3% slippage assumption
            calculate_slippage_pnl(trades, slippage_pct=0.03)
        else:
            print("No TRADE activities found for this user.")
            
        time.sleep(2) # rate limit

