import json
import os
import time
from datetime import datetime
from collections import defaultdict

def clear_console():
    os.system('cls' if os.name == 'nt' else 'clear')

def format_currency(value, width=0):
    formatted = f"${value:,.2f}"
    padded = f"{formatted:>{width}}" if width > 0 else formatted
    color = "\033[92m" if value > 0 else "\033[91m" if value < 0 else ""
    reset = "\033[0m"
    return f"{color}{padded}{reset}"

def run_dashboard():
    # 스크립트 실행 위치 기준 절대 경로 설정
    base_dir = os.path.dirname(os.path.abspath(__file__))
    
    start_time = time.time()
    
    # [DEBUG] 디버깅용: 시작 시 경로와 파일 목록 한 번 확인
    print(f"Scanning directory: {base_dir}")
    try:
        files = [f for f in os.listdir(base_dir) if f.startswith('status_') and f.endswith('.json')]
        print(f"Found status files: {files}")
    except Exception as e:
        print(f"Error scanning directory: {e}")
    time.sleep(1)
    
    while True:
        stats = {}
        
        try:
            # 절대 경로 사용하여 파일 목록 획득
            files = [f for f in os.listdir(base_dir) if f.startswith('status_') and f.endswith('.json')]
            
            for filename in files:
                full_path = os.path.join(base_dir, filename)
                
                # 파일 읽기 시도 (Lock 경합 대비 재시도)
                data = None
                for attempt in range(5): # 5번까지 재시도
                    try:
                        with open(full_path, 'r', encoding='utf-8') as f:
                            content = f.read().strip()
                            if content: # 빈 파일 체크
                                data = json.loads(content)
                            break
                    except (json.JSONDecodeError, PermissionError, OSError):
                        time.sleep(0.1)
                    except Exception:
                        time.sleep(0.1)
                
                if data is None:
                    continue

                # 데이터 파싱
                try:
                    name = data.get('strategy', filename[7:-5])
                    stats[name] = {
                        'pnl': float(data.get('pnl', 0.0)),
                        'trades': int(data.get('trades', 0)),
                        'win_rate': float(data.get('win_rate', 0.0)),
                        'total_bet': float(data.get('total_bet', 0.0)),
                        'active': int(data.get('active_bets', 0)),
                        'last_action': data.get('last_action', '-'),
                        'online': True
                    }
                    
                    # 파일 수정 시간이 60초 이상 지났으면 Offline 처리 (넉넉하게 잡음)
                    mtime = os.path.getmtime(full_path)
                    
                    # [FIX] Shadow Bot([R])은 거래가 없어도 켜져있는 것으로 간주 (가상 시뮬레이션)
                    # 또는 타임아웃을 길게 설정 (5분)
                    if name.startswith('[R] '):
                        if time.time() - mtime > 300: # 5분 이상 업데이트 없으면 OFF (봇이 죽었을 수도 있음)
                            stats[name]['online'] = False
                    else:
                        if time.time() - mtime > 45:
                            stats[name]['online'] = False
                except Exception as parse_e:
                    # 데이터 형식이 깨진 경우 스킵
                    continue
                    
        except Exception as e:
            # 메인 루프 터지지 않게 에러 출력만 하고 계속 진행
            print(f"Dashboard Loop Error: {e}")
            time.sleep(1)
            continue

        # 시간 계산
        elapsed = int(time.time() - start_time)
        h, r = divmod(elapsed, 3600)
        m, s = divmod(r, 60)
        running_time = f"{h:02}:{m:02}:{s:02}"
        
        # [REQUESTED] 현재 시간
        # [REQUESTED] 현재 시간 (모바일용으로 시분초만 간편하게 표시)
        time_only_str = datetime.now().strftime("%H:%M:%S")
        
        clear_console()
        print("="*55)
        print(f"🚀 [WHALE BOT] DASH | 🕒 {time_only_str} | Run: {running_time}")
        print("="*55)
        print(f"{'BOT':<10}|{'PnL($)':>8}|{'Win%':>5}|{'Trd':>3}|{'Act':>3}|{'Exp$':>6}")
        print("-" * 55)
        
        if not stats:
            print(f"\n Waiting for bot data... (Scanning {base_dir})")
            time.sleep(2)
            continue

        # PnL 순 정렬
        sorted_stats = sorted(stats.items(), key=lambda x: x[1]['pnl'], reverse=True)
        
        total_global_pnl = 0
        total_active_bets = 0
        total_exposure = 0
        
        for name, s in sorted_stats:
            total_global_pnl += s['pnl']
            total_active_bets += s['active']
            total_exposure += s['total_bet']
            
            if not s['online']:
                if s['pnl'] > -100.0:
                    continue 

            status_prefix = "" if s['online'] else "[X]"
            name_str = f"{status_prefix}{name}"
            
            # 모바일 최적화를 위해 이름 길이 크게 제한
            if len(name_str) > 10:
                name_str = name_str[:8] + ".."
            
            pnl_str = format_currency(s['pnl'], 8) # 8글자 
            print(f"{name_str:<10}|{pnl_str}|{s['win_rate']:>4.0f}%|{s['trades']:>3}|{s['active']:>3}|{s['total_bet']:>6.0f}")
        
        print("-" * 55)
        print(f"{'TOTAL PROFIT':<10}|{format_currency(total_global_pnl, 8)}|Act:{total_active_bets}|Exp:{total_exposure:>.0f}")
        print("=" * 55)
        
        # --- Active Whales Count ---
        whales_path = os.path.join(base_dir, 'whales.json')
        active_whale_count = 0
        try:
            if os.path.exists(whales_path):
                with open(whales_path, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        whales_data = json.loads(content)
                        active_whale_count = sum(
                            1 for info in whales_data.values()
                            if info.get('status') == 'active'
                        )
        except Exception:
            pass

        print(f"\n[WHALES] Active: {active_whale_count}")

        # --- Recent Trade History ---
        # Column widths: TIME:5 | ACTION:11 | MARKET:28 | PNL:8 = 55 chars (matches top section)
        W = 55
        SEP = "-" * W
        history_path = os.path.join(base_dir, 'trade_history.jsonl')
        recent_trades = []
        try:
            if os.path.exists(history_path):
                with open(history_path, 'r', encoding='utf-8') as f:
                    lines = [l.strip() for l in f.readlines() if l.strip()]
                for line in lines[-7:]:  # 최근 7건
                    t = json.loads(line)
                    recent_trades.append(t)
        except Exception:
            pass

        print(SEP)
        print(f"{'TIME':<5}|{'ACTION':<11}|{'MARKET':<28}|{'PnL':>8}")
        print(SEP)

        action_map = {
            'OPEN':         'ENTRY',
            'TAKE PROFIT':  'PROFIT',
            'STOP LOSS':    'STOP LOSS',
            'MIRROR EXIT':  'MIRROR',
            'SETTLED WIN':  'WIN',
            'SETTLED LOSS': 'LOSS',
        }

        if recent_trades:
            for t in reversed(recent_trades):  # 최신순
                action = t.get('action', '?')
                ts_str = t.get('timestamp', '')
                try:
                    ts_dt = datetime.strptime(ts_str[:19], "%Y-%m-%dT%H:%M:%S")
                    time_str = ts_dt.strftime("%H:%M")
                except Exception:
                    time_str = '--:--'

                question = t.get('question', '')
                if len(question) > 28:
                    question = question[:26] + '..'

                pnl = t.get('pnl', 0.0)
                if action == 'OPEN':
                    pnl_str = '    open'
                else:
                    color = "\033[92m" if pnl > 0 else "\033[91m" if pnl < 0 else ""
                    reset = "\033[0m"
                    pnl_str = f"{color}{pnl:>+8.2f}{reset}"

                action_str = action_map.get(action, action)[:11]
                print(f"{time_str:<5}|{action_str:<11}|{question:<28}|{pnl_str}")
        else:
            print(f"  (no trades yet)".ljust(W))

        print(SEP)
        print(f"\n [Tip] Auto-refresh every 2s")
        
        time.sleep(2)

if __name__ == "__main__":
    if os.name == 'nt':
        os.system('')
    run_dashboard()
