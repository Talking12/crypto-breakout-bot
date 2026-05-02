import os
import time
import threading
import requests
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime
from flask import Flask, jsonify

# ==================== CONFIG FROM ENVIRONMENT ====================
DISCORD_WEBHOOK = os.environ.get('DISCORD_WEBHOOK', '')
TIMEFRAME = os.environ.get('TIMEFRAME', '4h')
SCAN_INTERVAL = int(os.environ.get('SCAN_INTERVAL', '900'))  # 15 minutes
MIN_VOLUME_USD = int(os.environ.get('MIN_VOLUME_USD', '2000000'))
SCORE_THRESHOLD = int(os.environ.get('SCORE_THRESHOLD', '80'))
MAX_COINS = int(os.environ.get('MAX_COINS', '50'))

app = Flask(__name__)

# Global state for health checks
bot_state = {
    "last_scan": "Initializing...",
    "signals_today": 0,
    "total_scans": 0,
    "status": "running",
    "mode": "PAPER"
}

# Binance spot (no API key needed for public data)
 exchange = ccxt.bybit({'enableRateLimit': True})

# ==================== TECHNICAL INDICATORS ====================
def ema(series, period):
    return series.ewm(span=period, adjust=False).mean()

def rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def bbands(df, period=20, std_dev=2):
    mid = df['close'].rolling(window=period).mean()
    std = df['close'].rolling(window=period).std()
    upper = mid + (std * std_dev)
    lower = mid - (std * std_dev)
    width = (upper - lower) / mid
    return upper, lower, mid, width

def atr(df, period=14):
    hl = df['high'] - df['low']
    hc = abs(df['high'] - df['close'].shift())
    lc = abs(df['low'] - df['close'].shift())
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def obv_calc(df):
    obv_vals = [0]
    for i in range(1, len(df)):
        if df['close'].iloc[i] > df['close'].iloc[i-1]:
            obv_vals.append(obv_vals[-1] + df['volume'].iloc[i])
        elif df['close'].iloc[i] < df['close'].iloc[i-1]:
            obv_vals.append(obv_vals[-1] - df['volume'].iloc[i])
        else:
            obv_vals.append(obv_vals[-1])
    return pd.Series(obv_vals, index=df.index)

# ==================== SCORING ENGINE ====================
class BreakoutScorer:
    def __init__(self, df):
        self.df = df.copy()
        self._calculate()

    def _calculate(self):
        df = self.df
        df['ema9'] = ema(df['close'], 9)
        df['ema21'] = ema(df['close'], 21)
        df['ema55'] = ema(df['close'], 55)
        df['ema200'] = ema(df['close'], 200)

        df['bb_upper'], df['bb_lower'], df['bb_mid'], df['bb_width'] = bbands(df)
        df['atr'] = atr(df)
        df['atr_50'] = df['atr'].rolling(50).mean()
        df['rsi'] = rsi(df['close'])
        df['vol_sma20'] = df['volume'].rolling(20).mean()
        df['obv'] = obv_calc(df)

        self.df = df

    def score_trend(self):
        last = self.df.iloc[-1]
        score = 0
        reasons = []

        if last['close'] > last['ema9']: score += 5
        if last['ema9'] > last['ema21']: score += 5
        if last['ema21'] > last['ema55']: score += 5
        if last['ema55'] > last['ema200']: score += 5

        if score == 20: reasons.append("✅ Perfect Stage 2 Uptrend")
        elif score >= 15: reasons.append("📈 Strong Uptrend")
        elif score >= 10: reasons.append("📊 Moderate Uptrend")
        else: reasons.append("⚠️ Weak Trend Structure")

        return score, reasons

    def score_volatility(self):
        last = self.df.iloc[-1]
        score = 0
        reasons = []

        if last['bb_width'] < 0.06:
            score += 15
            reasons.append(f"🎯 Tight BB Squeeze ({last['bb_width']:.2%})")
        elif last['bb_width'] < 0.10:
            score += 10
            reasons.append("📦 Consolidating (BB)")

        if last['atr_50'] > 0:
            atr_ratio = last['atr'] / last['atr_50']
            if atr_ratio < 0.35:
                score += 5
                reasons.append("📉 ATR Compressed")

        return score, reasons

    def score_volume(self):
        df = self.df
        recent = df.tail(5)
        score = 0
        reasons = []

        vol_trend = np.polyfit(range(len(recent)), recent['volume'], 1)[0]
        if vol_trend < 0:
            score += 15
            reasons.append("🔇 Volume Declining (Supply Drying)")

        obv_slope = recent['obv'].iloc[-1] - recent['obv'].iloc[0]
        if obv_slope > 0:
            score += 5
            reasons.append("🐋 OBV Rising (Stealth Accumulation)")

        return score, reasons

    def score_sr(self):
        df = self.df
        last = df.iloc[-1]
        score = 0
        reasons = []

        resistance = df['high'].tail(20).max()
        support = df['low'].tail(10).min()

        dist_res = (resistance - last['close']) / resistance
        if 0 <= dist_res <= 0.03:
            score += 15
            reasons.append(f"🧱 Near Resistance ({dist_res:.2%})")

        if last['close'] > support * 1.02:
            score += 5
            reasons.append("🛡️ Holding Support")

        return score, reasons, resistance, support

    def score_momentum(self):
        last = self.df.iloc[-1]
        score = 0
        reasons = []

        if 45 <= last['rsi'] <= 65:
            score += 15
            reasons.append(f"⚡ RSI {last['rsi']:.0f} (Sweet Spot)")
        elif 35 <= last['rsi'] < 45:
            score += 10
            reasons.append(f"🔄 RSI {last['rsi']:.0f} (Resetting)")

        vwap = (self.df['close'] * self.df['volume']).tail(20).sum() / self.df['volume'].tail(20).sum()
        if last['close'] > vwap:
            score += 5
            reasons.append("📍 Above VWAP")

        return score, reasons

    def analyze(self):
        s1, r1 = self.score_trend()
        s2, r2 = self.score_volatility()
        s3, r3 = self.score_volume()
        s4, r4, res, sup = self.score_sr()
        s5, r5 = self.score_momentum()

        total = s1 + s2 + s3 + s4 + s5
        all_reasons = r1 + r2 + r3 + r4 + r5

        return {
            'score': total,
            'reasons': all_reasons,
            'resistance': res,
            'support': sup,
            'bb_width': self.df['bb_width'].iloc[-1],
            'rsi': self.df['rsi'].iloc[-1],
            'price': self.df['close'].iloc[-1]
        }

