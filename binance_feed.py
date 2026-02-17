"""
Binance 고빈도 변동성 엔진 (High-Frequency Volatility Engine)

핵심 원리:
- Binance Klines API에서 1분봉 캔들 데이터 수집
- Log-Return 기반 Rolling Volatility 계산
- Parkinson 추정기 (Hi-Lo 범위) 로 Fat-tail 보정
- 단기 Drift (모멘텀 방향) 계산하여 확률 모델에 공급

※ 일별 변동성이 아닌 '분 단위 실현 변동성'을 사용하는 것이 핵심.
  5분/15분 바이너리 옵션에서 일별 vol을 쓰면 확률이 왜곡됨.
"""

import math
import time
import threading
import requests
from collections import deque


class BinancePriceFeed:
    """
    Binance에서 실시간 가격 + 1분봉 캔들 데이터를 수집하고
    고빈도 실현 변동성/드리프트를 계산하는 엔진.
    """

    def __init__(self):
        self.symbols = {
            'BTC': 'BTCUSDT',
            'ETH': 'ETHUSDT',
            'SOL': 'SOLUSDT',
        }

        # 1분봉 캔들 히스토리 (close, high, low, open, volume)
        # 최대 120개 = 2시간치 데이터 보관
        self.candles = {coin: deque(maxlen=120) for coin in self.symbols}

        # 현재 스팟 가격 캐시
        self._spot_cache = {}
        self._spot_last_fetch = 0

        # 캔들 마지막 갱신 시각
        self._candle_last_fetch = {coin: 0 for coin in self.symbols}
        self.lock = threading.Lock()

    # ─── 스팟 가격 ────────────────────────────────────────────

    def fetch_spot_prices(self):
        """Binance에서 현재 스팟 가격 일괄 조회 (무료, 인증 불필요)"""
        now = time.time()
        if now - self._spot_last_fetch < 1:  # 1초 쿨다운
            return

        try:
            url = "https://api.binance.com/api/v3/ticker/price"
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                data = {p['symbol']: float(p['price']) for p in r.json()}
                for coin, symbol in self.symbols.items():
                    if symbol in data:
                        self._spot_cache[coin] = data[symbol]
                self._spot_last_fetch = now
        except Exception:
            pass  # 네트워크 오류 시 기존 캐시 유지

    def get_spot_price(self, coin: str) -> float:
        """특정 코인의 현재 스팟 가격 반환"""
        self.fetch_spot_prices()
        return self._spot_cache.get(coin, 0.0)

    def get_price_at_time(self, coin: str, target_ts: float) -> float:
        """특정 시점(과거)의 종가 반환 (Paper Trading 정산용)"""
        with self.lock:
            candles = list(self.candles.get(coin, []))
        
        if not candles: return 0.0
        
        # target_ts와 가장 가까운 캔들 찾기 (오차 2분 이내)
        best_price = 0.0
        min_diff = 120 # 2분 (120초)
        
        for c in candles:
            # c['close_time']은 캔들이 닫힌 시각
            diff = abs(c['close_time'] - target_ts)
            if diff < min_diff:
                min_diff = diff
                best_price = c['close']
        
        return best_price

    # ─── 1분봉 캔들 수집 ──────────────────────────────────────

    def fetch_candles(self, coin: str, limit: int = 60):
        """
        Binance Klines API에서 1분봉 캔들 데이터 수집.
        최소 60초 간격으로 호출 (API 부하 방지).
        """
        now = time.time()
        if now - self._candle_last_fetch.get(coin, 0) < 30:  # 30초 쿨다운
            return

        symbol = self.symbols.get(coin)
        if not symbol:
            return

        try:
            url = "https://api.binance.com/api/v3/klines"
            params = {
                'symbol': symbol,
                'interval': '1m',
                'limit': limit,
            }
            r = requests.get(url, params=params, timeout=10)
            if r.status_code == 200:
                raw = r.json()
                with self.lock:
                    # 기존 데이터 클리어하고 새로 채움
                    self.candles[coin].clear()
                    for k in raw:
                        self.candles[coin].append({
                            'open': float(k[1]),
                            'high': float(k[2]),
                            'low': float(k[3]),
                            'close': float(k[4]),
                            'volume': float(k[5]),
                            'close_time': k[6] / 1000.0,  # ms -> sec
                        })
                self._candle_last_fetch[coin] = now
        except Exception:
            pass

    def ensure_candles(self, coin: str, min_count: int = 10):
        """캔들 데이터가 최소 수량 이상 확보되도록 보장"""
        if len(self.candles.get(coin, [])) < min_count:
            self.fetch_candles(coin)

    # ─── 변동성 계산 ─────────────────────────────────────────

    def get_hf_volatility(self, coin: str, window: int = 30) -> float:
        """
        고빈도 실현 변동성 (High-Frequency Realized Volatility).

        방법: 최근 window개 1분봉의 log-return 표준편차를 구한 뒤
              연율화(annualize)한다.

        연율화 방식:
            σ_annual = σ_1min × √(525600)
            (525600 = 1년의 분 수 = 365.25 × 24 × 60)

        반환값: 연율화된 변동성 (예: 0.80 = 80%)
                데이터 부족 시 기본값 1.0 (100%) 반환
        """
        self.ensure_candles(coin, window)
        candle_list = list(self.candles.get(coin, []))

        if len(candle_list) < 3:
            return 1.0  # 데이터 없으면 보수적으로 높은 vol

        # 최근 window개만 사용
        recent = candle_list[-window:]

        # Log returns 계산
        log_returns = []
        for i in range(1, len(recent)):
            c_prev = recent[i - 1]['close']
            c_curr = recent[i]['close']
            if c_prev > 0 and c_curr > 0:
                log_returns.append(math.log(c_curr / c_prev))

        if len(log_returns) < 2:
            return 1.0

        # 표본 표준편차
        n = len(log_returns)
        mean_lr = sum(log_returns) / n
        variance = sum((lr - mean_lr) ** 2 for lr in log_returns) / (n - 1)
        sigma_1m = math.sqrt(variance)

        # 연율화: σ × √(분/년)
        # 1년 = 365.25일 × 24시간 × 60분 = 525,960분
        sigma_annual = sigma_1m * math.sqrt(525960)

        return sigma_annual

    def get_parkinson_vol(self, coin: str, window: int = 30) -> float:
        """
        Parkinson 변동성 추정기 (Hi-Lo Range-based).

        Fat-tail이 심한 크립토에서 close-to-close 변동성보다
        더 효율적인 추정치를 제공한다. Hi/Lo 범위를 사용하므로
        봉 내부의 가격 움직임도 포착함.

        공식: σ_P = √(1/(4n·ln2) × Σ(ln(H_i/L_i))²)

        반환값: 연율화된 Parkinson 변동성
        """
        self.ensure_candles(coin, window)
        candle_list = list(self.candles.get(coin, []))

        if len(candle_list) < 3:
            return 1.0

        recent = candle_list[-window:]

        hl_sq_sum = 0.0
        valid = 0
        for c in recent:
            h, lo = c['high'], c['low']
            if h > 0 and lo > 0 and h >= lo:
                hl_sq_sum += (math.log(h / lo)) ** 2
                valid += 1

        if valid < 2:
            return 1.0

        # Parkinson 공식
        sigma_1m_sq = hl_sq_sum / (4.0 * valid * math.log(2))
        sigma_1m = math.sqrt(sigma_1m_sq)

        # 연율화
        sigma_annual = sigma_1m * math.sqrt(525960)
        return sigma_annual

    def get_blended_volatility(self, coin: str, window: int = 30) -> float:
        """
        Log-return 변동성과 Parkinson 변동성의 블렌딩.

        Parkinson은 fat-tail을 더 잘 잡지만, 갭에 약하다.
        두 추정기를 50:50으로 블렌딩하여 안정적인 추정치를 만든다.
        """
        vol_lr = self.get_hf_volatility(coin, window)
        vol_pk = self.get_parkinson_vol(coin, window)
        return (vol_lr + vol_pk) / 2.0

    # ─── 드리프트 (추세 보정) ─────────────────────────────────

    def get_drift(self, coin: str, window: int = 10) -> float:
        """
        단기 드리프트 (μ) 계산.

        최근 window개 1분봉의 평균 log-return을 연율화한 값.
        양수면 상승 추세, 음수면 하락 추세.
        확률 모델의 drift term에 사용.

        반환값: 연율화된 드리프트
        """
        self.ensure_candles(coin, window)
        candle_list = list(self.candles.get(coin, []))

        if len(candle_list) < 3:
            return 0.0

        recent = candle_list[-window:]

        log_returns = []
        for i in range(1, len(recent)):
            c_prev = recent[i - 1]['close']
            c_curr = recent[i]['close']
            if c_prev > 0 and c_curr > 0:
                log_returns.append(math.log(c_curr / c_prev))

        if not log_returns:
            return 0.0

        mean_lr = sum(log_returns) / len(log_returns)
        # 연율화
        drift_annual = mean_lr * 525960
        return drift_annual

    # === 기술적 지표 (전문가 직관 보정용) ===

    def get_rsi(self, coin: str, period: int = 14) -> float:
        """RSI (Relative Strength Index) 계산"""
        # Assuming self.lock exists for thread safety, if not, remove or define it.
        # For this edit, I'll keep it as provided in the instruction.
        with self.lock:
            candles = list(self.candles.get(coin, []))
        if len(candles) < period + 1:
            return 50.0

        # Corrected: Access 'close' key from dictionary, not index
        closes = [c['close'] for c in candles]
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        
        # 최근 period개 delta 사용
        rev_deltas = deltas[-period:]
        gains = [d if d > 0 else 0 for d in rev_deltas]
        losses = [-d if d < 0 else 0 for d in rev_deltas]

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period

        if avg_loss == 0:
            return 100.0
        
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def get_ema(self, coin: str, period: int) -> float:
        """EMA (Exponential Moving Average) 계산"""
        # Assuming self.lock exists for thread safety, if not, remove or define it.
        # For this edit, I'll keep it as provided in the instruction.
        with self.lock:
            candles = list(self.candles.get(coin, []))
        if len(candles) < period:
            return self.get_spot_price(coin)

        # Corrected: Access 'close' key from dictionary, not index
        closes = [c['close'] for c in candles]
        
        # 단순화를 위해 데이터 끝부분만 사용하여 계산
        # 실제 EMA는 전체 히스토리가 필요하지만 단기 보정용이므로 최근 윈도우 사용
        alpha = 2 / (period + 1)
        ema = closes[-period] # 초기값
        for price in closes[-period+1:]:
            ema = price * alpha + ema * (1 - alpha)
        return ema

    def get_expert_signals(self, coin: str) -> dict:
        """
        Antigravity Pure Alpha 전략용 시장 신호
        """
        rsi = self.get_rsi(coin)
        ema10 = self.get_ema(coin, 10)
        ema20 = self.get_ema(coin, 20)
        spot = self.get_spot_price(coin)
        drift = self.get_drift(coin)

        # 1. 추세 판단 (EMA 배열 기반 직관 극대화)
        trend = 'neutral'
        strength = 0.0
        
        # EMA 이격도 (추세의 힘)
        ema_gap = (ema10 / ema20 - 1.0) * 100
        
        if ema10 > ema20:
            trend = 'bull'
            # 상승장: EMA 위에서 놀고 있고 드리프트도 양수면 풀베팅 각
            strength = min(1.0, (ema_gap * 2.0) + (0.3 if spot > ema10 else 0) + (0.2 if drift > 0 else 0))
        elif ema10 < ema20:
            trend = 'bear'
            # 하락장: EMA 아래에 있고 드리프트도 음수면 하락 베팅 각
            strength = min(1.0, (abs(ema_gap) * 2.0) + (0.3 if spot < ema10 else 0) + (0.2 if drift < 0 else 0))

        # 2. 상태 판단 (RSI 60-70 구간을 '진짜 추세'로 간주)
        state = 'normal'
        if rsi >= 80: state = 'overbought' # 극단적 과열
        elif rsi <= 20: state = 'oversold' # 극단적 침체
        elif 60 <= rsi < 80: state = 'strong_trend_up'
        elif 20 < rsi <= 40: state = 'strong_trend_down'

        return {
            'rsi': round(rsi, 1),
            'trend': trend,
            'strength': round(strength, 2), # 0.0 ~ 1.0
            'state': state,
            'ema_diff': round(ema_gap, 3)
        }

    def get_market_summary(self, coin: str) -> dict:
        """디버깅/로깅용 시장 요약 정보 반환"""
        spot = self.get_spot_price(coin)
        n_candles = len(self.candles.get(coin, []))
        vol = self.get_blended_volatility(coin) if n_candles >= 3 else None
        drift = self.get_drift(coin) if n_candles >= 3 else None

        return {
            'coin': coin,
            'spot': spot,
            'candles': n_candles,
            'vol_annual': vol,
            'drift_annual': drift,
        }
