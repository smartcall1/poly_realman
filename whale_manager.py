import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta

# API 엔드포인트 세팅
DATA_API_BASE = "https://data-api.polymarket.com"
GAMMA_API_BASE = "https://gamma-api.polymarket.com"
DB_FILE = "whales.json"

# 백테스팅 설정값 (최정예 1타 강사 단타 고래 30명 스피드 감시 기준)
SLIPPAGE_PCT = 0.03   # 3% 슬리피지 가정
MIN_WIN_RATE = 75.0   # 최소 75% 이상의 승률 요구 (상향)
MIN_ROI = 10.0        # 최소 10% 이상의 '슬리피지 후' 가상 ROI 요구 (대폭 상향)
MIN_TRADES = 20       # 최소 20건 이상의 거래 내역이 있어야 함 (상향)
MAX_ACTIVE_WHALES = 30 # 최대 유지 고래 수 (모바일 모니터링 최적화)

def load_whales_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_whales_db(db):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)

def fetch_market_current_value(slug, conditionId, outcomeIndex, session):
    url = f"{GAMMA_API_BASE}/events?slug={slug}"
    try:
        r = session.get(url, timeout=10)
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

def evaluate_whale_edge(address, session, limit=50):
    """
    해당 주소의 최근 거래를 바탕으로 1분 뒤 매수(슬리피지 적용) 가상 PnL 산출
    """
    url = f"{DATA_API_BASE}/activity?user={address}&limit={limit}"
    try:
        r = session.get(url, timeout=10)
        if r.status_code != 200:
            return None
            
        activities = r.json()
        buys = [a for a in activities if a.get('type') == 'TRADE' and a.get('side') == 'BUY']
        
        if len(buys) < MIN_TRADES:
            return None # 데이터 불충분
            
        total_invested = 0.0
        total_current_value = 0.0
        wins = 0
        losses = 0
        
        for t in buys:
            whale_price = float(t.get('price', 0))
            size = float(t.get('size', 1))
            outcome_idx = int(t.get('outcomeIndex', 0))
            
            our_price = min(0.99, whale_price * (1 + SLIPPAGE_PCT))
            investment = size * our_price
            
            current_price = fetch_market_current_value(t.get('slug'), t.get('conditionId'), outcome_idx, session)
            time.sleep(0.2) # API 밴 방지
            
            if current_price is None:
                continue
                
            current_value = size * current_price
            total_invested += investment
            total_current_value += current_value
            
            if current_price >= 0.99: wins += 1
            elif current_price <= 0.01: losses += 1
            
        if total_invested == 0:
            return None
            
        roi = ((total_current_value - total_invested) / total_invested) * 100
        win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0
        
        return {
            "roi": roi,
            "win_rate": win_rate,
            "trades_analyzed": len(buys)
        }
    except Exception as e:
        print(f"Error evaluating {address}: {e}")
        return None

def fetch_top_leaderboard(session, limit=500):
    """
    Polymarket Leaderboard API (최대 50건 반환 한계 극복)
    limit으로 요청한 수량만큼 offset을 조절하며 페이지네이션(Pagination) 수집
    """
    whales = []
    
    # API Max Limit is 50 per request
    batch_size = 50
    offsets = range(0, limit, batch_size)
    
    for offset in offsets:
        url = f"{DATA_API_BASE}/v1/leaderboard?limit={batch_size}&offset={offset}&timePeriod=MONTH&orderBy=PNL"
        try:
            r = session.get(url, timeout=10)
            data = r.json()
            items = data if isinstance(data, list) else data.get('data', [])
            if not items and isinstance(data, dict):
                items = data.get('results', []) or data.get('leaderboard', [])
            
            if not items:
                break # 데이터가 더 없으면 중단
                
            for item in items:
                addr = item.get('proxyWallet') or item.get('address')
                name = item.get('userName', 'Unknown')
                if addr:
                    whales.append({"address": addr, "name": name})
                    
            time.sleep(1) # IP 밴 제한 회피용
        except Exception as e:
            print(f"Error fetching leaderboard at offset {offset}: {e}")
            break
            
    return whales[:limit]

