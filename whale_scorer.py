import json
import os
import requests
import time
from datetime import datetime, timedelta, timezone
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

DB_FILE = "whales.json"

# 점수 부여 기준 (가중치)
WEIGHT_PROFIT = 0.40
WEIGHT_WIN_RATE = 0.40
WEIGHT_FREQUENCY = 0.20

class WhaleScorer:
    def __init__(self):
        self.session = requests.Session()
        
        # 재시도 로직 추가 (타임아웃 방지)
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[ 500, 502, 503, 504, 520, 524 ])
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        
        self.session.headers.update({"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        self.db_file = os.path.join(os.path.dirname(__file__), DB_FILE)
        
    def load_db(self):
        if os.path.exists(self.db_file):
            with open(self.db_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}
        
    def save_db(self, db):
        with open(self.db_file, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=4, ensure_ascii=False)
            
    def fetch_whale_stats(self, address):
        """감마 API를 통해 고래의 전체적인 수익 통계를 가져옵니다."""
        url = f"https://gamma-api.polymarket.com/events?slug={address}" # 실제로는 /users/{address}/profit 등 적절한 엔드포인트 필요. 폴리마켓 공식 통계 API 한계상, 이 부분은 클록(Clob)이나 퍼블릭 프로필 데이터를 긁어야 할 수 있습니다.
        # Polymarket 프로필 데이터 엔드포인트 
        profile_url = f"https://clob.polymarket.com/users/{address}/performance" # 예시(실제와 다를수있음)
        
        # 여기서는 좀 더 확실하게 동작하는 activity API를 기준으로 30일 승률을 긁어오는 '수동' 계산 방식을 채택하거나, 
        # Clob API의 profit/loss 데이터를 활용할 수 있습니다.
        # 일단 가장 간단하게, 기존 방식대로 activity를 긁어서 자체 계산하는 방식으로 베이스라인을 잡겠습니다.
        pass

    def calculate_score(self, address, thirty_days_ago, info):
        """특정 고래의 최근 30일치 활동을 바탕으로 점수를 계산합니다."""
        url = f'https://data-api.polymarket.com/activity?user={address}&limit=500'
        try:
            r = self.session.get(url, timeout=10)
            if r.status_code != 200:
                print(f"[{address}] API 에러: {r.status_code}")
                return None
                
            activities = r.json()
            
            # 1. 거래 빈도 및 카테고리 승률 분석
            trade_count = 0
            category_stats = {} # {"Politics": {"wins": 2, "total": 3}, ...}
            market_tags_cache = {} # 중복 API 호출 방지
            
            for t in activities:
                if t.get('type') == 'TRADE' and t.get('side') == 'BUY':

                    timestamp_val = t.get('timestamp')
                    if isinstance(timestamp_val, str):
                        api_time_str = timestamp_val.split('.')[0]
                        try:
                            tx_time = datetime.strptime(api_time_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                        except ValueError:
                            continue
                    else:
                        tx_time = datetime.fromtimestamp(int(timestamp_val), timezone.utc)
                    
                    if tx_time > thirty_days_ago:
                        trade_count += 1
                        
                # 1.5 카테고리 태깅 정보 수집 (최초 로드 시 최근 100개만 제한하여 API 부하 방지 - 너무 많으면 오래 걸림)
                if t.get('type') == 'TRADE' and t.get('side') == 'BUY':
                    slug = t.get('slug')
                    if not slug: continue
                    
                    if slug not in market_tags_cache:
                        # Gamma API에서 이벤트 태그 조회
                        gamma_url = f"https://gamma-api.polymarket.com/events?slug={slug}"
                        try:
                            gr = self.session.get(gamma_url, timeout=3)
                            if gr.status_code == 200 and gr.json():
                                ev = gr.json()[0]
                                tags = ev.get('tags', [])
                                market_tags_cache[slug] = [tag.get('label') for tag in tags if tag.get('label')]
                            else:
                                market_tags_cache[slug] = []
                            time.sleep(0.1) # 짧은 Rate Limit 방어
                        except:
                            market_tags_cache[slug] = []
                    
                    tags = market_tags_cache.get(slug, [])
                    if not tags: tags = ["Unknown"]
                    
                    # 승패 판별 (이미 정산된 마켓의 경우 price가 아니라 Polymarket Activity API 상에 종종 WIN/LOSS가 남거나 outcome값으로 유추, 
                    # 여기서는 근사치로 PnL 계산을 하진 않고 단순히 '이 카테고리 거래를 했다' 정도로도 빈도를 측정할 수 있지만, 
                    # 정확히 하려면 API 한계상 승패까지 다 구해오기 너무 무거우므로, 
                    # 일단 이 고래가 어느 분야(분모)에 몇 번 베팅했는지 분과비율을 잡겠습니다. 
                    # => 추후 심화: PnL이 아니라 활동 '비중'과 '전체 승률'을 합산하는 방식.
                    # V4 1차 목표: 카테고리별 베팅 분포수 파악
                    for tag in tags:
                        if tag not in category_stats:
                            category_stats[tag] = 0
                        category_stats[tag] += 1
                        
            # top 카테고리 3개 추출
            sorted_categories = sorted(category_stats.items(), key=lambda x: x[1], reverse=True)[:3]
            top_tags = {k: v for k, v in sorted_categories}
            
            # 2. 로컬 DB(whales.json)에 저장된 승률과 수익률 가져오기
            win_rate = float(info.get('win_rate', 0.0))
            roi = float(info.get('roi', 0.0))
            
            # 점수 정규화 로직 (100점 만점)
            # 1. 빈도 점수 (월 30회 이상이면 만점)
            freq_score = min(trade_count / 30.0 * 100, 100.0)
            
            # 2. 승률 점수 (50% 이하면 0점, 80% 이상이면 100점)
            win_score = max(0, min((win_rate - 50) / 30.0 * 100, 100.0))
            
            # 3. 수익률 점수 (0% 이하면 0점, 50% 이상이면 100점)
            roi_score = max(0, min((roi - 0) / 50.0 * 100, 100.0))
            
            final_score = (
                (freq_score * WEIGHT_FREQUENCY) +
                (win_score * WEIGHT_WIN_RATE) +
                (roi_score * WEIGHT_PROFIT)
            )
            
            return {
                "score": round(final_score, 1),
                "metrics": {
                    "30d_trades": trade_count,
                    "win_rate": round(win_rate, 2),
                    "roi": round(roi, 2),
                    "top_categories": top_tags # 새로 추가된 카테고리 태그
                }
            }
            
        except Exception as e:
            print(f"[{address}] 분석 중 에러: {e}")
            return None

    def run(self):
        print("=== Whale Scoring Started ===")
        db = self.load_db()
        thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
        
        for addr, info in db.items():
            if info.get('status') != 'active':
                continue
                
            print(f"Analyzing: {info.get('name', 'Unknown')} ({addr[:8]}...)")
            stats = self.calculate_score(addr, thirty_days_ago, info)
            
            if stats:
                db[addr]['score'] = stats['score']
                db[addr]['metrics'] = stats['metrics']
                print(f"  -> Final Score: {stats['score']} (Trades:{stats['metrics']['30d_trades']}, WinRate:{stats['metrics']['win_rate']}%, ROI:{stats['metrics']['roi']}%)")
            
            time.sleep(1) # Rate limit
            
        self.save_db(db)
        print("\n✅ whales.json score update completed!")

if __name__ == "__main__":
    scorer = WhaleScorer()
    scorer.run()
