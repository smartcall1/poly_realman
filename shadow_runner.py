import json
import time
import os
from datetime import datetime

class AntiStrategy:
    def __init__(self, name):
        self.name = f"[R] {name}"
        self.bankroll = 4000.0  # 초기 자본금 $4000 (원본과 동일하게 설정)
        self.positions = {}     # {tid: {entry_price, size, side, ...}}
        self.processed_tids = set() # [FIX] 중복 처리 방지용 Set
        self.stats = {
            'trades': 0,
            'wins': 0,
            'losses': 0,
            'pnl': 0.0,
            'total_bet': 0.0
        }
    
    def open_position(self, trade_data):
        tid = trade_data['tid']
        
        # [FIX] 이미 처리된 트레이드면 스킵 (중복 카운트 방지)
        if tid in self.processed_tids:
            return

        original_side = trade_data['side']
        anti_side = 'NO' if original_side == 'YES' else 'YES'
        
        original_price = trade_data['price']
        # [Simulate Slippage] 반대 포지션 진입 가격 (1 - p) + Spread
        # 현실성을 위해 2% 슬리피지/스프레드 적용 (0.4에 샀으면 반대는 0.62 정도에 사짐)
        anti_price = 1.0 - original_price + 0.02
        if anti_price >= 1.0: anti_price = 0.99
        
        size = trade_data['size_usdc']
        
        # 잔액 체크 (시뮬레이션이라도 파산은 파산)
        if size > self.bankroll:
            size = self.bankroll
        
        self.bankroll -= size
        self.stats['total_bet'] += size
        self.stats['trades'] += 1  # [FIX] 진입 시점에 트레이드 횟수 증가
        
        self.processed_tids.add(tid) # 처리된 ID 등록
        
        self.positions[tid] = {
            'side': anti_side,
            'entry_price': anti_price,
            'size': size,
            'shares': size / anti_price,
            'coin': trade_data['coin'],
            'marketId': trade_data.get('marketId', ''),
            'timestamp': datetime.fromisoformat(trade_data['timestamp']) # [FIX] 타임스탬프 저장
        }
        
        self.save_status()
        print(f"  [Shadow] 🌑 {self.name} Entered {anti_side} (Fade {original_side}) @ {anti_price:.2f}")

    def cleanup_stale_positions(self):
        """[FIX] 좀비 트레이드 청소 (1시간 지난 포지션 자동 삭제)"""
        now = datetime.now()
        tids_to_remove = []
        
        for tid, pos in self.positions.items():
            entry_time = pos.get('timestamp')
            if entry_time:
                # 1시간(3600초) 이상 지난 포지션은 강제 종료 (만료된 것으로 간주)
                if (now - entry_time).total_seconds() > 3600:
                    tids_to_remove.append(tid)
        
        for tid in tids_to_remove:
            del self.positions[tid]
            # print(f"  [Shadow] 🧹 {self.name} Cleaned up stale position {tid[:8]}...")

    def close_position(self, trade_data, result):
        tid = trade_data['tid']
        if tid not in self.positions:
            return
            
        pos = self.positions[tid]
        
        # Original WIN -> Anti LOSS
        if result == 'WIN':
            pnl = -pos['size']
            self.stats['losses'] += 1
            print(f"  [Shadow] ❌ {self.name} Processed LOSS (Original Won)")
            
        # Original LOSS -> Anti WIN
        elif result == 'LOSS':
            # Payout Logic with Fee (2%)
            # [FIX] 만기 보유 시 수수료 0% (Polymarket 수수료 없음)
            payout = pos['shares'] * 1.0
            fee = 0.0 
            net_payout = payout - fee
            
            pnl = net_payout - pos['size']
            self.bankroll += net_payout
            self.stats['wins'] += 1
            print(f"  [Shadow] ✅ {self.name} Processed WIN (Original Lost) +${pnl:.2f} (Fee: -${fee:.2f})")
            
        self.stats['pnl'] += pnl
        del self.positions[tid]
        
        self.save_status()

    def save_status(self):
        """대시보드 호환 상태 파일 저장"""
        # 저장하기 전에 좀비 청소 실행
        self.cleanup_stale_positions()
        
        try:
            filename = f"status_{self.name}.json"
            
            # 승률 계산: 승리 / (승리 + 패배)
            settled = self.stats['wins'] + self.stats['losses']
            win_rate = (self.stats['wins'] / settled * 100) if settled > 0 else 0.0
            
            # 활성 포지션 가치
            active_value = sum(p['size'] for p in self.positions.values())
            
            data = {
                "strategy": self.name,
                "timestamp": datetime.now().isoformat(),
                "pnl": round(self.stats['pnl'], 2),
                "equity": round(self.bankroll + active_value, 2),
                "balance": round(self.bankroll, 2),
                "roi": round(self.stats['pnl'] / 4000.0 * 100, 1), # [FIX] ROI 기준 $4000
                "win_rate": round(win_rate, 1),
                "trades": self.stats['trades'],
                "active_bets": len(self.positions),
                "total_bet": round(active_value, 2), # Exposure
                "last_action": datetime.now().isoformat()[:19]
            }

            
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
                
        except Exception as e:
            print(f"Error saving status for {self.name}: {e}")

def main():
    print("🥷 Shadow Fade Bot (Anti-Persona Simulator) Started...")
    print("   Monitoring trade_history.jsonl for new trades...")
    
    strategies = {} # Cache for AntiStrategy instances
    
    log_file = "trade_history.jsonl"
    
    # 1. 기존 파일 끝으로 이동 (재시작 시 중복 처리 방지 or 처음부터? 사용자가 '지금부터'라고 했으니 끝으로 이동)
    # 하지만 시뮬레이션 데이터가 좀 있어야 재밌으니까, 최근 데이터 읽어볼까? 
    # -> 아니야, 꼬일 수 있으니 실시간만 반영하자.
    
    if not os.path.exists(log_file):
        print("Waiting for trade_history.jsonl to be created...")
        while not os.path.exists(log_file):
            time.sleep(1)
            
    with open(log_file, "r", encoding="utf-8") as f:
        # 파일 처음부터 읽어서 히스토리 복원 (시뮬레이션)
        # f.seek(0, 2) -> f.seek(0, 0)
        
        while True:
            line = f.readline()
            if not line:
                time.sleep(0.1)
                continue
                
            try:
                data = json.loads(line)
                orig_name = data.get('strategy')
                action = data.get('action')
                
                if not orig_name: continue
                
                # Anti-Persona 인스턴스 가져오기 (없으면 생성)
                if orig_name not in strategies:
                    strategies[orig_name] = AntiStrategy(orig_name)
                
                anti_bot = strategies[orig_name]
                
                if action == 'OPEN':
                    anti_bot.open_position(data)
                elif action == 'WIN':
                    # Original WIN -> Anti LOSS
                    anti_bot.close_position(data, 'WIN')
                elif action == 'LOSS':
                    # Original LOSS -> Anti WIN
                    anti_bot.close_position(data, 'LOSS')

                    
            except json.JSONDecodeError:
                continue
            except Exception as e:
                print(f"Error processing line: {e}")

if __name__ == "__main__":
    main()
