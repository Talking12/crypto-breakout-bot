import os
import time
import threading
import requests
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from flask import Flask, jsonify

# ==================== CONFIG ====================
DISCORD_WEBHOOK = os.environ.get('DISCORD_WEBHOOK', '')
TIMEFRAME = os.environ.get('TIMEFRAME', '4h')
SCAN_INTERVAL = int(os.environ.get('SCAN_INTERVAL', '900'))
MIN_VOLUME_USD = int(os.environ.get('MIN_VOLUME_USD', '2000000'))
SCORE_THRESHOLD = int(os.environ.get('SCORE_THRESHOLD', '80'))
MAX_COINS = int(os.environ.get('MAX_COINS', '50'))
NEWS_API_KEY = os.environ.get('NEWS_API_KEY', '')  # Optional: CryptoPanic API

app = Flask(__name__)

bot_state = {
    "last_scan": "Initializing...",
    "signals_today": 0,
    "total_scans": 0,
    "status": "running",
    "mode": "ENHANCED",
    "usdt_d_trend": "checking...",
    "btc_trend": "checking..."
}

# Use KuCoin (works from US IPs)
exchange = ccxt.kucoin({'enableRateLimit': True})

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

# ==================== MARKET CONTEXT LAYER ====================
class MarketContext:
    """Checks USDT.D, BTC trend, and funding rates for market-wide confirmation"""

    def __init__(self):
        self.usdt_d_data = None
        self.btc_data = None
        self.funding_data = {}
        self.last_update = None

    def update(self):
        """Fetch all market context data"""
        try:
            # USDT.D (Tether Dominance) - KuCoin doesn't have this, use proxy
            # We approximate by checking USDT pairs strength
            self.usdt_d_data = self._get_usdt_d_proxy()

            # BTC trend
            self.btc_data = self._get_btc_trend()

            # Funding rates (proxy via price action)
            self.funding_data = self._get_market_sentiment()

            self.last_update = datetime.now()

            bot_state['usdt_d_trend'] = self.usdt_d_data['trend']
            bot_state['btc_trend'] = self.btc_data['trend']

        except Exception as e:
            print("[!] Market context update failed: {}".format(e))

    def _get_usdt_d_proxy(self):
        """
        USDT.D proxy: When USDT.D drops, money flows FROM stables INTO alts (bullish for alts)
        We detect this by checking if top alts are outperforming BTC
        """
        try:
            # Fetch BTC and ETH 4h data
            btc_ohlcv = exchange.fetch_ohlcv('BTC/USDT', TIMEFRAME, limit=20)
            eth_ohlcv = exchange.fetch_ohlcv('ETH/USDT', TIMEFRAME, limit=20)

            btc_df = pd.DataFrame(btc_ohlcv, columns=['timestamp','open','high','low','close','volume'])
            eth_df = pd.DataFrame(eth_ohlcv, columns=['timestamp','open','high','low','close','volume'])

            # Calculate 5-period performance
            btc_perf = (btc_df['close'].iloc[-1] - btc_df['close'].iloc[-5]) / btc_df['close'].iloc[-5]
            eth_perf = (eth_df['close'].iloc[-1] - eth_df['close'].iloc[-5]) / eth_df['close'].iloc[-5]

            # If ETH outperforming BTC, money likely flowing to alts (USDT.D dropping proxy)
            alt_strength = eth_perf - btc_perf

            if alt_strength > 0.02:
                return {"trend": "DROPPING", "score": 20, "reason": "USDT.D Proxy: Alts outperforming BTC (Money flowing IN)"}
            elif alt_strength > 0:
                return {"trend": "SLIGHT_DROP", "score": 15, "reason": "USDT.D Proxy: Mild alt strength"}
            elif alt_strength > -0.02:
                return {"trend": "NEUTRAL", "score": 10, "reason": "USDT.D Proxy: Neutral"}
            else:
                return {"trend": "RISING", "score": 0, "reason": "USDT.D Proxy: BTC outperforming (Money fleeing to stables)"}

        except Exception as e:
            return {"trend": "UNKNOWN", "score": 10, "reason": "USDT.D Proxy: Error - {}".format(e)}

    def _get_btc_trend(self):
        """Check if BTC is in uptrend, downtrend, or chop"""
        try:
            ohlcv = exchange.fetch_ohlcv('BTC/USDT', TIMEFRAME, limit=50)
            df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])

            df['ema9'] = ema(df['close'], 9)
            df['ema21'] = ema(df['close'], 21)
            df['ema55'] = ema(df['close'], 55)

            last = df.iloc[-1]

            # Strong uptrend
            if last['close'] > last['ema9'] > last['ema21'] > last['ema55']:
                return {"trend": "STRONG_UP", "score": 20, "reason": "BTC: Strong uptrend (Tailwind for alts)"}
            # Moderate uptrend
            elif last['close'] > last['ema21'] > last['ema55']:
                return {"trend": "UP", "score": 15, "reason": "BTC: Uptrend (Favorable)"}
            # Chop/Neutral
            elif last['close'] > last['ema55']:
                return {"trend": "NEUTRAL", "score": 10, "reason": "BTC: Neutral/Chop"}
            # Downtrend - AVOID
            else:
                return {"trend": "DOWN", "score": 0, "reason": "BTC: Downtrend (Headwind - AVOID)"}

        except Exception as e:
            return {"trend": "UNKNOWN", "score": 10, "reason": "BTC: Error - {}".format(e)}

    def _get_market_sentiment(self):
        """Check market sentiment via BTC funding rate proxy"""
        try:
            # We use BTC price action as proxy for funding
            # Sharp drops with volume = likely positive funding (longs getting liquidated)
            ohlcv = exchange.fetch_ohlcv('BTC/USDT', '1h', limit=24)
            df = pd.DataFrame(ohlcv, columns=['timestamp','open','high','low','close','volume'])

            # Check for recent liquidation cascade (sharp drop + volume spike)
            recent_drop = (df['close'].iloc[-1] - df['close'].iloc[-6]) / df['close'].iloc[-6]
            avg_vol = df['volume'].iloc[-12:].mean()
            recent_vol = df['volume'].iloc[-3:].mean()

            if recent_drop < -0.05 and recent_vol > avg_vol * 1.5:
                return {"sentiment": "CAPITULATION", "score": 15, "reason": "Market: Capitulation (Possible bottom)"}
            elif recent_drop < -0.03:
                return {"sentiment": "FEAR", "score": 5, "reason": "Market: Fear (Caution)"}
            elif recent_drop > 0.03:
                return {"sentiment": "GREED", "score": 10, "reason": "Market: Greed (FOMO risk)"}
            else:
                return {"sentiment": "NEUTRAL", "score": 10, "reason": "Market: Neutral sentiment"}

        except Exception as e:
            return {"sentiment": "UNKNOWN", "score": 10, "reason": "Sentiment: Error"}

    def get_context_score(self):
        """Combined market context score 0-60"""
        if not self.usdt_d_data or not self.btc_data:
            self.update()

        usdt_score = self.usdt_d_data.get('score', 10)
        btc_score = self.btc_data.get('score', 10)
        sentiment_score = self.funding_data.get('score', 10)

        total = usdt_score + btc_score + sentiment_score

        # If BTC is in downtrend, HALVE the total score (strong penalty)
        if self.btc_data.get('trend') == 'DOWN':
            total = total // 2

        return {
            'total': total,
            'max': 60,
            'usdt_d': self.usdt_d_data,
            'btc': self.btc_data,
            'sentiment': self.funding_data
        }

