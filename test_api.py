import requests
import json

def fetch_global_activity():
    headers = {"User-Agent": "Mozilla/5.0"}
    
    print("1. Fetching Global Activity...")
    act_url = "https://data-api.polymarket.com/activity?limit=10"
    r_act = requests.get(act_url, headers=headers, timeout=10)
    
    if r_act.status_code == 200:
        act_data = r_act.json()
        print(f"Success! Found {len(act_data)} global activities.")
        
        # Find a TRADE activity to get a user address
        for act in act_data:
            if act.get('type') == 'TRADE':
                user = act.get('address') or act.get('user')
                print(f"\n2. Found a Trader Address: {user}")
                print("\nSample Activity:")
                print(json.dumps(act, indent=2))
                return user
                
        print("No TRADE activity found in the global list.")
    else:
        print(f"Global Activity API failed: {r_act.status_code} - {r_act.text}")
        
    return None

if __name__ == '__main__':
    fetch_global_activity()
