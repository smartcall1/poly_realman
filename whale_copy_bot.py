import sys

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
        self.startup_time = int(time.time())  # 봇 시작 시각 (백로그 필터용)
        self.MAX_POSITIONS = config.MAX_POSITIONS  # .env에서 설정
        
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
        self.state_file_path = os.path.join(os.path.dirname(__file__), "state_WhaleCopy.json")

        # 이전 세션 상태 복구
        self._load_state()

        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})
        self.client = PolymarketClient()

        # 자동 유지보수 설정 (Background Scheduler)
        self.maintenance_thread = threading.Thread(target=self._maintenance_loop, daemon=True)
        self.maintenance_thread.start()

        # 봇 시작 시 status 파일 초기화 (이전 세션 PnL 잔상 제거)
        self._update_dashboard()

        print("=== 🐋 WHALE COPY BOT (PAPER MODE) ===")
        print(f"  초기 자본금: ${self.bankroll:.2f}")
        print(f"  가상 슬리피지: {self.slippage_pct * 100}% 적용")
        print("=====================================\n")

    def load_whales(self):
        """Active 고래 명단 로드 (score 순 상위 50개 제한)"""
        if os.path.exists(self.db_file):
            try:
                with open(self.db_file, "r", encoding="utf-8") as f:
                    db = json.load(f)
                actives = {k: v for k, v in db.items() if v.get('status') == 'active'}
                sorted_whales = sorted(actives.items(), key=lambda x: x[1].get('score', 0), reverse=True)
                return dict(sorted_whales[:30])
            except Exception as e:
                print(f"[WARN] whales.json 파싱 실패: {e}")
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
                    self._check_whale_activity(whale_addr, info['name'], score, info)

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

    def _check_whale_activity(self, addr, name, score, info=None):
        """특정 고래의 최근 트랜잭션 조회 및 카피"""
        if info is None:
            info = {}
        url = f"https://data-api.polymarket.com/activity?user={addr}&limit=10"
        try:
            r = self.session.get(url, timeout=5)
            if r.status_code != 200:
                return

            activities = r.json()
            now = int(time.time())

            # seen_txs 메모리 한계 방어 (10,000건 초과 시 절반 삭제)
            if len(self.seen_txs) > 10000:
                self.seen_txs = set(list(self.seen_txs)[5000:])

            for tx in activities:
                # BUG FIX4: 'id' 필드는 없음. transactionHash 사용
                tx_id = tx.get('transactionHash') or tx.get('id')
                if not tx_id or tx_id in self.seen_txs:
                    continue
                self.seen_txs.add(tx_id)

                tx_type = tx.get('type')
                tx_side = tx.get('side')

                # [Mirror Exit] 고래 SELL 감지 → 같은 고래가 카피한 포지션만 동반 청산
                if tx_type == 'TRADE' and tx_side == 'SELL':
                    cond_id = tx.get('conditionId') or ''
                    base_tid = cond_id + str(tx.get('outcomeIndex', 0))
                    exited = False
                    for pos_tid in list(self.positions.keys()):
                        if pos_tid == base_tid or pos_tid.startswith(base_tid + "_"):
                            pos = self.positions[pos_tid]
                            if pos.get('whale_name') == name:
                                self.positions.pop(pos_tid)
                                current_price = pos.get('current_price', pos['entry_price'])
                                self._execute_early_exit(pos_tid, pos, current_price, "MIRROR_EXIT")
                                print(f"🔄 [MIRROR EXIT] {name} SELL 감지 → 동반 청산 완료 ({pos_tid})")
                                exited = True
                    if exited:
                        self._save_state()
                    continue

                # 매수(BUY)만 이하 처리
                if tx_type != 'TRADE' or tx_side != 'BUY':
                    continue

                # [Filter 1] startup_time 백로그 방지 (봇 시작 전 거래 스킵)
                timestamp_val = tx.get('timestamp')
                try:
                    if isinstance(timestamp_val, (int, float)):
                        tx_time = int(timestamp_val)
                        # 밀리초 단위 감지 (1e12 초 = 서기 33658년 → 불가능, 밀리초임)
                        if tx_time > 1_000_000_000_000:
                            tx_time = tx_time // 1000
                    else:
                        api_time_str = str(timestamp_val).split('.')[0]
                        # 숫자형 문자열인 경우 (e.g. "1740743100")
                        if api_time_str.isdigit():
                            tx_time = int(api_time_str)
                            if tx_time > 1_000_000_000_000:
                                tx_time = tx_time // 1000
                        else:
                            tx_time = int(datetime.strptime(api_time_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
                except Exception:
                    continue

                if tx_time < self.startup_time:
                    continue

                # [Filter 2] 30분(1800초) 이내 거래만 처리
                if (now - tx_time) > 1800:
                    continue

                whale_price = float(tx.get('price', 0))
                whale_size = float(tx.get('size', 0))
                slug = tx.get('slug')

                # [Filter 3] 가격 범위 필터 (0.05 미만 = 5% 이하 확률 마켓 거부, 0.95 이상 = 정산 직전 마켓 거부)
                # 이유: price 0.01 마켓에 진입 시 shares가 폭발적으로 늘어 페이퍼 PnL이 비현실적으로 커짐
                if whale_price < 0.05 or whale_price >= 0.95:
                    if whale_price < 0.05:
                        print(f"🚫 [SKIP] 저확률 마켓 거부 (price={whale_price:.3f} < 0.05): {tx.get('title', '')[:40]}")
                    continue

                # [Filter 4] MAX_POSITIONS 체크
                if len(self.positions) >= self.MAX_POSITIONS:
                    print(f"🚫 [SKIP] 최대 포지션 한도 도달 ({self.MAX_POSITIONS}개)")
                    continue

                # 동일 마켓 반감기를 위해 conditionId/outcomeIndex 사전 추출
                # (고래 불문 동일 마켓 기존 포지션 수에 따라 베팅 반감기 적용)
                _cid = tx.get('conditionId') or ''
                _oidx = int(tx.get('outcomeIndex', 0))

                # [Filter 5] Gamma API 마켓 상태 확인
                target_market_url = f"https://gamma-api.polymarket.com/events?slug={slug}"
                try:
                    mr_res = self.session.get(target_market_url, timeout=3)
                    if mr_res.status_code == 200 and mr_res.json():
                        ev_data = mr_res.json()[0]
                        end_date_str = ev_data.get('endDate')

                        # 만기일 검증 (30일 초과 장기마켓 차단)
                        if end_date_str:
                            ed_dt = datetime.strptime(end_date_str.split('.')[0].replace('Z', ''), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                            days_left = (ed_dt.timestamp() - now) / 86400
                            if days_left > 30:
                                print(f"🚫 [SKIP] {name} 픽, 장기 마켓 ({days_left:.1f}일 남음): {slug}")
                                continue

                        # 고래 카테고리(주종목) 검증
                        market_tags = [t.get('label') for t in ev_data.get('tags', []) if t.get('label')]
                        whale_top_tags = info.get('metrics', {}).get('top_categories', {})
                        if whale_top_tags and market_tags:
                            matched_tags = set(market_tags).intersection(set(whale_top_tags.keys()))
                            if not matched_tags:
                                print(f"🚫 [SKIP] {name} 전공 외 픽 (마켓: {market_tags}, 전공: {list(whale_top_tags.keys())})")
                                continue
                except Exception as e:
                    print(f"[WARN] Gamma 마켓 필터 API 실패 ({name}): {e} → Fail Open으로 진행")

                # 다이나믹 슬리피지
                if whale_size >= 5000:
                    slippage_modifier = 0.05
                elif whale_size >= 1000:
                    slippage_modifier = 0.03
                elif whale_size >= 100:
                    slippage_modifier = 0.01
                else:
                    slippage_modifier = 0.005

                if score >= 80:
                    slippage_modifier += 0.01

                target_price = min(0.99, whale_price * (1 + slippage_modifier))
                token_id = tx.get('asset')

                base_bet_size = min(self.bankroll * 0.05, 100.0)
                weight = max(0, min(score / 100.0, 1.0))
                bet_size = base_bet_size * weight

                # 동일 마켓 기존 포지션 수에 따라 베팅 반감기 적용
                # (다른 고래가 같은 마켓을 독립적으로 픽할수록 확신도↑, 하지만 추가 리스크↑ → 베팅 절반씩 감소)
                existing_in_market = sum(
                    1 for pos in self.positions.values()
                    if pos.get('conditionId') == _cid and pos.get('outcomeIndex') == _oidx
                )
                if existing_in_market > 0:
                    bet_size = bet_size * (0.5 ** existing_in_market)
                    print(f"📉 [HALVING] 동일 마켓 기존 포지션 {existing_in_market}개 → 베팅 ${bet_size:.2f} (반감기 적용)")

                vwap_price = self.client.simulate_market_buy_vwap(token_id, bet_size)

                # [Filter 6] VWAP 최소가격 체크 (VWAP < 0.05 → 시장 유동성 극히 낮음, shares 폭등 방지)
                if vwap_price is not None and vwap_price < 0.05:
                    print(f"🚫 [SKIP] VWAP 저유동성 거부 (vwap={vwap_price:.3f} < 0.05): {tx.get('title', '')[:40]}")
                    vwap_price = None  # PENDING 전환 방지

                if vwap_price is not None and vwap_price <= target_price:
                    print(f"\n⚡ [FAST EXECUTE] 🐋 {name} 픽, 즉시 매수!")
                    print(f"  고래매수가: ${whale_price:.3f} (규모: ${whale_size:.0f}) | VWAP: ${vwap_price:.3f} | 한도: ${target_price:.3f}")
                    self._execute_copy_trade(tx, name, score, vwap_price)
                else:
                    print(f"\n⏳ [PENDING] 🐋 {name} 픽 → 대기열 등록 (1분)")
                    if vwap_price:
                        print(f"  VWAP: ${vwap_price:.3f} > 한도: ${target_price:.3f}")
                    else:
                        print(f"  호가창 분석 실패 또는 잔량 부족")

                    self.pending_orders.append({
                        "tx": tx,
                        "whale_name": name,
                        "whale_addr": addr,
                        "score": score,
                        "whale_price": whale_price,
                        "target_price": target_price,
                        "bet_size": bet_size,
                        "expires_at": now + 60,
                    })

        except Exception as e:
            print(f"[WARN] {name} 고래 활동 조회 중 예외 발생: {e}")

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
        except Exception as e:
            print(f"[WARN] _get_gamma_price 실패 ({slug}): {e}")
        return None

    def _process_pending_orders(self):
        if not self.pending_orders:
            return

        now = int(time.time())
        active_orders = []
        active_whale_addrs = set(self.load_whales().keys())

        for order in self.pending_orders:
            # 고래가 비활성화된 경우 즉시 취소
            whale_addr = order.get('whale_addr', '')
            if whale_addr and whale_addr not in active_whale_addrs:
                print(f"🚫 [CANCELLED] {order['whale_name']} 비활성화 → 대기 주문 취소 (목표가 ${order['target_price']:.3f})")
                continue

            if now > order['expires_at']:
                print(f"⏰ [EXPIRED] {order['whale_name']} 픽 체결 실패 (시장가가 목표가 ${order['target_price']:.3f} 이내로 오지 않음)")
                continue
                
            tx = order['tx']
            token_id = tx.get('asset')
            bet_size = order['bet_size']
            
            # 큐에서도 호가창 긁어서 (VWAP) 바로 체결각 재기
            vwap_price = self.client.simulate_market_buy_vwap(token_id, bet_size)
            
            if vwap_price is not None and vwap_price < 0.05:
                print(f"🚫 [CANCELLED] PENDING VWAP 저유동성 ({vwap_price:.3f} < 0.05) → 주문 취소")
                continue

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
        
        # 포지션에 기록 (같은 마켓이라도 다른 고래 시그널이면 중복 진입 허용)
        cond_id = tx.get('conditionId') or ''
        base_tid = cond_id + str(tx.get('outcomeIndex', 0))
        tid = base_tid
        counter = 1
        while tid in self.positions:
            tid = f"{base_tid}_{counter}"
            counter += 1
            
        slug = tx.get('slug')
        
        # 로그 및 State 반영 (Taker fee 2% 반영)
        taker_fee = bet_size * 0.02
        self.bankroll -= (bet_size + taker_fee)
        self.stats['total_bets'] += 1
        
        self.positions[tid] = {
            'whale_name': whale_name,
            'title': tx.get('title'),
            'side': 'YES', # 여기서 outcome index에 따라 NO일수도 있지만 제목은 정해짐
            'outcome': tx.get('outcome'),
            'outcomeIndex': int(tx.get('outcomeIndex', 0)),
            'entry_price': executed_price,
            'size_usdc': bet_size,
            'shares': shares,
            'conditionId': tx.get('conditionId'),
            'marketId': tx.get('marketId'), # if exists
            'token_id': tx.get('asset'),    # 청산 시 bid 오더북 조회용
            'slug': slug,
            'timestamp': int(time.time()),
            'current_price': executed_price,
            'peak_price': executed_price,       # 트레일링 스탑용 고점 추적
        }
        
        whale_price = float(tx.get('price', 0))
        print(f"\n🚨 [COPY TRADE] 🐋 {whale_name} 픽 탑승!")
        print(f"  마켓: {tx.get('title')} ({tx.get('outcome')})")
        print(f"  상대가: ${whale_price:.3f} | 실제 체결가: ${executed_price:.3f}")
        print(f"  배팅금: ${bet_size:.2f} | 남은자본금: ${self.bankroll:.2f}")
        
        # 호환성 위해 Trade Log 기록 (strategy 이름으로 분리)
        self._log_trade(tid, "WHL", "YES", tx.get('title'), executed_price, bet_size, "OPEN", tx.get('marketId'))
        self._save_state()  # 포지션 진입 즉시 저장

    def _settle_positions(self):
        """진행 중인 포지션의 현재가 조회 및 Hybrid Exit 청산 판단"""
        to_remove = []
        for tid, pos in list(self.positions.items()):
            slug = pos['slug']
            cond_id = pos['conditionId']

            url = f"https://gamma-api.polymarket.com/events?slug={slug}"
            try:
                r = self.session.get(url, timeout=5)
                events = r.json()
                if not events:
                    # slug 없거나 이미 삭제된 이벤트 → 타임아웃 기반 청산 폴백
                    held_seconds = int(time.time()) - pos.get('timestamp', int(time.time()))
                    if held_seconds > 259200:
                        self._execute_early_exit(tid, pos, pos['entry_price'] * 0.5, "TIMEOUT")
                        to_remove.append(tid)
                    continue
                market_found = False
                for m in events[0].get('markets', []):
                    if m.get('conditionId') != cond_id:
                        continue
                    market_found = True

                    closed = m.get('closed', False)
                    winner = self.client.get_market_winner(m.get('id', ''))
                    self._log_settle_debug(pos, m, winner, closed)

                    # [우선순위 1] 마켓 자연 정산
                    if winner not in ['WAITING', None] or closed:
                        outcome = str(pos.get('outcome') or '')
                        outcome_up = outcome.upper()
                        is_yes = any(k in outcome_up for k in ('YES', 'UP', 'ABOVE', 'HIGH'))
                        won = (winner == 'YES' and is_yes) or (winner == 'NO' and not is_yes) or (winner == outcome)
                        if won:
                            self._settle_as_win(tid, pos)
                        else:
                            self._settle_as_loss(tid, pos)
                        to_remove.append(tid)
                        break

                    # 현재가 파싱
                    current_price = None
                    try:
                        prices = m.get('outcomePrices')
                        if isinstance(prices, str):
                            prices = json.loads(prices)
                        if isinstance(prices, list):
                            outcome_idx = pos.get('outcomeIndex', 0)
                            if len(prices) > outcome_idx:
                                current_price = float(prices[outcome_idx])
                                pos['current_price'] = current_price
                                # 고점 갱신 (트레일링 스탑용)
                                if current_price > pos.get('peak_price', pos['entry_price']):
                                    pos['peak_price'] = current_price
                    except Exception as e:
                        print(f"[WARN] 현재가 파싱 실패 ({pos.get('title', '')}): {e}")

                    if current_price is None:
                        # current_price 없어도 타임아웃은 실행 (죽은 포지션 강제 청산)
                        held_seconds = int(time.time()) - pos.get('timestamp', int(time.time()))
                        if held_seconds > 259200:
                            self._execute_early_exit(tid, pos, pos['entry_price'] * 0.5, "TIMEOUT")
                            to_remove.append(tid)
                        break

                    roi = (current_price - pos['entry_price']) / pos['entry_price']
                    peak_price = pos.get('peak_price', pos['entry_price'])
                    peak_roi = (peak_price - pos['entry_price']) / pos['entry_price']

                    # [우선순위 2] Take Profit +30%
                    if roi >= 0.30:
                        self._execute_early_exit(tid, pos, current_price, "TAKE_PROFIT")
                        to_remove.append(tid)
                        break

                    # [우선순위 3] Trailing Stop (고점 +10% 달성 후 고점 대비 -15% 하락)
                    if peak_roi >= 0.10 and (current_price - peak_price) / peak_price <= -0.15:
                        self._execute_early_exit(tid, pos, current_price, "TRAILING_STOP")
                        to_remove.append(tid)
                        break

                    # [우선순위 4] Stop Loss -20%
                    if roi <= -0.20:
                        self._execute_early_exit(tid, pos, current_price, "STOP_LOSS")
                        to_remove.append(tid)
                        break

                    # [우선순위 5] Timeout 3일 (259200초)
                    held_seconds = int(time.time()) - pos.get('timestamp', int(time.time()))
                    if held_seconds > 259200:
                        self._execute_early_exit(tid, pos, current_price, "TIMEOUT")
                        to_remove.append(tid)
                        break

                    break  # conditionId 매칭 마켓 처리 완료

                # conditionId 매칭 마켓이 이벤트에 없는 경우 → 타임아웃 폴백
                if not market_found:
                    held_seconds = int(time.time()) - pos.get('timestamp', int(time.time()))
                    if held_seconds > 259200:
                        self._execute_early_exit(tid, pos, pos['entry_price'] * 0.5, "TIMEOUT")
                        to_remove.append(tid)

            except Exception as e:
                print(f"[WARN] 포지션 정산 처리 실패 ({pos.get('title', tid)}): {e}")

        for tid in to_remove:
            self.positions.pop(tid, None)
        if to_remove:
            self._save_state()  # 청산 후 즉시 저장

    def _execute_early_exit(self, tid, pos, current_price, reason):
        """TP / SL / Trailing Stop / Timeout 조기 청산"""
        token_id = pos.get('token_id')
        payout = None

        # 실제 bid 오더북 기반 VWAP 청산 시뮬레이션
        if token_id:
            sell_result = self.client.simulate_market_sell_vwap(token_id, pos['shares'])
            if sell_result is not None:
                payout, effective_sell_price = sell_result
                print(f"  [SELL VWAP] bid오더북 기반 체결가: ${effective_sell_price:.4f} (보유 {pos['shares']:.1f}shares → ${payout:.2f})")

        # fallback: 오더북 조회 실패 시 고정 슬리피지 2%
        if payout is None:
            effective_sell_price = current_price * 0.98
            payout = pos['shares'] * effective_sell_price

        # Taker fee 2% 차감 (조기 청산은 시장가 매도)
        taker_fee = payout * 0.02
        payout -= taker_fee

        profit = payout - pos['size_usdc']
        roi_pct = profit / pos['size_usdc'] * 100

        self.bankroll += payout
        if profit >= 0:
            self.stats['wins'] += 1
        else:
            self.stats['losses'] += 1
        self.stats['total_pnl'] += profit

        emoji_map = {
            "TAKE_PROFIT":    "💰",
            "TRAILING_STOP":  "📉",
            "STOP_LOSS":      "🛑",
            "TIMEOUT":        "⏰",
            "MIRROR_EXIT":    "🔄",
        }
        emoji = emoji_map.get(reason, "🔔")
        print(f"\n{emoji} [{reason}] {pos['title']}")
        print(f"  체결가: ${effective_sell_price:.3f} | PnL: ${profit:+.2f} ({roi_pct:+.1f}%)")
        self._log_trade(tid, "WHL", pos['outcome'], pos['title'], effective_sell_price, payout, reason, pos.get('marketId', ''), pnl=profit)

    def _settle_as_win(self, tid, pos):
        payout = pos['shares'] * 1.0 # 1달러 
        profit = payout - pos['size_usdc']
        self.bankroll += payout
        self.stats['wins'] += 1
        self.stats['total_pnl'] += profit
        
        print(f"\n✅ [WIN] {pos['title']} 수익: +${profit:.2f}")
        self._log_trade(tid, "WHL", pos.get('outcome', ''), pos['title'], 1.0, payout, "WIN", pos.get('marketId', ''), pnl=profit)

    def _settle_as_loss(self, tid, pos):
        loss = -pos['size_usdc']
        self.stats['losses'] += 1
        self.stats['total_pnl'] += loss
        
        print(f"\n❌ [LOSS] {pos['title']} 손실: ${loss:.2f}")
        self._log_trade(tid, "WHL", pos.get('outcome', ''), pos['title'], 0.0, pos['size_usdc'], "LOSS", pos.get('marketId', ''), pnl=loss)

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

    def _log_settle_debug(self, pos, market_data, winner, closed):
        """정산 시도마다 winner/closed/raw 필드를 파일로 기록 (분석용)"""
        record = {
            "ts": datetime.now().isoformat()[:19],
            "title": (pos.get('title') or '')[:50],
            "winner": winner,
            "closed": closed,
            "market_id": market_data.get('id', ''),
            "conditionId": market_data.get('conditionId', ''),
            "outcomePrices": market_data.get('outcomePrices'),
            "winnerOutcome": market_data.get('winnerOutcome'),
            "resolved": market_data.get('resolved'),
            "pos_outcome": pos.get('outcome'),
        }
        path = os.path.join(os.path.dirname(__file__), "settle_debug.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _save_state(self):
        """positions, bankroll, stats, seen_txs를 JSON 파일로 영속화"""
        try:
            # seen_txs는 최근 2000개만 보존 (메모리 & 파일 크기 제한)
            recent_txs = list(self.seen_txs)[-2000:]
            state = {
                'positions': self.positions,
                'bankroll': self.bankroll,
                'peak_bankroll': self.peak_bankroll,
                'stats': self.stats,
                'seen_txs': recent_txs,
            }
            tmp_path = self.state_file_path + '.tmp'
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            # 원자적 교체 (Windows에서는 기존 파일 먼저 삭제 필요)
            if os.path.exists(self.state_file_path):
                os.remove(self.state_file_path)
            os.rename(tmp_path, self.state_file_path)
        except Exception as e:
            print(f"[WARN] 상태 저장 실패: {e}")

    def _load_state(self):
        """이전 세션의 상태를 state_WhaleCopy.json에서 복구"""
        if not os.path.exists(self.state_file_path):
            return
        try:
            with open(self.state_file_path, 'r', encoding='utf-8') as f:
                state = json.load(f)
            self.positions = state.get('positions', {})
            self.bankroll = state.get('bankroll', self.bankroll)
            self.peak_bankroll = state.get('peak_bankroll', self.peak_bankroll)
            self.stats = state.get('stats', self.stats)
            self.seen_txs = set(state.get('seen_txs', []))
            settled = self.stats['wins'] + self.stats['losses']
            print(f"[STATE] 이전 세션 복구 완료:")
            print(f"  포지션: {len(self.positions)}개 | 자본금: ${self.bankroll:.2f}")
            print(f"  통계: {settled}건 (W{self.stats['wins']}/L{self.stats['losses']}) | PnL: ${self.stats['total_pnl']:+.2f}")
            print(f"  seen_txs: {len(self.seen_txs)}건 복구")
        except Exception as e:
            print(f"[WARN] 상태 복구 실패 (초기값 사용): {e}")

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

        # 상태 영속화 (대시보드 업데이트마다 함께 저장)
        self._save_state()

if __name__ == '__main__':
    bot = WhaleCopyBot()
    bot.run_loop()