# ==================== NEWS LAYER ====================
class NewsChecker:
    """Checks for recent negative news about coins"""

    def __init__(self):
        self.cache = {}
        self.cache_time = 1800  # 30 minutes

    def check_news(self, symbol):
        """
        Free news check using CryptoPanic public API (no key needed for basic)
        Returns: {'score': 0-20, 'has_bad_news': bool, 'reason': str}
        """
        try:
            coin = symbol.replace('/USDT', '').lower()

            # Try CryptoPanic free API
            url = "https://cryptopanic.com/api/v1/posts/"
            params = {
                'auth_token': NEWS_API_KEY if NEWS_API_KEY else None,
                'currencies': coin.upper(),
                'kind': 'news',
                'public': 'true'  # Use public endpoint if no API key
            }

            # Remove None values
            params = {k: v for k, v in params.items() if v is not None}

            resp = requests.get(url, params=params, timeout=10)

            if resp.status_code != 200:
                # Fallback: assume neutral if API fails
                return {'score': 10, 'has_bad_news': False, 'reason': 'News: API unavailable (neutral)'}

            data = resp.json()
            results = data.get('results', [])

            if not results:
                return {'score': 10, 'has_bad_news': False, 'reason': 'News: No recent news (neutral)'}

            # Analyze sentiment of recent news
            bad_keywords = ['hack', 'exploit', 'lawsuit', 'sec', 'investigation', 
                           'delist', 'ban', 'shutdown', 'fraud', 'scam', 'crash',
                           'liquidation', 'dump', 'bearish', 'sell']

            good_keywords = ['partnership', 'launch', 'upgrade', 'bullish', 'adoption',
                            'list', 'integrate', 'growth', 'breakout', 'rally']

            bad_count = 0
            good_count = 0

            for post in results[:5]:  # Check last 5 news items
                title = post.get('title', '').lower()

                for bad in bad_keywords:
                    if bad in title:
                        bad_count += 1
                        break

                for good in good_keywords:
                    if good in title:
                        good_count += 1
                        break

            if bad_count > 0:
                return {
                    'score': 0, 
                    'has_bad_news': True, 
                    'reason': 'News: {} negative article(s) found (AVOID)'.format(bad_count)
                }
            elif good_count > 0:
                return {
                    'score': 15, 
                    'has_bad_news': False, 
                    'reason': 'News: {} positive article(s)'.format(good_count)
                }
            else:
                return {
                    'score': 10, 
                    'has_bad_news': False, 
                    'reason': 'News: Recent news but neutral sentiment'
                }

        except Exception as e:
            return {'score': 10, 'has_bad_news': False, 'reason': 'News: Check failed - neutral'}

