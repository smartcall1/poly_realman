import sys
import os
import json
import time
import requests
from datetime import datetime
from collections import defaultdict

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', line_buffering=True)
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', line_buffering=True)
from datetime import datetime
from collections import defaultdict

DB_FILE = "whales.json"
SLIPPAGE_PCT = 0.03
INITIAL_CAPITAL = 1000.0

class DeepBacktester:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})
        self.market_cache = {} # conditionId -> final price or current price
        
    def load_whales(self):
        if os.path.exists(DB_FILE):
            with open(DB_FILE, "r", encoding="utf-8") as f:
                db = json.load(f)
            return {k: v for k, v in db.items() if v.get('status') == 'active'}
        return {}

    def fetch_all_trades(self, address, limit=2000):
        """특정 고래의 과거 트랜잭션을 가져옵니다. (최대 limit 지정)"""
        url = f"https://data-api.polymarket.com/activity?user={address}&limit={limit}"
        try:
            r = self.session.get(url, timeout=15)
            if r.status_code == 200:
                activities = r.json()
                print(f"[{address}] API에서 {len(activities)}건의 활동 정보를 가져왔습니다.")
                
                # 구매(BUY) 내역만 필터링
                buys = []
                for a in activities:
                    if a.get('type') == 'TRADE' and a.get('side') == 'BUY':
                        buys.append(a)
                    elif a.get('action') == 'Buy': # Clob나 Activity V2 API 구조일 수 있음
                        buys.append(a)
                        
                print(f"[{address}] BUY 필터링 후 {len(buys)}건의 매수 내역이 남았습니다.")
                return buys
        except Exception as e:
            print(f"[{address}] 활동 내역 로드 에러: {e}")
        return []

    def get_market_resolution_price(self, slug, conditionId, outcomeIndex):
        """마켓의 최종 결과 (또는 현재 가격)을 캐싱하여 반환합니다."""
        cache_key = f"{conditionId}_{outcomeIndex}"
        if cache_key in self.market_cache:
            return self.market_cache[cache_key]
            
        url = f"https://gamma-api.polymarket.com/events?slug={slug}"
        try:
            r = self.session.get(url, timeout=5)
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
                            price = float(prices[outcomeIndex])
                            self.market_cache[cache_key] = price
                            return price
        except:
            pass
        return None

    def simulate(self):
        print("=== 📈 Deep Backtesting Engine ===")
        whales = self.load_whales()
        if not whales:
            print("활성화된 고래가 없습니다.")
            return

        all_trades = []
        
        # 1. 모든 고래의 과거 트랜잭션 수집
        for addr, info in whales.items():
            name = info.get('name', 'Unknown')
            score = info.get('score', 50)
            print(f"⬇️ 과거 거래 내역 수집 중... {name} ({addr})")
            
            trades = self.fetch_all_trades(addr)
            for t in trades:
                # 타임스탬프 파싱
                ts_val = t.get('timestamp')
                try:
                    if isinstance(ts_val, str):
                        ts = int(datetime.strptime(ts_val.split('.')[0], "%Y-%m-%dT%H:%M:%S").timestamp())
                    else:
                        ts = int(ts_val)
                    
                    t['parsed_time'] = ts
                    t['whale_name'] = name
                    t['whale_score'] = score
                    all_trades.append(t)
                except Exception as e:
                    continue
            time.sleep(1) # API Rate limit 보호

        if not all_trades:
            print("분석할 거래 내역이 없습니다.")
            return

        # 2. 시간순 정렬 (타임라인 구축)
        all_trades.sort(key=lambda x: x['parsed_time'])
        print(f"\n총 {len(all_trades)}개의 매수 트랜잭션을 시간순으로 시뮬레이션 합니다...")

        # 3. 자산 성장 곡선 시뮬레이션
        capital = INITIAL_CAPITAL
        timeline_log = []
        
        wins = 0
        losses = 0
        open_positions = 0
        
        for idx, t in enumerate(all_trades):
            whale_price = float(t.get('price', 0))
            if whale_price <= 0: continue
            
            # 진입가 (슬리피지 적용)
            our_price = min(0.99, whale_price * (1 + SLIPPAGE_PCT))
            
            score = t['whale_score']
            
            # 동적 배팅 사이즈 로직 (whale_copy_bot.py와 동일하게 5% 룰 적용)
            base_bet = min(capital * 0.05, 100.0)
            weight = max(0, min(score / 100.0, 1.0))
            bet_size = base_bet * weight
            
            if bet_size < 1.0 or capital < bet_size:
                continue # 잔고 부족
                
            shares = bet_size / our_price
            
            # 미래 시점의 결과(정산가)를 가져옴 (실제로는 t의 시간 이후에 정산되지만, 
            # 단순 복리 시뮬레이션을 위해 "지금 샀고, 최종 결과가 이렇다"를 현재 자산에 곧바로 반영하는 단순화 모델 사용.
            # (시간 엄밀성을 위해선 만기일(resolved time)을 추적하는 큐가 필요하지만, 
            # 여기서는 근사치 PnL 곡선을 그리기 위해 즉시 정산 처리)
            
            res_price = self.get_market_resolution_price(t.get('slug'), t.get('conditionId'), int(t.get('outcomeIndex', 0)))
            time.sleep(0.1) # 감마 API 레이트리밋
            
            if res_price is None:
                continue
                
            payout = shares * res_price
            profit = payout - bet_size
            
            # 자산 업데이트
            capital += profit
            
            # 승패 기록
            if res_price >= 0.99: wins += 1
            elif res_price <= 0.01: losses += 1
            else: open_positions += 1
            
            date_str = datetime.fromtimestamp(t['parsed_time']).strftime('%Y-%m-%d %H:%M')
            timeline_log.append({
                "date": date_str,
                "whale": t['whale_name'],
                "market": t.get('title'),
                "bet_size": round(bet_size, 2),
                "profit": round(profit, 2),
                "capital": round(capital, 2)
            })
            
            if (idx + 1) % 50 == 0:
                print(f"진행도: {idx+1}/{len(all_trades)}... 현재 자산: ${capital:.2f}")

        # 4. 결과 출력
        print("\n==================================================")
        print(" 📊 DEEP BACKTEST RESULT (Compound Growth)")
        print("==================================================")
        print(f"Initial Capital    : ${INITIAL_CAPITAL:.2f}")
        print(f"Final Capital      : ${capital:.2f}")
        roi = ((capital - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100
        print(f"Total ROI          : {roi:+.2f}%")
        print(f"Total Trades       : {len(timeline_log)}")
        print(f"Win/Loss/Open      : {wins} W / {losses} L / {open_positions} O")
        if (wins + losses) > 0:
            print(f"Win Rate           : {(wins / (wins+losses) * 100):.1f}%")
            
        with open("backtest_results.json", "w", encoding="utf-8") as f:
            json.dump(timeline_log, f, indent=2, ensure_ascii=False)
            
        print("\n✅ 전체 타임라인 로그가 backtest_results.json 에 저장되었습니다.")

if __name__ == "__main__":
    backtester = DeepBacktester()
    backtester.simulate()
