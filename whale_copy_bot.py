import time
import json
import os
import asyncio
import aiohttp
import re
from datetime import datetime, timedelta, timezone, date as _date
from config import config
from client_wrapper import PolymarketClient
from whale_manager import run_manager
from whale_scorer import WhaleScorer

class WhaleCopyBot:
    def __init__(self):
        self.db_file = "whales.json"
        
        # 상태 기록 (이전에 본 트랜잭션 아이디를 저장해 중복 매매 방지)
        self.seen_txs = set()
        self.MAX_SEEN_TXS = 10000  # 메모리 누수 방지
        self.positions = {}
        self.MAX_POSITIONS = 10    # 동시 포지션 상한 (자본 집중)
        self.pending_orders = []   # 지정가 대기 큐
        self.cooldown_tids = {}    # 청산 후 재진입 방지: {tid: expire_timestamp}
        
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

        # === 페이퍼 트레이딩 현실화 파라미터 ===
        self.startup_time = int(time.time())      # 봇 시작 시각 (백로그 방지용)
        self.TRADE_FEE_RATE = 0.01                # 1% 거래 수수료 (spread 실비 근사)
        self.RESOLUTION_FEE_RATE = 0.02           # 2% 정산 수수료 (Polymarket 프로토콜)
        self.SELL_SLIPPAGE = 0.02                 # 2% 매도 슬리피지
        self.MAX_BET_SIZE = 200.0                 # 단회 최대 베팅 $200 (Kelly 복리 폭주 방지)

        # 파일 경로
        self.trade_log_path = os.path.join(os.path.dirname(__file__), "trade_history.jsonl")
        self.status_file_path = os.path.join(os.path.dirname(__file__), "status_WhaleCopy.json")
        
        self.async_session = None
        self.client = PolymarketClient()

        print("=== WHALE COPY BOT (PAPER MODE) ===")
        print(f"  초기 자본금: ${self.bankroll:.2f}")
        print(f"  매수 슬리피지: 동적({self.slippage_pct * 100:.0f}%~15%)")
        print(f"  거래 수수료: {self.TRADE_FEE_RATE * 100:.0f}% | 정산 수수료: {self.RESOLUTION_FEE_RATE * 100:.0f}%")
        print(f"  매도 슬리피지: {self.SELL_SLIPPAGE * 100:.0f}% | 최대 베팅: ${self.MAX_BET_SIZE:.0f}")
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
                        print(f"[{datetime.now().strftime('%H:%M:%S')}] Active whales not found. Check whales.json.")
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
                    
                # 폴링 간격 3초 (기존 5초에서 단축 → 감지 속도 향상)
                await asyncio.sleep(3)
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
        MANAGER_INTERVAL = 4 * 3600   # 4시간마다 리더보드 전체 스캔 (기존 24h → 4h 단축)
        SCORER_INTERVAL = 1 * 3600   # 1시간마다 스코어 및 카테고리 최신화
        
        last_manager_run = 0
        last_scorer_run = 0
        
        scorer = WhaleScorer()
        
        while True:
            now = time.time()
            
            # 1. 고래 매니저 실행 (신규 고래 발굴 및 부적격 고래 제거)
            if now - last_manager_run >= MANAGER_INTERVAL:
                try:
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] [Maintenance] Running Whale Manager (Discovery)...")
                    await asyncio.to_thread(run_manager)
                    last_manager_run = time.time()
                except Exception as e:
                    print(f"❌ [Maintenance] Manager Error: {e}")
            
            # 2. 고래 스코어러 실행 (카테고리 픽 분석 및 점수 갱신)
            if now - last_scorer_run >= SCORER_INTERVAL:
                try:
                    print(f"\n[{datetime.now().strftime('%H:%M:%S')}] [Maintenance] Running Whale Scorer (Tagging)...")
                    await asyncio.to_thread(scorer.run)
                    last_scorer_run = time.time()
                except Exception as e:
                    print(f"❌ [Maintenance] Scorer Error: {e}")
            
            # 메인 거래 루프에 영향을 주지 않으려 아주 가끔씩만 체크 (1분 간격)
            await asyncio.sleep(60)

    async def _check_whale_activity(self, addr, name, score):
        """특정 고래의 최근 트랜잭션 비동기 조회 및 카피"""
        url = f"https://data-api.polymarket.com/activity?user={addr}&limit=25"
        try:
            async with self.async_session.get(url, timeout=5) as r:
                if r.status != 200:
                    return
                activities = await r.json()
                
            for tx in activities:
                # 1. 고래의 매집 (BUY) 액션 모니터링
                if tx.get('type') == 'TRADE' and tx.get('side') == 'BUY':
                    tx_id = tx.get('transactionHash') or tx.get('id')  # [BUG FIX4] 'id' 필드 없음, transactionHash 사용
                    
                    if tx_id not in self.seen_txs:
                        timestamp_val = tx.get('timestamp')
                        if isinstance(timestamp_val, str):
                            api_time_str = timestamp_val.split('.')[0].replace('Z', '')
                            tx_time = int(datetime.strptime(api_time_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp())
                        else:
                            tx_time = int(timestamp_val) # Unix timestamp integer
                        
                        now = int(time.time())
                        
                        self.seen_txs.add(tx_id)
                        # 메모리 관리: seen_txs가 너무 커지면 절반 삭제
                        if len(self.seen_txs) > self.MAX_SEEN_TXS:
                            self.seen_txs = set(list(self.seen_txs)[self.MAX_SEEN_TXS // 2:])
                        
                        # [현실화] 봇 시작 이전 거래는 백로그 체결 방지 (실제 거래에서는 과거 거래 소급 불가)
                        if tx_time < self.startup_time:
                            age_min = (self.startup_time - tx_time) / 60
                            print(f"⏪ [BACKLOG SKIP] {name} 거래, 봇 시작 {age_min:.0f}분 전 발생 → 소급 불가")
                            continue

                        if (now - tx_time) <= 1800: # [FIX1] 300초(5분) → 1800초(30분)으로 확대
                            whale_price = float(tx.get('price', 0))
                            whale_size = float(tx.get('size', 0))
                            slug = tx.get('slug')

                            # [FIX2] 정산 직전 마켓 필터: price >= 0.95는 호가창이 비어 복사 불가
                            if whale_price >= 0.95:
                                print(f"🚫 [SKIP] {name} 픽, price={whale_price:.3f} >= 0.95 (정산 직전 마켓, 호가창 없음)")
                                continue

                            # ══════════════════════════════════════════════════════════
                            # [FIX6 - 핵심] 마켓 제목(title)에서 날짜 직접 파싱
                            # "Bitcoin Up or Down - February 27, 6:45PM-7:00PM ET" 형태에서
                            # "February 27" → date(2026, 2, 27) → 오늘(2026-02-28)보다 과거 → 스킵
                            # Gamma API closed 필드나 endDate 파싱 실패에도 완벽히 차단
                            # ══════════════════════════════════════════════════════════
                            title_str = tx.get('title', '')
                            if title_str:
                                _MONTHS = {
                                    'January': 1, 'February': 2, 'March': 3, 'April': 4,
                                    'May': 5, 'June': 6, 'July': 7, 'August': 8,
                                    'September': 9, 'October': 10, 'November': 11, 'December': 12
                                }
                                _m = re.search(
                                    r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})',
                                    title_str
                                )
                                if _m:
                                    _month = _MONTHS[_m.group(1)]
                                    _day = int(_m.group(2))
                                    _market_date = _date(_date.today().year, _month, _day)
                                    if _market_date < _date.today():
                                        print(f"🚫 [SKIP] {name} 픽, 어제 마켓 ({_market_date}): {title_str[:45]}")
                                        continue

                            target_market_url = f"https://gamma-api.polymarket.com/events?slug={slug}"
                            try:
                                async with self.async_session.get(target_market_url, timeout=3) as mr_res:
                                    if mr_res.status == 200:
                                        data = await mr_res.json()
                                        if data:
                                            ev_data = data[0]
                                            cond_id_to_check = tx.get('conditionId')

                                            # ══════════════════════════════════════════════
                                            # [핵심 픽스] 마켓 레벨 closed 필드 확인
                                            # 이벤트 endDate가 아닌, 개별 마켓의 closed/endDate를 확인해야 함
                                            # 반복 5분 마켓(BTC UP/DOWN)은 이벤트는 살아있어도
                                            # 개별 마켓 슬롯은 이미 closed=True일 수 있음 → 정산 아비트라지 방지
                                            # ══════════════════════════════════════════════
                                            market_closed = False
                                            market_end_passed = False
                                            matched_market = None

                                            for mkt in ev_data.get('markets', []):
                                                if mkt.get('conditionId') == cond_id_to_check:
                                                    matched_market = mkt
                                                    break

                                            if matched_market:
                                                # closed 필드 직접 확인 (가장 신뢰성 높음)
                                                if matched_market.get('closed', False):
                                                    market_closed = True

                                                # 마켓 레벨 endDate도 추가 확인
                                                mkt_end_str = matched_market.get('endDate')
                                                if mkt_end_str:
                                                    try:
                                                        if isinstance(mkt_end_str, str):
                                                            mkt_ed_dt = datetime.strptime(mkt_end_str.split('.')[0].replace('Z',''), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                                                            mkt_ed_ts = mkt_ed_dt.timestamp()
                                                        else:
                                                            mkt_ed_ts = float(mkt_end_str)
                                                        if mkt_ed_ts < now:
                                                            market_end_passed = True
                                                    except Exception:
                                                        pass
                                            else:
                                                # [FAIL CLOSED] conditionId가 현재 이벤트 markets 배열에 없음
                                                # = Recurring 마켓의 만료된 과거 슬롯
                                                # (오늘 이벤트는 오늘 conditionId만 포함 → 어제 conditionId는 찾을 수 없음)
                                                # 검증 불가이므로 무조건 스킵 (정산 아비트라지 완전 차단)
                                                print(f"🚫 [SKIP] {name} 픽, conditionId 불일치 - 만료된 Recurring 마켓 슬롯: {slug}")
                                                market_closed = True

                                            if market_closed:
                                                print(f"🚫 [SKIP] {name} 픽, 마켓 종료(closed=True): {slug}")
                                                continue

                                            if market_end_passed:
                                                print(f"🚫 [SKIP] {name} 픽, 마켓 만료(endDate 경과): {slug}")
                                                continue

                                            # 기회비용 필터: 365일 이상 장기 마켓 (이벤트 레벨 endDate 기준)
                                            ev_end_str = ev_data.get('endDate')
                                            if ev_end_str:
                                                try:
                                                    if isinstance(ev_end_str, str):
                                                        ev_ed_dt = datetime.strptime(ev_end_str.split('.')[0].replace('Z',''), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                                                        days_left = (ev_ed_dt.timestamp() - now) / 86400
                                                    else:
                                                        days_left = (float(ev_end_str) - now) / 86400

                                                    if days_left > 365 and score < 90:
                                                        print(f"🚫 [SKIP] {name} 픽, 기회비용 필터 발동 (종료까지 {days_left:.1f}일 남은 장기 마켓: {slug})")
                                                        continue
                                                    elif days_left > 365 and score >= 90:
                                                        print(f"💡 [PASS] {name} 픽, 장기 마켓이지만 고득점({score}) 고래이므로 통과: {slug}")
                                                except Exception:
                                                    pass

                                            # 전공 분야 필터 제거 (데이비드 요청)
                                            ev_tags = [tag.get('label','') for tag in ev_data.get('tags', []) if tag.get('label')]
                                            print(f"✅ [FILTER PASS] {name} 픽, 카테고리 필터 없이 통과 (마켓태그: {ev_tags})")
                            except Exception as e:
                                print(f"⚠️ [Error] 마켓 정보 조회 실패 (SKIP 방지 위해 통과): {e}")
                               
                            # 동시 포지션 상한 체크
                            if len(self.positions) >= self.MAX_POSITIONS:
                                print(f"🚫 [SKIP] 동시 포지션 상한({self.MAX_POSITIONS}개) 도달. 기존 포지션 정산 후 진입.")
                                continue
                            
                            token_id = tx.get('asset')  # [BUG FIX] token_id 변수 정의
                            if not token_id:
                                print(f"⚠️ [SKIP] {name} 픽, asset(token_id) 없음: {slug}")
                                continue
                            
                            if whale_size >= 5000:
                                slippage_modifier = 0.10  # 기존 0.07 → 0.10 
                            elif whale_size >= 1000:
                                slippage_modifier = 0.08  # 기존 0.05 → 0.08
                            elif whale_size >= 100:
                                slippage_modifier = 0.05  # 기존 0.03 → 0.05
                            else:
                                slippage_modifier = 0.03  # 기존 0.015 → 0.03
                            
                            if score >= 90:
                                slippage_modifier = max(slippage_modifier, 0.15) 
                                print(f"[VIP PASS] High score whale({name}, {score}pts) pick! Slippage 15% open")
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
                                # EV가 플러스인 꿀자리: 잔고의 최대 30%까지 투자 (기존 15%에서 확대)
                                bet_fraction = min(fractional_kelly, 0.30)
                                # [현실화] Kelly 복리 폭주 방지: 단회 베팅 최대 $200 캡
                                bet_size = min(self.bankroll * bet_fraction, self.MAX_BET_SIZE)
                                bet_type = "KELLY"
                                print(f"[KELLY] EV Positive! Kelly Fraction: {bet_fraction*100:.1f}% → Bet: ${bet_size:.1f}")
                            else:
                                # EV가 마이너스인 쓰레기 자리: 정찰병 확대 (잔고의 1.5% 또는 $50 중 작은 값)
                                bet_size = min(self.bankroll * 0.015, 50.0)
                                bet_type = "SCOUT"
                                print(f"[SCOUT] EV Negative (f={kelly_f:.2f}). Scout bet entry.")
                            
                            # 최소 베팅 금액 $10 이상 강제 (의미 없는 소액 방지)
                            bet_size = max(bet_size, 10.0)
                            
                            vwap_price = await asyncio.to_thread(self.client.simulate_market_buy_vwap, token_id, bet_size)

                            idx = tx.get('outcomeIndex', 0)
                            if vwap_price is not None and vwap_price <= target_price:
                                # 로그는 _execute_copy_trade 내부 중복 체크 통과 후 출력됨 (허위 EXECUTE 방지)
                                self._execute_copy_trade(tx, name, score, vwap_price, str(idx), bet_size, bet_type=bet_type)
                            elif vwap_price is None:
                                # [현실화 FIX - Fail Closed] 호가창 없을 때 Gamma 현재가 필수 확인
                                # gamma_now=None(마켓 조회 실패) 또는 고래가와 20% 이상 괴리 → 무조건 스킵
                                cond_id_check = tx.get('conditionId')
                                gamma_now = await self._get_gamma_price(slug, cond_id_check, idx)
                                if gamma_now is None:
                                    print(f"🚫 [SKIP] {name} 픽, Fallback 불가 - Gamma 조회 실패 (호가창+시장가 모두 없음, 마켓 종료 추정)")
                                    continue
                                if abs(gamma_now - whale_price) > 0.20:
                                    print(f"🚫 [SKIP] {name} 픽, Fallback 불가 - 시장 이미 이동 (고래가:{whale_price:.3f} vs 현재:{gamma_now:.3f})")
                                    continue
                                fallback_price = min(0.99, gamma_now * (1 + slippage_modifier))
                                print(f"\n🔄 [FALLBACK EXECUTE] 🐋 {name} 픽, VWAP None → Gamma 기준 fallback! (price={fallback_price:.3f}, {bet_type})")
                                self._execute_copy_trade(tx, name, score, fallback_price, str(idx), bet_size, bet_type=bet_type)
                            else:
                                # VWAP 존재하지만 목표가 초과 → 대기열 등록
                                print(f"\n⏳ [PENDING] 🐋 {name} 픽, VWAP={vwap_price:.3f} > target={target_price:.3f}, 대기열 등록 ({bet_type})")
                                self.pending_orders.append({
                                    "tx": tx,
                                    "whale_name": name,
                                    "score": score,
                                    "whale_price": whale_price,
                                    "slippage_modifier": slippage_modifier,
                                    "target_price": target_price,
                                    "bet_size": bet_size,
                                    "idx": str(idx),
                                    "expires_at": now + 600
                                })
                                
                # 2. 고래의 덤핑 (SELL) 액션 모니터링 (Mirror Exit)
                elif tx.get('type') == 'TRADE' and tx.get('side') == 'SELL':
                    tx_id = tx.get('transactionHash') or tx.get('id')  # [BUG FIX4] 'id' 필드 없음, transactionHash 사용
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
            elif vwap_price is None:
                # [현실화 FIX] Pending 큐 Fallback도 Gamma 현재가 괴리 확인 후 집행
                whale_p = order['whale_price']
                slip = order.get('slippage_modifier', 0.10)
                slug_check = tx.get('slug')
                cid_check = tx.get('conditionId')
                idx_check = int(order['idx'])
                gamma_now = await self._get_gamma_price(slug_check, cid_check, idx_check)
                if gamma_now is None:
                    print(f"🚫 [PENDING SKIP] {order['whale_name']} 픽, Gamma 조회 실패 (마켓 종료 추정)")
                    continue
                if abs(gamma_now - whale_p) > 0.20:
                    print(f"🚫 [PENDING SKIP] {order['whale_name']} 픽, 시장 이미 이동 (고래가:{whale_p:.3f} vs 현재:{gamma_now:.3f})")
                    continue
                fallback_price = min(0.99, gamma_now * (1 + slip))
                print(f"🔄 [PENDING Fallback] 🐋 {order['whale_name']} 픽, 호가창 계속 비어있음 → fallback 체결! (price={fallback_price:.3f})")
                self._execute_copy_trade(tx, order['whale_name'], order['score'], fallback_price, order['idx'], bet_size)
            else:
                active_orders.append(order)
                
        self.pending_orders = active_orders

    def _execute_copy_trade(self, tx, whale_name, score, executed_price, outcome_idx="0", computed_bet_size=None, bet_type="KELLY"):
        """가상 매매 집행"""
        bet_size = computed_bet_size if computed_bet_size else 10.0 # 에러 방지용 Fallback
        
        if bet_size < 1.0: 
            print(f"🚫 [SKIP] {whale_name} 픽, 스코어/잔고 부족 (산출금: ${bet_size:.2f})")
            return # 잔고 부족
            
        if executed_price <= 0:
            print(f"🚫 [SKIP] {whale_name} 픽, 체결가 0 이하 (price: {executed_price})")
            return
        
        # [현실화] 거래 수수료 차감 후 실제 체결 shares 계산
        trade_fee = bet_size * self.TRADE_FEE_RATE
        shares = (bet_size - trade_fee) / executed_price

        # 포지션에 기록
        cond_id = tx.get('conditionId')
        outcome_idx_val = tx.get('outcomeIndex', 0)
        if not cond_id:
            print(f"🚫 [SKIP] {whale_name} 픽, conditionId 없음")
            return
        tid = cond_id + str(outcome_idx_val) # Unique Key
        
        if tid in self.positions:
            # 중복 포지션은 조용히 무시 (로그 없음 - 반복 루프에서 스팸 방지)
            return

        # [현실화 Fix B] 최근 청산된 마켓 재진입 금지 (10분 쿨다운)
        cooldown_expire = self.cooldown_tids.get(tid, 0)
        if int(time.time()) < cooldown_expire:
            remaining = cooldown_expire - int(time.time())
            print(f"🚫 [COOLDOWN] {whale_name} 픽, 청산 후 쿨다운 중 (잔여 {remaining}초): {tx.get('title')}")
            return

        # 여기까지 통과 = 실제 신규 체결 확정 → 로그 출력
        print(f"\n⚡ [FAST EXECUTE] 🐋 {whale_name} 픽, 매수 체결! ({bet_type})")

        slug = tx.get('slug')
        
        # 로그 및 State 반영
        self.bankroll -= bet_size
        self.stats['total_bets'] += 1
        
        self.positions[tid] = {
            'whale_name': whale_name,
            'title': tx.get('title'),
            'side': tx.get('outcome', 'YES'),  # [현실화] 실제 outcome 기반 side 기록
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
        print(f"\n[COPY TRADE] {whale_name} pick entry!")
        print(f"  Market: {tx.get('title')} ({tx.get('outcome')})")
        print(f"  Whale: ${whale_price:.3f} | My Price: ${executed_price:.3f}")
        print(f"  Bet: ${bet_size:.2f} | Remaining: ${self.bankroll:.2f}")
        
        # 호환성 위해 Trade Log 기록 (strategy 이름으로 분리)
        self._log_trade(tid, "WHL", tx.get('outcome', 'YES'), tx.get('title'), executed_price, bet_size, "OPEN", tx.get('marketId'))

    async def _settle_positions(self):
        """진행 중인 포지션의 현재가 조회 및 정산 (정산 여부는 Gamma API 활용)"""
        to_remove = []
        for tid, pos in list(self.positions.items()):  # [BUG FIX5] dict 순회 중 크기 변경 방지용 list() 스냅샷
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
                                # 정산 - closed인데 winner가 아직 없으면 스킵 (정산 미출력 마켓)
                                if winner in ['WAITING', None]:
                                    # closed는 True이지만 아직 결과 미확정 → 다음 폴링에서 재확인
                                    continue
                                won = (winner == pos['outcome']) or (winner == 'YES' and str(pos.get('outcome','')).upper() == 'YES')
                                if won:
                                    self._settle_as_win(tid, pos)
                                else:
                                    self._settle_as_loss(tid, pos)
                                to_remove.append(tid)
                                continue
                                
                            # 아직 진행중이면 현재가 기반 청산 규칙(TP/SL/Trailing/Timeout) 검사
                            prices = m.get('outcomePrices')
                            try:
                                if isinstance(prices, str): prices = json.loads(prices)
                                if prices:
                                    # outcomeIndex 정확 매칭 (기존 단순화 → 정확한 인덱스)
                                    outcome_idx = int(pos.get('outcomeIndex', 0))
                                    if outcome_idx < len(prices):
                                        current_price = float(prices[outcome_idx])
                                    else:
                                        current_price = float(prices[0])
                                    
                                    shares = pos['shares']
                                    current_value = shares * current_price
                                    roi = (current_value - pos['size_usdc']) / pos['size_usdc'] * 100
                                    
                                    # 트레일링 스탑 로직: 고점 기록 및 추적
                                    if 'peak_price' not in pos:
                                        pos['peak_price'] = current_price
                                    if current_price > pos['peak_price']:
                                        pos['peak_price'] = current_price
                                    
                                    peak_roi = (shares * pos['peak_price'] - pos['size_usdc']) / pos['size_usdc'] * 100
                                    drawdown_from_peak = peak_roi - roi
                                    
                                    # 1. 30% 수익 달성 시 익절 (Hard TP, 기존 20% → 30%)
                                    if roi >= 30.0:
                                        self._execute_sell(tid, pos, current_price, "TAKE PROFIT")
                                        to_remove.append(tid)
                                        continue
                                    
                                    # 2. 트레일링 스탑: 고점 대비 15% 하락 시 청산 (수익 보호)
                                    if peak_roi >= 10.0 and drawdown_from_peak >= 15.0:
                                        self._execute_sell(tid, pos, current_price, "TRAILING STOP")
                                        to_remove.append(tid)
                                        continue
                                        
                                    # 3. -30% 손실 시 손절 (Hard SL)
                                    if roi <= -30.0:
                                        self._execute_sell(tid, pos, current_price, "STOP LOSS")
                                        to_remove.append(tid)
                                        continue
                                    
                                    # 4. 타임아웃 청산 (3일 초과, 기존 7일 → 3일로 단축)
                                    days_held = (int(time.time()) - pos['timestamp']) / 86400
                                    if days_held > 3.0:
                                        self._execute_sell(tid, pos, current_price, "TIMEOUT")
                                        to_remove.append(tid)
                                        continue

                            except: pass
            except:
                pass
                
        for tid in to_remove:
            self.positions.pop(tid, None)

    def _settle_as_win(self, tid, pos):
        # [현실화] Polymarket 프로토콜 2% 정산 수수료 차감 (실제 지급액 = shares * $0.98)
        payout = pos['shares'] * (1.0 - self.RESOLUTION_FEE_RATE)
        profit = payout - pos['size_usdc']
        self.bankroll += payout
        self.stats['wins'] += 1
        self.stats['total_pnl'] += profit

        print(f"\n✅ [WIN] {pos['title']} 수익: +${profit:.2f} (정산수수료 {self.RESOLUTION_FEE_RATE*100:.0f}% 적용)")
        self._log_trade(tid, "WHL", pos['outcome'], pos['title'], 1.0, payout, "WIN", pos['marketId'], pnl=profit)
        # [Fix B] 정산 완료 후 쿨다운 등록 (재진입 방지)
        self.cooldown_tids[tid] = int(time.time()) + 600

    def _settle_as_loss(self, tid, pos):
        loss = -pos['size_usdc']
        self.stats['losses'] += 1
        self.stats['total_pnl'] += loss

        print(f"\n❌ [LOSS] {pos['title']} 손실: ${loss:.2f}")
        self._log_trade(tid, "WHL", pos['outcome'], pos['title'], 0.0, pos['size_usdc'], "LOSS", pos['marketId'], pnl=loss)
        # [Fix B] 정산 완료 후 쿨다운 등록 (재진입 방지)
        self.cooldown_tids[tid] = int(time.time()) + 600

    def _execute_sell(self, tid, pos, sell_price, reason="TP/SL"):
        """보유 중인 포지션을 수동 매도(청산) 처리"""
        if tid not in self.positions:
            return
            
        shares = pos['shares']
        # [현실화] 매도 슬리피지 반영 (실제 호가창에서 매도 시 불리한 가격으로 체결)
        effective_sell_price = sell_price * (1 - self.SELL_SLIPPAGE)
        payout = shares * effective_sell_price
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
        elif reason == "TRAILING STOP":
            icon = "📉 [TRAILING STOP]"
            
        print(f"\n{icon} {pos['title']} 청산 완료!")
        print(f"  매수 평단: ${pos['entry_price']:.3f} -> 매도 평단: ${effective_sell_price:.3f} (슬리피지 {self.SELL_SLIPPAGE*100:.0f}% 적용)")
        print(f"  수익금: ${profit:+.2f} | 회수금: ${payout:.2f}")

        self._log_trade(tid, "WHL", pos['outcome'], pos['title'], effective_sell_price, payout, reason, pos['marketId'], pnl=profit)
        self.positions.pop(tid, None)
        # [Fix B] 청산 후 10분 재진입 금지 쿨다운 등록
        self.cooldown_tids[tid] = int(time.time()) + 600
        # 메모리 정리: 오래된 쿨다운 항목 제거
        now_ts = int(time.time())
        self.cooldown_tids = {k: v for k, v in self.cooldown_tids.items() if v > now_ts}


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
