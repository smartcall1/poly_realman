import time
import json
import os
import asyncio
import aiohttp
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
        
        self.async_session = None
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

    async def run_loop(self):
        """메인 모니터링 루프"""
        self.async_session = aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0"})
        
        # 백그라운드 태스크 시작
        asyncio.create_task(self._maintenance_loop())
        asyncio.create_task(self._pending_order_loop())
        
        try:
            while True:
                try:
                    # 1. 고래 목록 갱신 (1분마다)
                    active_whales = self.load_whales()
                    if not active_whales:
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] ⚠️ Active 상태인 고래가 없습니다. whales.json을 확인하세요.")
                        await asyncio.sleep(30)
                        continue

                    # 2. 각 고래의 최신 Activity 병렬 조회
                    tasks = [
                        self._check_whale_activity(whale_addr, info['name'], info.get('score', 50))
                        for whale_addr, info in active_whales.items()
                    ]
                    await asyncio.gather(*tasks, return_exceptions=True)

                    # 3. 진행 중인 포지션 정산
                    await self._settle_positions()

                    # 4. 대시보드 스냅샷 업데이트
                    self._update_dashboard()

                except Exception as e:
                    print(f"❌ 루프 에러: {e}")
                    await asyncio.sleep(5)
                    
                # 폴링 간격 (5초: 병렬 스캔이므로 폴링 속도 극대화 가능)
                await asyncio.sleep(5)
        finally:
            await self.async_session.close()

    async def _pending_order_loop(self):
        """1초 주기로 pending_orders에 등록된 지정가 큐를 확인하여 체결 시도 (비동기 독립 스레드)"""
        while True:
            try:
                await self._process_pending_orders()
            except Exception as e:
                print(f"❌ Pending Loop Error: {e}")
            await asyncio.sleep(1)

    async def _maintenance_loop(self):
        """백그라운드에서 주기적으로 고래 목록 갱신 및 스코어링 수행"""
        print("[Maintenance] Background maintenance task started.")
        
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
                    await asyncio.to_thread(run_manager)
                    last_manager_run = time.time()
                except Exception as e:
                    print(f"❌ [Maintenance] Manager Error: {e}")
            
            # 2. 고래 스코어러 실행 (카테고리 픽 분석 및 점수 갱신)
            if now - last_scorer_run >= SCORER_INTERVAL:
                try:
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] ⚙️ [Maintenance] Running Whale Scorer (Tagging)...")
                    await asyncio.to_thread(scorer.run)
                    last_scorer_run = time.time()
                except Exception as e:
                    print(f"❌ [Maintenance] Scorer Error: {e}")
            
            # 메인 거래 루프에 영향을 주지 않으려 아주 가끔씩만 체크 (1분 간격)
            await asyncio.sleep(60)

    async def _check_whale_activity(self, addr, name, score):
        """특정 고래의 최근 트랜잭션 비동기 조회 및 카피"""
        url = f"https://data-api.polymarket.com/activity?user={addr}&limit=10"
        try:
            async with self.async_session.get(url, timeout=5) as r:
                if r.status != 200:
                    return
                activities = await r.json()
                
            for tx in activities:
                # 1. 고래의 매집 (BUY) 액션 모니터링
                if tx.get('type') == 'TRADE' and tx.get('side') == 'BUY':
                    tx_id = tx.get('id')
                    
                    if tx_id not in self.seen_txs:
                        api_time_str = tx.get('timestamp').split('.')[0].replace('Z', '')
                        tx_time = int(datetime.strptime(api_time_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
                        now = int(time.time())
                        
                        self.seen_txs.add(tx_id)
                        
                        if (now - tx_time) <= 60: 
                            whale_price = float(tx.get('price', 0))
                            whale_size = float(tx.get('size', 0))
                            slug = tx.get('slug')
                            
                            target_market_url = f"https://gamma-api.polymarket.com/events?slug={slug}"
                            try:
                                async with self.async_session.get(target_market_url, timeout=3) as mr_res:
                                    if mr_res.status == 200:
                                        data = await mr_res.json()
                                        if data:
                                            ev_data = data[0]
                                            end_date_str = ev_data.get('endDate')
                                            
                                            if end_date_str:
                                                ed_dt = datetime.strptime(end_date_str.split('.')[0].replace('Z',''), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                                                days_left = (ed_dt.timestamp() - now) / 86400
                                                if days_left > 30:
                                                    print(f"🚫 [SKIP] {name} 픽, 기회비용 필터 발동 (종료까지 {days_left:.1f}일 남은 장기 마켓: {slug})")
                                                    continue
                                                    
                                            info = self.load_whales().get(addr, {})
                                            market_tags = [t.get('label') for t in ev_data.get('tags', []) if t.get('label')]
                                            whale_top_tags = info.get('metrics', {}).get('top_categories', {})
                                            
                                            if whale_top_tags:
                                                matched_tags = set(market_tags).intersection(set(whale_top_tags.keys()))
                                                if not matched_tags and len(market_tags) > 0:
                                                    print(f"🚫 [SKIP] {name} 픽, 전공 외 픽 필터 발동 (마켓태그: {market_tags}, 고래전공: {list(whale_top_tags.keys())})")
                                                    continue
                            except Exception as e:
                                pass
                               
                            if whale_size >= 5000:
                                slippage_modifier = 0.07 
                            elif whale_size >= 1000:
                                slippage_modifier = 0.05 
                            elif whale_size >= 100:
                                slippage_modifier = 0.03 
                            else:
                                slippage_modifier = 0.015 
                            
                            if score >= 90:
                                slippage_modifier = max(slippage_modifier, 0.15) 
                                print(f"💎 [VIP PASS] 90점 이상 최상급 고래({name}, {score}점) 픽! 슬리피지 15% 개방")
                            elif score >= 80:
                                slippage_modifier += 0.02 
                                
                            ev_ceiling = (score / 100.0) * 0.95
                            target_price = min(0.99, max(whale_price * (1 + slippage_modifier), ev_ceiling))
                            # 켈리 베팅(Kelly Criterion) 산출: p = 승률, b = 배당비율
                            p = score / 100.0
                            # 보수적인 EV 계산을 위해 우리가 살 수 있는 최악의 가격(target_price)을 기준으로 계산
                            b = (1.0 - target_price) / target_price if target_price > 0 and target_price < 1.0 else 0
                            
                            if b > 0:
                                kelly_f = p - ((1.0 - p) / b)
                            else:
                                kelly_f = -1.0
                                
                            fractional_kelly = kelly_f * 0.5 # Half Kelly (안전형)
                            
                            if fractional_kelly > 0:
                                # EV가 플러스인 꿀자리: 잔고의 최대 15%까지 투자 (과감한 배팅)
                                bet_fraction = min(fractional_kelly, 0.15)
                                bet_size = self.bankroll * bet_fraction
                                bet_type = "KELLY"
                                print(f"🧠 [KELLY] EV Positive! 켈리 배팅 비율: {bet_fraction*100:.1f}%")
                            else:
                                # EV가 마이너스인 쓰레기 자리: 정찰병만 보냄 (잔고의 1% 또는 $20 중 작은 값)
                                bet_size = min(self.bankroll * 0.01, 20.0)
                                bet_type = "SCOUT"
                                print(f"🛡️ [SCOUT] EV Negative (f={kelly_f:.2f}). 정찰병 배팅 투입.")
                            
                            vwap_price = await asyncio.to_thread(self.client.simulate_market_buy_vwap, token_id, bet_size)
                            
                            idx = tx.get('outcomeIndex', 0)
                            if vwap_price is not None and vwap_price <= target_price:
                                print(f"\n⚡ [FAST EXECUTE] 🐋 {name} 픽, 매수 체결! ({bet_type})")
                                self._execute_copy_trade(tx, name, score, vwap_price, str(idx), bet_size)
                            else:
                                print(f"\n⏳ [PENDING] 🐋 {name} 픽, 목표가 {target_price:.3f} 대기열 등록 ({bet_type})")
                                self.pending_orders.append({
                                    "tx": tx,
                                    "whale_name": name,
                                    "score": score,
                                    "whale_price": whale_price,
                                    "target_price": target_price,
                                    "bet_size": bet_size,
                                    "idx": str(idx),
                                    "expires_at": now + 300
                                })
                                
                # 2. 고래의 덤핑 (SELL) 액션 모니터링 (Mirror Exit)
                elif tx.get('type') == 'TRADE' and tx.get('side') == 'SELL':
                    tx_id = tx.get('id')
                    if tx_id not in self.seen_txs:
                        self.seen_txs.add(tx_id)
                        
                        slug = tx.get('slug')
                        for tid, pos in list(self.positions.items()):
                            if pos['slug'] == slug and pos['whale_name'] == name:
                                # 고래가 해당 종목을 던졌으므로 미러링 액션 발동
                                print(f"👀 [WATCH] 🐋 고래 {name}가 {tx.get('title')} 종목을 매도했습니다! 추격 청산 준비...")
                                
                                token_id = tx.get('asset')
                                sell_size = pos['shares'] # 전량 매도
                                
                                # 시장가(VWAP)로 매도 가격 산출 (simulate_market_sell_vwap 함수는 추후 고도화 필요, 현재는 직전 price 참조)
                                current_vwap = await asyncio.to_thread(self.client.simulate_market_buy_vwap, token_id, 10)
                                if not current_vwap:
                                    current_vwap = float(tx.get('price', 0))
                                    
                                self._execute_sell(tid, pos, current_vwap, "MIRROR")
                            
        except Exception as e:
            # 모바일 환경에서 갑자기 통신이 끊기거나 파싱 에러가 날 때 원인을 파악할 수 있도록 표기 (무시하지 않음)
            print(f"⚠️ [Error] _check_whale_activity failed for {name}: {e}")

    async def _get_gamma_price(self, slug, conditionId, outcomeIndex):
        url = f"https://gamma-api.polymarket.com/events?slug={slug}"
        try:
            async with self.async_session.get(url, timeout=5) as r:
                events = await r.json()
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

    async def _process_pending_orders(self):
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
            
            # 큐에서도 호가창 긁어서 (VWAP) 바로 체결각 재기 (블로킹이므로 to_thread)
            vwap_price = await asyncio.to_thread(self.client.simulate_market_buy_vwap, token_id, bet_size)
            
            if vwap_price is not None and vwap_price <= order['target_price']:
                print(f"✅ [PENDING Filled] 🐋 {order['whale_name']} 픽 체결! (VWAP: ${vwap_price:.3f} <= ${order['target_price']:.3f})")
                self._execute_copy_trade(tx, order['whale_name'], order['score'], vwap_price, order['idx'], bet_size)
            else:
                active_orders.append(order)
                
        self.pending_orders = active_orders

    def _execute_copy_trade(self, tx, whale_name, score, executed_price, outcome_idx="0", computed_bet_size=None):
        """가상 매매 집행"""
        bet_size = computed_bet_size if computed_bet_size else 10.0 # 에러 방지용 Fallback
        
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
            'side': 'YES', 
            'outcome': tx.get('outcome'),
            'outcomeIndex': outcome_idx, # idx 저장
            'entry_price': executed_price,
            'size_usdc': bet_size,
            'shares': shares,
            'conditionId': tx.get('conditionId'),
            'marketId': tx.get('marketId'), 
            'slug': slug,
            'timestamp': int(time.time()),
            'current_price': executed_price 
        }
        
        whale_price = float(tx.get('price', 0))
        print(f"\n🚨 [COPY TRADE] 🐋 {whale_name} 픽 탑승!")
        print(f"  마켓: {tx.get('title')} ({tx.get('outcome')})")
        print(f"  상대가: ${whale_price:.3f} | 실제 체결가: ${executed_price:.3f}")
        print(f"  배팅금: ${bet_size:.2f} | 남은자본금: ${self.bankroll:.2f}")
        
        # 호환성 위해 Trade Log 기록 (strategy 이름으로 분리)
        self._log_trade(tid, "WHL", "YES", tx.get('title'), executed_price, bet_size, "OPEN", tx.get('marketId'))

    async def _settle_positions(self):
        """진행 중인 포지션의 현재가 조회 및 정산 (정산 여부는 Gamma API 활용)"""
        to_remove = []
        for tid, pos in self.positions.items():
            # 30초마다 현재가 업데이트
            slug = pos['slug']
            cond_id = pos['conditionId']
            
            url = f"https://gamma-api.polymarket.com/events?slug={slug}"
            try:
                async with self.async_session.get(url, timeout=5) as r:
                    events = await r.json()
                    
                    for m in events[0].get('markets', []):
                        if m.get('conditionId') == cond_id:
                            
                            # 1. Closed 인가?
                            closed = m.get('closed', False)
                            # 2. 결과가 났는가? (동기 blocking이므로 to_thread)
                            winner = await asyncio.to_thread(self.client.get_market_winner, m.get('id', ''))
                            
                            if winner not in ['WAITING', None] or closed:
                                # 정산
                                won = (winner == pos['outcome']) or (winner == 'YES' and str(pos['outcome']).upper() == 'YES')
                                if won:
                                    self._settle_as_win(tid, pos)
                                else:
                                    self._settle_as_loss(tid, pos)
                                to_remove.append(tid)
                                continue
                                
                            # 아직 진행중이면 현재가 기반 청산 규칙(TP/SL/Timeout) 검사
                            prices = m.get('outcomePrices')
                            try:
                                if isinstance(prices, str): prices = json.loads(prices)
                                if prices:
                                    # 해당 마켓의 내가 샀던 outcomeIndex 찾기 처리 (단순화: prices[int(pos['outcomeIndex'])])
                                    current_price = float(prices[0]) # 임시 단순화 (보통 YES는 인덱스 0)
                                    # 고도화(추후): outcome 문자열과 인덱스 매칭, 지금은 일단 첫 번째 가격(YES) 기준
                                    
                                    shares = pos['shares']
                                    current_value = shares * current_price
                                    roi = (current_value - pos['size_usdc']) / pos['size_usdc'] * 100
                                    
                                    # 1. 20% 수익 달성 시 익절 (Hard TP)
                                    if roi >= 20.0:
                                        self._execute_sell(tid, pos, current_price, "TAKE PROFIT")
                                        to_remove.append(tid)
                                        continue
                                        
                                    # 2. -30% 손실 시 손절 (Hard SL)
                                    if roi <= -30.0:
                                        self._execute_sell(tid, pos, current_price, "STOP LOSS")
                                        to_remove.append(tid)
                                        continue
                                    
                                    # 3. 타임아웃 청산 (7일 초과)
                                    days_held = (int(time.time()) - pos['timestamp']) / 86400
                                    if days_held > 7.0:
                                        self._execute_sell(tid, pos, current_price, "TIMEOUT")
                                        to_remove.append(tid)
                                        continue

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

    def _execute_sell(self, tid, pos, sell_price, reason="TP/SL"):
        """보유 중인 포지션을 수동 매도(청산) 처리"""
        if tid not in self.positions:
            return
            
        shares = pos['shares']
        payout = shares * sell_price
        profit = payout - pos['size_usdc']
        
        self.bankroll += payout
        if profit >= 0:
            self.stats['wins'] += 1
        else:
            self.stats['losses'] += 1
            
        self.stats['total_pnl'] += profit
        
        icon = "✅ [TAKE PROFIT]" if profit >= 0 else "🚨 [STOP LOSS]"
        if reason == "MIRROR":
            icon = "👀 [MIRROR EXIT]"
        elif reason == "TIMEOUT":
            icon = "⏳ [TIMEOUT EXIT]"
            
        print(f"\n{icon} {pos['title']} 청산 완료!")
        print(f"  매수 평단: ${pos['entry_price']:.3f} -> 매도 평단: ${sell_price:.3f}")
        print(f"  수익금: ${profit:+.2f} | 회수금: ${payout:.2f}")
        
        self._log_trade(tid, "WHL", pos['outcome'], pos['title'], sell_price, payout, reason, pos['marketId'], pnl=profit)
        self.positions.pop(tid, None)


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
    try:
        asyncio.run(bot.run_loop())
    except KeyboardInterrupt:
        print("\n봇 종료 중...")