def check_whale_recency(address, session, hours=48):
    """고래의 최근 활동 여부를 확인합니다. hours 내에 거래가 있으면 True."""
    url = f"{DATA_API_BASE}/activity?user={address}&limit=5"
    try:
        r = session.get(url, timeout=10)
        if r.status_code != 200:
            return False
        activities = r.json()
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        
        for tx in activities:
            if tx.get('type') != 'TRADE':
                continue
            ts = tx.get('timestamp')
            if not ts:
                continue
            if isinstance(ts, str):
                try:
                    tx_time = datetime.strptime(ts.split('.')[0].replace('Z',''), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                except:
                    continue
            else:
                tx_time = datetime.fromtimestamp(int(ts), timezone.utc)
            
            if tx_time > cutoff:
                return True
        return False
    except:
        return False

def run_manager():
    print(f"[{datetime.now()}] Starting Whale Manager...")
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    
    db = load_whales_db()
    
    # 0. Recency Check: 48시간 내 활동 없는 고래 자동 퇴출
    print("\n--- 0. Recency Check (48h Activity Filter) ---")
    inactive_count = 0
    for addr, info in list(db.items()):
        if info.get('status') == 'active':
            has_recent = check_whale_recency(addr, session, hours=48)
            if not has_recent:
                print(f"  💤 {info['name']} - 48시간 내 활동 없음 → inactive 전환")
                info['status'] = 'inactive'
                inactive_count += 1
            else:
                print(f"  ✅ {info['name']} - 최근 활동 확인됨")
            time.sleep(0.3)
    print(f"  → {inactive_count}명 비활동 고래 퇴출 완료")
    
    # 1. Pruning: 기존 DB의 고래들 성적 재평가
    print("\n--- 1. Pruning Existing Whales ---")
    keys_to_remove = []
    
    for addr, info in list(db.items()):
        if info.get('status') == 'active':
            print(f"Re-evaluating {info['name']} ({addr})...")
            result = evaluate_whale_edge(addr, session, limit=30) # 재평가는 최근 30개만
            
            if result is None:
                print(f"  -> Insufficient data or error. Marking inactive.")
                info['status'] = 'inactive'
                continue
                
            roi = result['roi']
            win_rate = result['win_rate']
            
            print(f"  -> ROI: {roi:+.2f}%, Win Rate: {win_rate:.1f}%")
            
            if roi < MIN_ROI or win_rate < MIN_WIN_RATE:
                print("  -> Underperforming. Marking as inactive.")
                info['status'] = 'inactive'
            else:
                print("  -> Passed. Keeping active.")
                info['last_updated'] = int(time.time())
                info['roi'] = roi
                info['win_rate'] = win_rate
                
    # 2. Discovery: 리더보드에서 새로운 고래 발굴
    print("\n--- 2. Discovering New Whales (Top 500 Pagination) ---")
    candidates = fetch_top_leaderboard(session, limit=500)
    print(f"✅ Fetched {len(candidates)} candidates from Leaderboard.")
    
    new_found = 0
    for cand in candidates:
        addr = cand['address']
        name = cand['name']
        
        # 이미 액티브 상태면 건너뜀 (pruning에서 평가 받았으므로)
        if addr in db and db[addr].get('status') == 'active':
            continue
            
        print(f"Evaluating candidate: {name} ({addr})...")
        result = evaluate_whale_edge(addr, session, limit=50) # 신규는 50개 빡세게 검증
        
        if result:
            roi = result['roi']
            win_rate = result['win_rate']
            print(f"  -> ROI: {roi:+.2f}%, Win Rate: {win_rate:.1f}%")
            
            if roi >= MIN_ROI and win_rate >= MIN_WIN_RATE:
                print("  New Whale Edge Verified! Adding to DB.")
                db[addr] = {
                    "name": name,
                    "win_rate": win_rate,
                    "roi": roi,
                    "added_at": int(time.time()),
                    "last_updated": int(time.time()),
                    "status": "active"
                }
                new_found += 1
            else:
                print("  -> Failed edge criteria.")
        else:
            print("  -> Insufficient data or error.")
            
        time.sleep(1) # Rate limit
        
    # 3. Trim to Top N Whales (최정예만 남김)
    print(f"\n--- 3. Trimming to Top {MAX_ACTIVE_WHALES} Whales ---")
    active_only = [(addr, info) for addr, info in db.items() if info.get('status') == 'active']
    if len(active_only) > MAX_ACTIVE_WHALES:
        # ROI 기준으로 정렬 (최상위 N명만 유지)
        sorted_whales = sorted(active_only, key=lambda x: x[1].get('roi', 0), reverse=True)
        # 컷오프 당한 고래는 inactive 강등
        for addr, info in sorted_whales[MAX_ACTIVE_WHALES:]:
            print(f"Demoting {info['name']} (ROI {info.get('roi', 0):.1f}% - Not in Top {MAX_ACTIVE_WHALES})")
            db[addr]['status'] = 'inactive'
            
    save_whales_db(db)
    
    active_count = sum(1 for v in db.values() if v.get('status') == 'active')
    print(f"\n[{datetime.now()}] Manager Finished.")
    print(f"Current Active Whales: {active_count}")
    print(f"Newly Added: {new_found}")

if __name__ == "__main__":
    run_manager()