# ==================== TECHNICAL SCORING (ORIGINAL) ====================
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
        if score == 20: reasons.append("Perfect Stage 2 Uptrend")
        elif score >= 15: reasons.append("Strong Uptrend")
        elif score >= 10: reasons.append("Moderate Uptrend")
        else: reasons.append("Weak Trend Structure")
        return score, reasons

    def score_volatility(self):
        last = self.df.iloc[-1]
        score = 0
        reasons = []
        if last['bb_width'] < 0.06:
            score += 15
            reasons.append("Tight BB Squeeze ({:.2%})".format(last['bb_width']))
        elif last['bb_width'] < 0.10:
            score += 10
            reasons.append("Consolidating (BB)")
        if last['atr_50'] > 0:
            atr_ratio = last['atr'] / last['atr_50']
            if atr_ratio < 0.35:
                score += 5
                reasons.append("ATR Compressed")
        return score, reasons

    def score_volume(self):
        df = self.df
        recent = df.tail(5)
        score = 0
        reasons = []
        vol_trend = np.polyfit(range(len(recent)), recent['volume'], 1)[0]
        if vol_trend < 0:
            score += 15
            reasons.append("Volume Declining (Supply Drying)")
        obv_slope = recent['obv'].iloc[-1] - recent['obv'].iloc[0]
        if obv_slope > 0:
            score += 5
            reasons.append("OBV Rising (Stealth Accumulation)")
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
            reasons.append("Near Resistance ({:.2%})".format(dist_res))
        if last['close'] > support * 1.02:
            score += 5
            reasons.append("Holding Support")
        return score, reasons, resistance, support

    def score_momentum(self):
        last = self.df.iloc[-1]
        score = 0
        reasons = []
        if 45 <= last['rsi'] <= 65:
            score += 15
            reasons.append("RSI {:.0f} (Sweet Spot)".format(last['rsi']))
        elif 35 <= last['rsi'] < 45:
            score += 10
            reasons.append("RSI {:.0f} (Resetting)".format(last['rsi']))
        vwap = (self.df['close'] * self.df['volume']).tail(20).sum() / self.df['volume'].tail(20).sum()
        if last['close'] > vwap:
            score += 5
            reasons.append("Above VWAP")
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

