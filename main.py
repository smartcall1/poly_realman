"""
+EV 바이너리 옵션 봇 — 메인 진입점

루프:
1. Polymarket에서 활성 UPDOWN 마켓 탐색
2. EVStrategy.run_ev_step() 호출 (Fair Value → Edge → Kelly → 진입)
3. 만기 도달 포지션 자동 정산
4. 반복

핵심: 절대 조기 청산하지 않는다. 만기까지 보유. Hold-to-Maturity.
"""

import time
import json
from config import config


def main():
    print("=== POLYMARKET HATEBOT v3.0 ===")
    print("Core: Pure Alpha Sniper (YES/NO Mode)")
    print()
    print(f"  모드: {'📋 PAPER TRADING (가상)' if config.PAPER_TRADING else '💰 LIVE TRADING (실전)'}")
    print(f"  뱅크롤: ${config.INITIAL_BANKROLL:.2f}")
    print(f"  최소 엣지: {config.MIN_EDGE:.0%}")
    print(f"  켈리 비율: {config.KELLY_FRACTION:.0%}")
    print(f"  최대 베팅: 뱅크롤의 {config.MAX_BET_FRACTION:.0%}")
    print(f"  드로다운 한도: {config.DRAWDOWN_HALT_PCT:.0%}")
    print()

    from client_wrapper import PolymarketClient
    from ev_strategy import EVStrategy

    # 클라이언트 초기화
    try:
        client = PolymarketClient()
    except Exception as e:
        print(f"[경고] 클라이언트 초기화 중 오류: {e}")
        # 세션 초기화 버그 수정으로 인해 여기서 client = None 일 확률은 낮음
        # 하지만 방어적으로 대비
        if not vars().get('client'):
            try: client = PolymarketClient() 
            except: client = None
    
    if client is None:
        print("🚨 치명적 에러: API 클라이언트를 생성할 수 없습니다. 실행을 중단합니다.")
        return

    strategy = EVStrategy(client)

    print("  🚀 봇 시작! Binance 데이터 수집 중...\n")

    # 초기 캔들 데이터 로딩 (첫 루프 전 변동성 계산용)
    print(f"  ⏳ [{config.STRATEGY_NAME}] 초기 데이터 수집 중...", end="", flush=True)
    # XRP 제거
    for coin in ['BTC', 'ETH', 'SOL']:
        strategy.binance.fetch_candles(coin, limit=60)
        time.sleep(1.0)  # API 부하 방지 지연 확대
    print(" 완료!")

    try:
        active_tokens = []
        last_search = 0

        while True:
            try:
                now = time.time()

                # === 시장 탐색 ===
                if not active_tokens or (now - last_search) > config.MARKET_SCAN_INTERVAL:
                    if config.DEBUG_MODE:
                        print(f"\n  [Loop] Starting market search... (last search: {int(now - last_search)}s ago)")
                    
                    # [JITTER] 12개 봇이 동시에 쏘지 않도록 대폭 분산 (1~15초)
                    import random
                    jitter = random.uniform(1.0, 15.0)
                    if config.DEBUG_MODE:
                        print(f"  [Jitter] Spreading out... waiting {jitter:.2f}s for API slot...")
                    time.sleep(jitter) 
                    
                    markets = client.find_active_markets() if client else []
                    active_tokens = []
                    for m in markets:
                        tids = m.get('clobTokenIds', [])
                        if isinstance(tids, str):
                            tids = json.loads(tids)
                        if tids:
                            # YES 토큰 (보통 인덱스 0)
                            active_tokens.append({
                                'tid': tids[0],
                                'side': 'YES',
                                'question': m.get('question', '?'),
                                'slug': m.get('slug', ''),
                                'end_time': m.get('end_time', 0),
                                'marketId': m.get('marketId', ''),
                                'conditionId': m.get('conditionId', ''),
                            })
                            # NO 토큰 (보통 인덱스 1) - 하락 베팅용
                            if len(tids) > 1:
                                active_tokens.append({
                                    'tid': tids[1],
                                    'side': 'NO',
                                    'question': m.get('question', '?'),
                                    'slug': m.get('slug', ''),
                                    'end_time': m.get('end_time', 0),
                                    'marketId': m.get('marketId', ''),
                                    'conditionId': m.get('conditionId', ''),
                                })
                    last_search = now

                    if not active_tokens:
                        strategy.show_status("진행 중인 UPDOWN 마켓 없음 — 재탐색 대기 (30s)...")
                        time.sleep(30)
                        continue

                # === 각 마켓 데이터 수집 ===
                market_data = []
                for item in active_tokens:
                    order_book = client.get_order_book(item['tid']) if client else None
                    if order_book:
                        market_data.append({
                            'tid': item['tid'],
                            'side': item['side'],
                            'question': item['question'],
                            'order_book': order_book,
                            'end_time': item.get('end_time', 0),
                            'marketId': item.get('marketId', ''),
                            'conditionId': item.get('conditionId', ''),
                        })

                # === +EV 전략 실행 ===
                if market_data:
                    strategy.run_ev_step(market_data)
                else:
                    # 데이터 수집 실패 → 즉시 재탐색
                    active_tokens = []
                    last_search = 0
                    strategy.show_status("데이터 수집 실패 — 시장 재탐색 중...")

            except Exception as e:
                if config.DEBUG_MODE:
                    print(f"\n[Error] {e}")
                    import traceback
                    traceback.print_exc()

            time.sleep(config.MAIN_LOOP_INTERVAL)

    except KeyboardInterrupt:
        print("\n=== HATEBOT STOPPED (User Interrupted) ===")
    except Exception as e:
        print(f"\n=== HATEBOT CRASHED ===")
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 종료 시 통계 출력
        if 'strategy' in locals():
            print(f"  Bets: {strategy.stats['total_bets']} | W:{strategy.stats['wins']} / L:{strategy.stats['losses']}")
            print(f"  PnL: ${strategy.stats['total_pnl']:+.2f} | Bankroll: ${strategy.bankroll:.2f}")



if __name__ == "__main__":
    main()