# ==================== DISCORD ALERTS ====================
def send_discord(signal, symbol):
    if not DISCORD_WEBHOOK:
        print(f"[!] No Discord webhook configured. Signal: {symbol} {signal['score']}/100")
        return

    color = 0x00FF00 if signal['score'] >= 85 else 0xFFA500

    entry = signal['price']
    stop = signal['support'] * 0.995
    risk = entry - stop if entry > stop else entry * 0.02
    tp1 = entry + (risk * 2)
    tp2 = entry + (risk * 3)

    fields = [
        {"name": "📊 Confluence Breakdown", "value": "\n".join(signal['reasons']), "inline": False},
        {"name": "💰 Entry / Stop", "value": f"Entry: `${entry:.4f}`\nStop: `${stop:.4f}`\nRisk: {((entry-stop)/entry)*100:.1f}%", "inline": True},
        {"name": "🎯 Targets", "value": f"TP1 (2R): `${tp1:.4f}`\nTP2 (3R): `${tp2:.4f}`", "inline": True},
        {"name": "📐 Metrics", "value": f"BB Width: {signal['bb_width']:.2%}\nRSI: {signal['rsi']:.1f}", "inline": False}
    ]

    embed = {
        "title": f"{'🚀' if signal['score']>=85 else '⚡'} PRE-BREAKOUT: {symbol}",
        "description": f"**Confidence Score: {signal['score']}/100**\nPrice: `${entry:.4f}`",
        "color": color,
        "fields": fields,
        "footer": {"text": f"AI Agent v1.0 • {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}"}
    }

    try:
        resp = requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=15)
        if resp.status_code == 204:
            print(f"[✓] Discord alert sent: {symbol}")
        else:
            print(f"[!] Discord error {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"[!] Discord exception: {e}")

# ==================== SCANNER ====================
def scan():
    global bot_state
    print(f"\n[{datetime.now().strftime('%H:%M')}] 🔍 Scanning market...")

    try:
        markets = exchange.load_markets()
        usdt_pairs = [s for s in markets if s.endswith('/USDT') and markets[s]['active']]

        found = 0
        scan_count = 0

        for symbol in usdt_pairs:
            scan_count += 1
            if scan_count > MAX_COINS:
                break

            try:
                ohlcv = exchange.fetch_ohlcv(symbol, TIMEFRAME, limit=100)
                if len(ohlcv) < 55:
                    continue

                df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])

                # Volume filter ($2M+ daily approx)
                avg_vol = df['volume'].tail(24).mean() * df['close'].iloc[-1]
                if avg_vol < MIN_VOLUME_USD:
                    continue

                scorer = BreakoutScorer(df)
                result = scorer.analyze()

                if result['score'] >= SCORE_THRESHOLD:
                    found += 1
                    send_discord(result, symbol)
                    print(f"🚨 {symbol}: {result['score']}/100")

            except Exception as e:
                continue

        bot_state['signals_today'] += found
        bot_state['total_scans'] += 1
        bot_state['last_scan'] = datetime.now().isoformat()
        print(f"[✓] Scan complete. Checked {scan_count} coins. Found {found} signals.")

    except Exception as e:
        print(f"[!] Fatal scan error: {e}")

def scanner_loop():
    while True:
        try:
            scan()
        except Exception as e:
            print(f"[!] Loop crash: {e}")

        print(f"[*] Sleeping {SCAN_INTERVAL//60} minutes...\n")
        time.sleep(SCAN_INTERVAL)

# ==================== FLASK ROUTES (HEALTH CHECK) ====================
@app.route('/')
def health():
    return jsonify(bot_state)

@app.route('/scan-now')
def manual_scan():
    threading.Thread(target=scan, daemon=True).start()
    return jsonify({"status": "Manual scan triggered"})

# ==================== MAIN ====================
if __name__ == '__main__':
    print("="*60)
    print("🤖 CRYPTO PRE-BREAKOUT AI AGENT")
    print("="*60)
    print(f"Mode: {bot_state['mode']}")
    print(f"Timeframe: {TIMEFRAME}")
    print(f"Score Threshold: {SCORE_THRESHOLD}/100")
    print(f"Scan Interval: {SCAN_INTERVAL//60} minutes")
    print(f"Discord Webhook: {'✓ Configured' if DISCORD_WEBHOOK else '✗ NOT SET'}")
    print("="*60)

    # Start scanner in background thread
    scanner_thread = threading.Thread(target=scanner_loop, daemon=True)
    scanner_thread.start()

    # Start Flask web server (Railway provides PORT env var)
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