# ==================== ENHANCED DISCORD ALERTS ====================
def send_discord(signal, symbol, context, news):
    if not DISCORD_WEBHOOK:
        print("[!] No Discord webhook configured. Signal: {} {}/100".format(symbol, signal['score']))
        return

    # Calculate composite score
    tech_score = signal['score']
    context_score = context['total']
    news_score = news['score']

    # Weighted: Technical 50%, Context 30%, News 20%
    composite = int((tech_score * 0.5) + (context_score * 0.5) + (news_score * 0.2))

    # Color based on composite
    if composite >= 85: color = 0x00FF00  # Green
    elif composite >= 75: color = 0xFFA500  # Orange
    else: color = 0xFF0000  # Red

    entry = signal['price']
    stop = signal['support'] * 0.995
    risk = entry - stop if entry > stop else entry * 0.02
    tp1 = entry + (risk * 2)
    tp2 = entry + (risk * 3)

    # Build context fields
    context_fields = [
        {"name": "📊 Technical Score", "value": "{}/100".format(tech_score), "inline": True},
        {"name": "🌍 Market Context", "value": "{}/60".format(context_score), "inline": True},
        {"name": "📰 News Sentiment", "value": "{}/20".format(news_score), "inline": True},
    ]

    # Market context details
    market_details = []
    market_details.append(context['usdt_d']['reason'])
    market_details.append(context['btc']['reason'])
    market_details.append(context['sentiment']['reason'])
    market_details.append(news['reason'])

    fields = [
        {"name": "🎯 Composite Score", "value": "**{}/100** (Technical {} + Context {} + News {})".format(
            composite, tech_score, context_score, news_score), "inline": False},
        {"name": "📈 Confluence Breakdown", "value": "\n".join(signal['reasons']), "inline": False},
        {"name": "🌍 Market Context", "value": "\n".join(market_details), "inline": False},
        {"name": "💰 Entry / Stop", "value": "Entry: ${:.4f}\nStop: ${:.4f}\nRisk: {:.1f}%".format(entry, stop, ((entry-stop)/entry)*100), "inline": True},
        {"name": "🎯 Targets", "value": "TP1 (2R): ${:.4f}\nTP2 (3R): ${:.4f}".format(tp1, tp2), "inline": True},
        {"name": "📐 Metrics", "value": "BB Width: {:.2%}\nRSI: {:.1f}".format(signal['bb_width'], signal['rsi']), "inline": False}
    ]

    embed = {
        "title": "{} ENHANCED PRE-BREAKOUT: {}".format('🚀' if composite>=85 else '⚡' if composite>=75 else '⚠️', symbol),
        "description": "**Confidence: {}/100** | Price: ${:.4f}\n\n*This signal checks: Technicals + USDT.D + BTC Trend + News*".format(composite, entry),
        "color": color,
        "fields": fields,
        "footer": {"text": "Enhanced AI Agent v2.0 • {}".format(datetime.now().strftime('%Y-%m-%d %H:%M UTC'))}
    }

    try:
        resp = requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=15)
        if resp.status_code == 204:
            print("[OK] Enhanced Discord alert sent: {} (Composite: {})".format(symbol, composite))
        else:
            print("[!] Discord error {}: {}".format(resp.status_code, resp.text))
    except Exception as e:
        print("[!] Discord exception: {}".format(e))

