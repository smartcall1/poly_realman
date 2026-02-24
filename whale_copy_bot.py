import time
import json
import os
import requests
import threading
from datetime import datetime, timedelta, timezone
from config import config
from client_wrapper import PolymarketClient
from whale_manager import run_manager
from whale_scorer import WhaleScorer

class WhaleCopyBot:
    def __init__(self):
        self.db_file = "whales.json"
        
        # 상태 기록 (이전에 본 트랜잭션 아이디를 저장해 중복 매매 방지)
        self.seen_txs = set()
        self.positions = {}
        self.pending_orders = [] # 지정가 대기 큐
        
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

        # 자동 유지보수 설정 (Background Scheduler)
        self.maintenance_thread = threading.Thread(target=self._maintenance_loop, daemon=True)
        self.maintenance_thread.start()

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
                    score = info.get('score', 50) # 기본 50점으로 간주
                    self._check_whale_activity(whale_addr, info['name'], score)

                # 스마트 진입(대기열) 처리
                self._process_pending_orders()

                # 3. 진행 중인 포지션 정산
                self._settle_positions()

                # 4. 대시보드 스냅샷 업데이트
                self._update_dashboard()

            except Exception as e:
                print(f"❌ 루프 에러: {e}")
                time.sleep(5)
                
            # 폴링 간격 (5초: 초당 API 1회 수준이므로 충분히 안전함)
            time.sleep(5)

    def _maintenance_loop(self):
        """백그라운드에서 주기적으로 고래 목록 갱신 및 스코어링 수행"""
        print("[Maintenance] Background maintenance thread started.")
        
        # 주기에 따른 실행 간격 정의
        MANAGER_INTERVAL = 24 * 3600  # 24시간마다 리더보드 전체 스캔
        SCORER_INTERVAL = 1 * 3600   # 1시간마다 스코어 및 카테고리 최신화
        
        last_manager_run = 0
        last_scorer_run = 0
        
        scorer = WhaleScorer()
        
        while True:
            now = time.time()
            
            # 1. 고래 매니저 실행 (신규 고래 발굴 및 부적격 고래 제거)
            if now - last_manager_run >= MANAGER_INTERVAL:
                try:
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ⚙️ [Maintenance] Running Whale Manager (Discovery)...")
                    run_manager()
                    last_manager_run = time.time()
                except Exception as e:
                    print(f"❌ [Maintenance] Manager Error: {e}")
            
            # 2. 고래 스코어러 실행 (카테고리 픽 분석 및 점수 갱신)
            if now - last_scorer_run >= SCORER_INTERVAL:
                try:
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ⚙️ [Maintenance] Running Whale Scorer (Tagging)...")
                    scorer.run()
                    last_scorer_run = time.time()
                except Exception as e:
                    print(f"❌ [Maintenance] Scorer Error: {e}")
            
            # 메인 거래 루프에 영향을 주지 않으려 아주 가끔씩만 체크 (1분 간격)
            time.sleep(60)

    def _check_whale_activity(self, addr, name, score):
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
                        # UTC로 들어오는 timestamps를 제대로 파싱해서 로컬 시간(now)과 비교해야 함 (타임존 버그 픽스)
                        from datetime import timezone
                        # 밀리초가 없는 경우 'Z'가 남아 에러가 나는 것을 방지
                        api_time_str = tx.get('timestamp').split('.')[0].replace('Z', '')
                        tx_time = int(datetime.strptime(api_time_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
                        now = int(time.time())
                        
                        self.seen_txs.add(tx_id)
                        
                        # 최정예 30명으로 압축했으므로 루프 속도가 빨라짐. 1분(60초) 이내의 매수만 칼타이밍으로 추적!
                        if (now - tx_time) <= 60: 
                            whale_price = float(tx.get('price', 0))
                            whale_size = float(tx.get('size', 0)) # 고래가 산 금액 (USDC)
                            slug = tx.get('slug')
                            
                            # V4: 스마트 필터 엔진 (마감일 및 카테고리 체크)
                            target_market_url = f"https://gamma-api.polymarket.com/events?slug={slug}"
                            try:
                                mr_res = self.session.get(target_market_url, timeout=3)
                                if mr_res.status_code == 200 and mr_res.json():
                                    ev_data = mr_res.json()[0]
                                    end_date_str = ev_data.get('endDate')
                                    
                                    # 1. 만기일 검증 (30일 초과 장기마켓 차단)
                                    if end_date_str:
                                        ed_dt = datetime.strptime(end_date_str.split('.')[0].replace('Z',''), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                                        days_left = (ed_dt.timestamp() - now) / 86400
                                        if days_left > 30:
                                            print(f"🚫 [SKIP] {name} 픽, 기회비용 필터 발동 (종료까지 {days_left:.1f}일 남은 장기 마켓: {slug})")
                                            self.seen_txs.add(tx_id)
                                            continue
                                            
                                    # 2. 고래 카테고리 (주종목) 검증
                                    market_tags = [t.get('label') for t in ev_data.get('tags', []) if t.get('label')]
                                    whale_top_tags = info.get('metrics', {}).get('top_categories', {})
                                    
                                    # 고래가 주종목이 하나도 등록 안 돼 있거나(초기), 교집합 태그가 있는지 확인 
                                    if whale_top_tags:
                                        # 시장 태그와 고래의 탑 3 태그 간의 교집합 검색
                                        matched_tags = set(market_tags).intersection(set(whale_top_tags.keys()))
                                        
                                        # 주종목이 아닌 경우 (태그가 전혀 안 겹침)
                                        if not matched_tags and len(market_tags) > 0:
                                            print(f"🚫 [SKIP] {name} 픽, 전공 외 픽 필터 발동 (마켓태그: {market_tags}, 고래전공: {list(whale_top_tags.keys())})")
                                            self.seen_txs.add(tx_id)
                                            continue
                            except Exception as e:
                                pass # API 로드 실패 시 보수적으로 그냥 일단 넘어감 (필터 미적용 패스)
                               
                            
                            # 고래 액수에 따른 다이나믹 슬리피지 (고래가 많이 샀을수록 허용폭을 넓힘)
                            if whale_size >= 5000:
                                slippage_modifier = 0.05 # 5,000불 이상 초거대 매수: 5% 슬리피지 허용 (무조건 따라붙기)
                            elif whale_size >= 1000:
                                slippage_modifier = 0.03 # 1,000불 이상: 3% 허용
                            elif whale_size >= 100:
                                slippage_modifier = 0.01 # 100불 이상: 1% 허용
                            else:
                                slippage_modifier = 0.005 # 소액 잡코인: 0.5% (사실상 찍먹)
                            
                            # 스코어가 높으면 슬리피지 여유를 1% 추가로 줌
                            if score >= 80:
                                slippage_modifier += 0.01 
                                
                            target_price = min(0.99, whale_price * (1 + slippage_modifier))
                            
                            token_id = tx.get('asset') # 트랜잭션의 token_id
                            
                            # 우리가 살 금액 (잔고비례)
                            base_bet_size = min(self.bankroll * 0.05, 100.0) 
                            weight = max(0, min(score / 100.0, 1.0))
                            bet_size = base_bet_size * weight
                            
                            # 호가창(Orderbook) 뒤져서 예상 체결가 산출
                            vwap_price = self.client.simulate_market_buy_vwap(token_id, bet_size)
                            
                            if vwap_price is not None and vwap_price <= target_price:
                                print(f"\n⚡ [FAST EXECUTE] 🐋 {name} 픽, 호가창 포착 즉시 매수!")
                                print(f"  고래매수가: ${whale_price:.3f} (규모: ${whale_size:.0f}) | VWAP평단가: ${vwap_price:.3f} | 한도: ${target_price:.3f}")
                                self._execute_copy_trade(tx, name, score, vwap_price)
                            else:
                                print(f"\n⏳ [PENDING Queue] 🐋 {name} 픽, 호가창 유동성 부족/가격 이탈 -> 대기열 등록 (1분)")
                                if vwap_price:
                                    print(f"  VWAP평단가: ${vwap_price:.3f} > 한도: ${target_price:.3f}")
                                else:
                                    print(f"  호가창 분석 실패 또는 잔량 부족")
                                    
                                self.pending_orders.append({
                                    "tx": tx,
                                    "whale_name": name,
                                    "score": score,
                                    "whale_price": whale_price,
                                    "target_price": target_price,
                                    "bet_size": bet_size,
                                    "expires_at": now + 60 # 즉시 체결 못했으면 1분만 기다림 (너무 기다리면 포모)
                                })
                
                # 본 내역은 전부 기록해둠 (중복방지)
                self.seen_txs.add(tx.get('id'))
                            
        except Exception as e:
            # 모바일 환경에서 갑자기 통신이 끊기거나 파싱 에러가 날 때 원인을 파악할 수 있도록 표기 (무시하지 않음)
            print(f"⚠️ [Error] _check_whale_activity failed for {name}: {e}")

    def _get_gamma_price(self, slug, conditionId, outcomeIndex):
        url = f"https://gamma-api.polymarket.com/events?slug={slug}"
        try:
            r = self.session.get(url, timeout=5)
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

    def _process_pending_orders(self):
        if not self.pending_orders:
            return
            
        now = int(time.time())
        active_orders = []
        
        for order in self.pending_orders:
            if now > order['expires_at']:
                print(f"⏰ [EXPIRED] {order['whale_name']} 픽 체결 실패 (시장가가 목표가 ${order['target_price']:.3f} 이내로 오지 않음)")
                continue
                
            tx = order['tx']
            token_id = tx.get('asset')
            bet_size = order['bet_size']
            
            # 큐에서도 호가창 긁어서 (VWAP) 바로 체결각 재기
            vwap_price = self.client.simulate_market_buy_vwap(token_id, bet_size)
            
            if vwap_price is not None and vwap_price <= order['target_price']:
                print(f"✅ [PENDING Filled] 🐋 {order['whale_name']} 픽 체결! (VWAP: ${vwap_price:.3f} <= ${order['target_price']:.3f})")
                self._execute_copy_trade(tx, order['whale_name'], order['score'], vwap_price)
            else:
                active_orders.append(order)
                
        self.pending_orders = active_orders

    def _execute_copy_trade(self, tx, whale_name, score, executed_price):
        """가상 매매 집행 (bet_size가 외부에서 주어지거나 여기서 계산되지만 일원화를 위해 여기서 계산 유지)"""
        # 켈리 배팅이 아니라 고정 $10 혹은 자산의 1% 투자 (예시: 잔고의 5% 최대 $100)
        base_bet_size = min(self.bankroll * 0.05, 100.0) 
        
        # 스코어에 비례하여 투자 비중 조절 (100점 -> 최대비중, 50점 -> 절반)
        weight = max(0, min(score / 100.0, 1.0))
        bet_size = base_bet_size * weight
        
        if bet_size < 1.0: 
            print(f"🚫 [SKIP] {whale_name} 픽, 스코어/잔고 부족 (산출금: ${bet_size:.2f})")
            return # 잔고 부족
            
        shares = bet_size / executed_price
        
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
            'entry_price': executed_price,
            'size_usdc': bet_size,
            'shares': shares,
            'conditionId': tx.get('conditionId'),
            'marketId': tx.get('marketId'), # if exists
            'slug': slug,
            'timestamp': int(time.time()),
            'current_price': executed_price # 초기 가격
        }
        
        whale_price = float(tx.get('price', 0))
        print(f"\n🚨 [COPY TRADE] 🐋 {whale_name} 픽 탑승!")
        print(f"  마켓: {tx.get('title')} ({tx.get('outcome')})")
        print(f"  상대가: ${whale_price:.3f} | 실제 체결가: ${executed_price:.3f}")
        print(f"  배팅금: ${bet_size:.2f} | 남은자본금: ${self.bankroll:.2f}")
        
        # 호환성 위해 Trade Log 기록 (strategy 이름으로 분리)
        self._log_trade(tid, "WHL", "YES", tx.get('title'), executed_price, bet_size, "OPEN", tx.get('marketId'))

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
