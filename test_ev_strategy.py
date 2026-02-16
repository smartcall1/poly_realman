"""Quick test for critical functions"""
from probability_engine import calculate_binary_probability, calculate_edge, _norm_cdf
from kelly_sizing import kelly_bet_size, kelly_info
from ev_strategy import EVStrategy

errors = []

# === Probability Engine ===
print("=== Probability Engine ===")

# Norm CDF
assert abs(_norm_cdf(0) - 0.5) < 1e-10
print("  OK: norm_cdf(0) = 0.5")

# ATM
p = calculate_binary_probability(100, 100, 0.8, 300, 0.0, 0.0, 1.0)
assert 0.40 < p < 0.60, f"ATM fail: {p}"
print(f"  OK: ATM = {p:.4f}")

# Deep ITM
p2 = calculate_binary_probability(110, 100, 0.3, 300, 0.0, 0.0, 1.0)
assert p2 > 0.90, f"ITM fail: {p2}"
print(f"  OK: ITM = {p2:.4f}")

# Deep OTM
p3 = calculate_binary_probability(90, 100, 0.3, 300, 0.0, 0.0, 1.0)
assert p3 < 0.10, f"OTM fail: {p3}"
print(f"  OK: OTM = {p3:.4f}")

# Expired
assert calculate_binary_probability(105, 100, 0.5, 0) == 1.0
assert calculate_binary_probability(95, 100, 0.5, 0) == 0.0
print("  OK: Expired cases")

# Edge
e1 = calculate_edge(0.70, 0.55, 0.02)
assert e1 > 0, f"+EV edge fail: {e1}"
e2 = calculate_edge(0.40, 0.55, 0.02)
assert e2 < 0, f"-EV edge fail: {e2}"
print(f"  OK: +EV edge={e1:.4f}, -EV edge={e2:.4f}")

# === Kelly Sizing ===
print("\n=== Kelly Sizing ===")

b1 = kelly_bet_size(100, 0.70, 0.55, 0.02, 0.25, 0.10, 1.0)
assert b1 > 0 and b1 <= 10, f"Kelly fail: {b1}"
print(f"  OK: +EV bet = ${b1}")

b2 = kelly_bet_size(100, 0.40, 0.55, 0.02, 0.25, 0.10, 1.0)
assert b2 == 0, f"-EV should be 0: {b2}"
print(f"  OK: -EV bet = ${b2}")

assert kelly_bet_size(0, 0.7, 0.5) == 0
assert kelly_bet_size(100, 0.5, 0.0) == 0
assert kelly_bet_size(100, 0.5, 1.0) == 0
print("  OK: Edge cases")

ki = kelly_info(0.65, 0.50, 0.02)
assert ki['full_kelly'] > 0
print(f"  OK: Kelly info = {ki}")

# === Market Parsing ===
print("\n=== Market Parsing ===")
s = EVStrategy(None)

assert s.extract_coin("Will BTC be above $97,500?") == "BTC"
assert s.extract_coin("Will ETH go above $2,650?") == "ETH"
assert s.extract_coin("Will SOL be above $195?") == "SOL"
assert s.extract_coin("Will XRP be above $0.65?") == "XRP"
print("  OK: Coin extraction")

# Strike extraction
s1 = s.extract_strike_price("Will BTC be above $97,500 at 12:05?")
assert s1 == 97500.0, f"BTC strike fail: {s1}"
s2 = s.extract_strike_price("Will ETH go above $2,650.50 by 12:15?")
assert s2 == 2650.50, f"ETH strike fail: {s2}"
s3 = s.extract_strike_price("Will SOL be above $195 at 12:20?")
assert s3 == 195.0, f"SOL strike fail: {s3}"
s4 = s.extract_strike_price("Will XRP be above $0.65 at 12:30?")
assert s4 == 0.65, f"XRP strike fail: {s4}"
print(f"  OK: Strikes = [{s1}, {s2}, {s3}, {s4}]")

assert s.is_above_market("Will BTC be above $97,500?") == True
assert s.is_above_market("Will BTC be below $97,500?") == False
print("  OK: Above/Below detection")

print("\n" + "="*50)
print("  ALL TESTS PASSED!")
print("="*50)