# ==================== MAIN SCANNER ====================
market_context = MarketContext()
news_checker = NewsChecker()

def scan():
    global bot_state
    print("\n[{}] 🔍 ENHANCED SCAN STARTING...".format(datetime.now().strftime('%H:%M')))

    # Update market context first
    print("[*] Updating market context...")
    market_context.update()
    ctx = market_context.get_context_score()

    print("[*] USDT.D: {} | BTC: {} | Sentiment: {}".format(
        ctx['usdt_d']['trend'], ctx['btc']['trend'], ctx['sentiment']['sentiment']))

    # If market context is terrible, skip scanning entirely
    if ctx['total'] < 15:
        print("[!] MARKET CONTEXT TOO WEAK (Score: {}/60). Skipping scan to avoid false breakouts.".format(ctx['total']))
        bot_state['last_scan'] = datetime.now().isoformat()
        bot_state['total_scans'] += 1
        return

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
                avg_vol = df['volume'].tail(24).mean() * df['close'].iloc[-1]
                if avg_vol < MIN_VOLUME_USD:
                    continue

                # Technical analysis
                scorer = BreakoutScorer(df)
                result = scorer.analyze()

                # Skip if technical score is too low
                if result['score'] < SCORE_THRESHOLD:
                    continue

                # NEWS CHECK
                print("[*] Checking news for {}...".format(symbol))
                news = news_checker.check_news(symbol)

                if news['has_bad_news']:
                    print("[!] SKIPPING {} - Bad news detected".format(symbol))
                    continue

                # Calculate composite score
                composite = int((result['score'] * 0.5) + (ctx['total'] * 0.5) + (news['score'] * 0.2))

                # Only alert if composite is strong
                if composite >= 75:
                    found += 1
                    send_discord(result, symbol, ctx, news)
                    print("🚨 ENHANCED ALERT {}: Tech={} Context={} News={} COMPOSITE={}".format(
                        symbol, result['score'], ctx['total'], news['score'], composite))
                else:
                    print("[-] {} passed technicals ({}) but composite too low ({})".format(
                        symbol, result['score'], composite))

            except Exception as e:
                continue

        bot_state['signals_today'] += found
        bot_state['total_scans'] += 1
        bot_state['last_scan'] = datetime.now().isoformat()
        print("[OK] Enhanced scan complete. Checked {} coins. Found {} high-probability signals.".format(scan_count, found))

    except Exception as e:
        print("[!] Fatal scan error: {}".format(e))

def scanner_loop():
    while True:
        try:
            scan()
        except Exception as e:
            print("[!] Loop crash: {}".format(e))
        print("[*] Sleeping {} minutes...\n".format(SCAN_INTERVAL//60))
        time.sleep(SCAN_INTERVAL)

# ==================== FLASK ROUTES ====================
@app.route('/')
def health():
    return jsonify(bot_state)

@app.route('/scan-now')
def manual_scan():
    threading.Thread(target=scan, daemon=True).start()
    return jsonify({"status": "Manual enhanced scan triggered"})

@app.route('/market-context')
def get_context():
    ctx = market_context.get_context_score()
    return jsonify(ctx)

# ==================== MAIN ====================
if __name__ == '__main__':
    print("="*60)
    print("🤖 ENHANCED CRYPTO PRE-BREAKOUT AI AGENT v2.0")
    print("="*60)
    print("Mode: {}".format(bot_state['mode']))
    print("Features: Technicals + USDT.D Proxy + BTC Trend + News")
    print("Timeframe: {}".format(TIMEFRAME))
    print("Score Threshold: {}/100".format(SCORE_THRESHOLD))
    print("Scan Interval: {} minutes".format(SCAN_INTERVAL//60))
    print("Discord Webhook: {}".format('Configured' if DISCORD_WEBHOOK else 'NOT SET'))
    print("="*60)

    scanner_thread = threading.Thread(target=scanner_loop, daemon=True)
    scanner_thread.start()
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
