import time
import json
import os
import requests
from datetime import datetime
from config import config
from client_wrapper import PolymarketClient

class WhaleCopyBot:
    def __init__(self):
        self.db_file = "whales.json"
        
        # 상태 기록 (이전에 본 트랜잭션 아이디를 저장해 중복 매매 방지)
        self.seen_txs = set()
        self.positions = {}
        
        # 페이퍼 트레이딩 공통 자산
        self.bankroll = config.INITIAL_BANKROLL
        self.peak_bankroll = self.bankroll
        
        self.stats = {
            'total_bets': 0,
            'wins': 0,
            'losses': 0,
            'total_pnl': 0.0,
            'max_drawdown': 0.0
        }
        
        # 3% 슬리피지 고정
        self.slippage_pct = 0.03
        
        # 파일 경로
        self.trade_log_path = os.path.join(os.path.dirname(__file__), "trade_history.jsonl")
        self.status_file_path = os.path.join(os.path.dirname(__file__), "status_WhaleCopy.json")
        
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})
        self.client = PolymarketClient()

        print("=== 🐋 WHALE COPY BOT (PAPER MODE) ===")
        print(f"  초기 자본금: ${self.bankroll:.2f}")
        print(f"  가상 슬리피지: {self.slippage_pct * 100}% 적용")
        print("=====================================\n")

    def load_whales(self):
        """Active 고래 명단 로드"""
        if os.path.exists(self.db_file):
            try:
                with open(self.db_file, "r", encoding="utf-8") as f:
                    db = json.load(f)
                return {k: v for k, v in db.items() if v.get('status') == 'active'}
            except:
                return {}
        return {}

    def run_loop(self):
        """메인 모니터링 루프"""
        while True:
            try:
                # 1. 고래 목록 갱신 (1분마다)
                active_whales = self.load_whales()
                if not active_whales:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Active 상태인 고래가 없습니다. whales.json을 확인하세요.")
                    time.sleep(30)
                    continue

                # 2. 각 고래의 최신 Activity 조회
                for whale_addr, info in active_whales.items():
                    self._check_whale_activity(whale_addr, info['name'])

                # 3. 진행 중인 포지션 정산
                self._settle_positions()

                # 4. 대시보드 스냅샷 업데이트
                self._update_dashboard()

            except Exception as e:
                print(f"❌ 루프 에러: {e}")
                time.sleep(5)
                
            # 폴링 간격 (5초: 초당 API 1회 수준이므로 충분히 안전함)
            time.sleep(5)

    def _check_whale_activity(self, addr, name):
        """특정 고래의 최근 트랜잭션 조회 및 카피"""
        url = f"https://data-api.polymarket.com/activity?user={addr}&limit=10"
        try:
            r = self.session.get(url, timeout=5)
            if r.status_code != 200:
                return
                
            activities = r.json()
            for tx in activities:
                # 거래(TRADE)이면서 매수(BUY) 액션만
                if tx.get('type') == 'TRADE' and tx.get('side') == 'BUY':
                    tx_id = tx.get('id')
                    
                    if tx_id not in self.seen_txs:
                        # 최초 로딩 시엔 과거 내역도 묶일 수 있지만 
                        # timestamps를 체크해 너무 오래된 거면 패스 (1분 즉 60초 이내만)
                        tx_time = int(datetime.strptime(tx.get('timestamp').split('.')[0], "%Y-%m-%dT%H:%M:%S").timestamp())
                        now = int(time.time())
                        
                        self.seen_txs.add(tx_id)
                        
                        if (now - tx_time) <= 60: 
                            self._execute_copy_trade(tx, name)
                
                # 본 내역은 전부 기록해둠 (중복방지)
                self.seen_txs.add(tx.get('id'))
                            
        except Exception as e:
            pass

    def _execute_copy_trade(self, tx, whale_name):
        """가상 매매 집행"""
        # 고래의 체결가
        whale_price = float(tx.get('price', 0))
        # 무조건 3% 비싸게 샀다고 가정
        our_price = min(0.99, whale_price * (1 + self.slippage_pct))
        
        # 켈리 배팅이 아니라 고정 $10 혹은 자산의 1% 투자 (예시: 잔고의 5% 최대 $100)
        bet_size = min(self.bankroll * 0.05, 100.0) 
        if bet_size < 1.0: 
            return # 잔고 부족
            
        shares = bet_size / our_price
        
        # 포지션에 기록
        tid = tx.get('conditionId') + str(tx.get('outcomeIndex')) # Unique Key
        
        if tid in self.positions:
            print(f"🚫 이미 카피 중인 포지션입니다: {tx.get('title')}")
            return
            
        slug = tx.get('slug')
        
        # 로그 및 State 반영
        self.bankroll -= bet_size
        self.stats['total_bets'] += 1
        
        self.positions[tid] = {
            'whale_name': whale_name,
            'title': tx.get('title'),
            'side': 'YES', # 여기서 outcome index에 따라 NO일수도 있지만 제목은 정해짐
            'outcome': tx.get('outcome'),
            'entry_price': our_price,
            'size_usdc': bet_size,
            'shares': shares,
            'conditionId': tx.get('conditionId'),
            'marketId': tx.get('marketId'), # if exists
            'slug': slug,
            'timestamp': int(time.time()),
            'current_price': our_price # 초기 가격
        }
        
        print(f"\n🚨 [COPY TRADE] 🐋 {whale_name} 픽 탑승!")
        print(f"  마켓: {tx.get('title')} ({tx.get('outcome')})")
        print(f"  상대가: ${whale_price:.3f} | 진입가(슬리피지적용): ${our_price:.3f}")
        print(f"  배팅금: ${bet_size:.2f} | 남은자본금: ${self.bankroll:.2f}")
        
        # 호환성 위해 Trade Log 기록 (strategy 이름으로 분리)
        self._log_trade(tid, "WHL", "YES", tx.get('title'), our_price, bet_size, "OPEN", tx.get('marketId'))

    def _settle_positions(self):
        """진행 중인 포지션의 현재가 조회 및 정산 (정산 여부는 Gamma API 활용)"""
        to_remove = []
        for tid, pos in self.positions.items():
            # 30초마다 현재가 업데이트
            slug = pos['slug']
            cond_id = pos['conditionId']
            
            url = f"https://gamma-api.polymarket.com/events?slug={slug}"
            try:
                r = self.session.get(url, timeout=5)
                events = r.json()
                for m in events[0].get('markets', []):
                    if m.get('conditionId') == cond_id:
                        
                        # 1. Closed 인가?
                        closed = m.get('closed', False)
                        # 2. 결과가 났는가?
                        winner = self.client.get_market_winner(m.get('id', ''))
                        
                        if winner not in ['WAITING', None] or closed:
                            # 정산
                            won = (winner == pos['outcome']) or (winner == 'YES' and str(pos['outcome']).upper() == 'YES')
                            if won:
                                self._settle_as_win(tid, pos)
                            else:
                                self._settle_as_loss(tid, pos)
                            to_remove.append(tid)
                            continue
                            
                        # 아직 진행중이면 현재가만 갱신
                        prices = m.get('outcomePrices')
                        try:
                            if isinstance(prices, str): prices = json.loads(prices)
                            if prices:
                                # 보통 outcome이 YES/NO 형태이거나 토큰 리스트의 index 순서와 맞물림
                                # 좀 더 확실하게 하려면 order_book을 가져와야 함. 여기선 rough하게 0번/1번 파싱.
                                pass 
                        except: pass
            except:
                pass
                
        for tid in to_remove:
            self.positions.pop(tid, None)

    def _settle_as_win(self, tid, pos):
        payout = pos['shares'] * 1.0 # 1달러 
        profit = payout - pos['size_usdc']
        self.bankroll += payout
        self.stats['wins'] += 1
        self.stats['total_pnl'] += profit
        
        print(f"\n✅ [WIN] {pos['title']} 수익: +${profit:.2f}")
        self._log_trade(tid, "WHL", pos['outcome'], pos['title'], 1.0, payout, "WIN", pos['marketId'], pnl=profit)

    def _settle_as_loss(self, tid, pos):
        loss = -pos['size_usdc']
        self.stats['losses'] += 1
        self.stats['total_pnl'] += loss
        
        print(f"\n❌ [LOSS] {pos['title']} 손실: ${loss:.2f}")
        self._log_trade(tid, "WHL", pos['outcome'], pos['title'], 0.0, pos['size_usdc'], "LOSS", pos['marketId'], pnl=loss)

    def _log_trade(self, tid, coin, side, question, price, size, action, market_id="", pnl=0.0):
        record = {
            "strategy": "WhaleCopy",
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "coin": coin,
            "side": side,
            "size_usdc": round(size, 2),
            "pnl": round(pnl, 2),
            "price": round(price, 3),
            "question": question,
            "tid": tid,
            "marketId": market_id,
            "bankroll_after": round(self.bankroll, 2)
        }
        with open(self.trade_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _update_dashboard(self):
        settled = self.stats['wins'] + self.stats['losses']
        win_rate = (self.stats['wins'] / settled * 100) if settled > 0 else 0.0
        roi = (self.stats['total_pnl'] / config.INITIAL_BANKROLL * 100)
        
        data = {
            "strategy": "WhaleCopy",
            "timestamp": datetime.now().isoformat(),
            "pnl": round(self.stats['total_pnl'], 2),
            "equity": round(self.bankroll + sum(p['size_usdc'] for p in self.positions.values()), 2),
            "balance": round(self.bankroll, 2),
            "roi": round(roi, 1),
            "win_rate": round(win_rate, 1),
            "trades": settled,
            "active_bets": len(self.positions),
            "total_bet": round(sum(p['size_usdc'] for p in self.positions.values()), 2),
            "last_action": datetime.now().isoformat()[:19]
        }
        with open(self.status_file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)

if __name__ == '__main__':
    bot = WhaleCopyBot()
    bot.run_loop()
