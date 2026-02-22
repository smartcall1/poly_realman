import json
with open("trade_history.jsonl") as f:
    for line in f.readlines()[-30:]:
        d = json.loads(line)
        if "Spread_Fisher" in d["strategy"]:
            print(d["action"], d["coin"], d["side"], d["pnl"], d.get("price"))

