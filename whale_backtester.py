import requests
import json
import time

def fetch_market_current_value(slug, conditionId, outcomeIndex):
    """
    Gamma API에서 slug를 통해 마켓을 찾고, 해당 outcome의 현재 가치(0~1)를 반환.
    정산(Resolved)된 경우 승리했으면 1.0, 패배했으면 0.0이 됨.
    """
    url = f"https://gamma-api.polymarket.com/events?slug={slug}"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            events = r.json()
            if not events: return None
            
            for m in events[0].get('markets', []):
                if m.get('conditionId') == conditionId:
                    prices = m.get('outcomePrices', [])
                    if isinstance(prices, str):
                        try: prices = json.loads(prices)
                        except: pass
                        
                    if isinstance(prices, list) and len(prices) > outcomeIndex:
                        return float(prices[outcomeIndex])
    except:
        pass
    return None

def calculate_slippage_pnl(transactions, slippage_pct=0.05):
    """
    고래의 BUY 트랜잭션을 기반으로, 우리가 +X% 슬리피지 가격으로 샀을 때
    현재 가치(또는 정산 결과) 대비 PnL을 계산.
    """
    buys = [t for t in transactions if t.get('side') == 'BUY']
    if not buys:
        print("No BUY transactions found for backtesting.")
        return

    print(f"\n--- 🐋 Whale Copy-Trading Backtest ---")
    print(f"Total BUY trades analyzed: {len(buys)}")
    print(f"Assumed Slippage: {slippage_pct*100}%\n")
    
    total_invested = 0.0
    total_current_value = 0.0
    wins = 0
    losses = 0
    open_pos = 0

    for idx, t in enumerate(buys):
        whale_price = float(t.get('price', 0))
        size = float(t.get('size', 1))
        outcome_idx = int(t.get('outcomeIndex', 0))
        slug = t.get('slug')
        cond_id = t.get('conditionId')
        title = t.get('title', 'Unknown Market')
        
        # 1. 우리의 진입 가격 (슬리피지 적용: 고래가 산 가격보다 더 비싸게 산다고 가정)
        # 단, 가격은 최고 0.99로 제한
        our_price = min(0.99, whale_price * (1 + slippage_pct))
        investment = size * our_price
        
        # 2. 형제 가치 조회
        current_price = fetch_market_current_value(slug, cond_id, outcome_idx)
        time.sleep(0.5) # API 밴 방지
        
        if current_price is None:
            # Cannot find market, skip
            continue
            
        current_value = size * current_price
        
        total_invested += investment
        total_current_value += current_value
        
        pnl_pct = ((current_price - our_price) / our_price) * 100
        
        # Win/Loss 판별 (대략적으로 0.99 이상이면 승, 0.01 이하 패)
        status = "OPEN"
        if current_price >= 0.99:
            wins += 1
            status = "WIN"
        elif current_price <= 0.01:
            losses += 1
            status = "LOSS"
        else:
            open_pos += 1
            
        print(f"[{idx+1}] {title[:30]}... ({t.get('outcome')})")
        print(f"    Whale Entry: ${whale_price:.4f} | Our Entry: ${our_price:.4f}")
        print(f"    Current Val: ${current_price:.4f} | PnL: {pnl_pct:+.2f}% | Status: {status}")

    print("\n==================================================")
    print(" 📊 BACKTEST RESULT (Buy & Hold to Current)")
    print("==================================================")
    print(f"Total Invested (Our Cost) : ${total_invested:.2f}")
    print(f"Total Current Value       : ${total_current_value:.2f}")
    
    if total_invested > 0:
        net_profit = total_current_value - total_invested
        roi = (net_profit / total_invested) * 100
        print(f"Net Profit                : ${net_profit:+.2f} ({roi:+.2f}%)")
    
    print(f"Win/Loss/Open             : {wins} W / {losses} L / {open_pos} O")
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
    print(f"Win Rate (Closed Only)    : {win_rate:.1f}%")

def fetch_whale_trades(address, limit=50):
    headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
    url = f"https://data-api.polymarket.com/activity?user={address}&limit={limit}"
    
    try:
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            activities = r.json()
            trades = [a for a in activities if a.get('type') == 'TRADE']
            return trades
        return []
    except Exception as e:
        print(f"Request failed: {e}")
        return []

if __name__ == "__main__":
    # Test with my bot's proxy address first
    TEST_ADDRESS = "0xF709A25988A921b01b6aE5a9349aAf727247c75c"
    
    trades = fetch_whale_trades(TEST_ADDRESS, limit=30)
    calculate_slippage_pnl(trades, slippage_pct=0.03) # 3% slippage assumptions
