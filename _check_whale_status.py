import json

data = json.load(open('whales.json', 'r', encoding='utf-16'))
total = len(data)
statuses = {}
for v in data.values():
    s = v.get('status', 'unknown')
    statuses[s] = statuses.get(s, 0) + 1

active = {k: v for k, v in data.items() if v.get('status') == 'active'}

print(f"Total whales in DB: {total}")
print(f"Status breakdown: {statuses}")
print(f"\nActive whales: {len(active)}")
print("--- Active whale details ---")
for k, v in active.items():
    m = v.get('metrics', {})
    print(f"  {v.get('name','?')}: score={v.get('score',0)}, wr={m.get('win_rate',0)}%, roi={m.get('roi',0)}%, trades={m.get('total_trades',0)}")
