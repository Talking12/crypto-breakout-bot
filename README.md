# 🤖 Crypto Pre-Breakout AI Agent

A production-ready Python bot that scans top cryptocurrencies for high-probability pre-breakout setups and sends alerts to Discord.

---

## 📋 What This Bot Does

1. **Scans** top 50 USDT pairs on Binance every 15 minutes
2. **Scores** each coin 0-100 using 5 professional confluence layers:
   - Trend Structure (EMA alignment)
   - Volatility Contraction (Bollinger Band squeeze)
   - Volume Signature (declining volume + OBV accumulation)
   - Support/Resistance proximity
   - Momentum Reset (RSI sweet spot)
3. **Alerts** Discord only when score ≥ 80
4. **Calculates** entry, stop-loss, and 2R/3R targets automatically

---

## 🚀 Step-by-Step Deployment (Railway)

### Step 1: Create Accounts (5 minutes)
1. **GitHub**: [github.com](https://github.com) → Sign up
2. **Railway**: [railway.app](https://railway.app) → Sign up with GitHub
3. **Discord**: Create a private server → Server Settings → Integrations → Webhooks → Copy URL

### Step 2: Push Code to GitHub (5 minutes)
```bash
# On your computer
cd crypto-bot
git init
git add .
git commit -m "Initial bot"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/crypto-bot.git
git push -u origin main
```

### Step 3: Deploy to Railway (3 minutes)
1. Go to [railway.app/dashboard](https://railway.app/dashboard)
2. Click **New Project** → **Deploy from GitHub repo**
3. Select your `crypto-bot` repository
4. Railway auto-detects Python and installs dependencies

### Step 4: Add Environment Variables (2 minutes)
1. In Railway, click your project → **Variables** tab
2. Add these variables:

| Variable | Value | Description |
|----------|-------|-------------|
| `DISCORD_WEBHOOK` | `https://discord.com/api/webhooks/...` | Your Discord webhook URL |
| `TIMEFRAME` | `4h` | Candle timeframe |
| `SCAN_INTERVAL` | `900` | Seconds between scans (900 = 15 min) |
| `MIN_VOLUME_USD` | `2000000` | Minimum $2M daily volume |
| `SCORE_THRESHOLD` | `80` | Only alert if score ≥ 80 |
| `MAX_COINS` | `50` | Scan top 50 USDT pairs |

3. Click **Deploy** again

### Step 5: Verify It Works
1. Railway gives you a URL: `https://crypto-bot-production.up.railway.app`
2. Visit that URL → You should see JSON status
3. Visit `/scan-now` to trigger a manual scan
4. Check Discord for alerts!

---

## 💰 Trading Rules (Follow These Strictly)

With $100 capital:
- **Trade Spot only** (no futures)
- **Risk $3-4 per trade** (3-4% of account)
- **Position size**: $40-50 per coin
- **Stop loss**: Below support (usually 6-8%)
- **Take profit**: 2R and 3R (auto-calculated in Discord alert)
- **Max 2 positions** at a time
- **Only enter** when you see volume expansion on the breakout candle

---

## 🔧 Customization

### Change Timeframe
Edit `TIMEFRAME` variable:
- `1h` = Day trading
- `4h` = Swing trading (recommended)
- `1d` = Long-term

### Change Sensitivity
- Lower `SCORE_THRESHOLD` to `75` → More signals, lower quality
- Raise to `85` → Fewer signals, higher quality

### Add More Coins
Change `MAX_COINS` to `100` or `200` (slower scan, more opportunities)

---

## 📁 File Structure

```
crypto-bot/
├── main.py              # Bot logic + Flask server
├── requirements.txt     # Python packages
├── Procfile             # Railway start command
├── .env.example         # Local config template
├── .gitignore           # Ignore sensitive files
└── README.md            # This file
```

---

## 🛠️ Troubleshooting

| Problem | Fix |
|---------|-----|
| "No Discord webhook" in logs | Add `DISCORD_WEBHOOK` variable in Railway |
| No signals for hours | Normal. Good setups are rare. Lower threshold to 75 to test. |
| Railway says "Build failed" | Check `requirements.txt` has no typos |
| Scan too slow | Reduce `MAX_COINS` to 30 |
| Want live trading | Add Binance API keys (NOT recommended until 100 paper signals tested) |

---

## ⚠️ Risk Warning

- This bot finds **pre-breakout setups**, not guaranteed breakouts
- Always wait for **volume confirmation** on the breakout candle
- Paper trade for minimum 30 days before using real money
- Past signals do not guarantee future results
- Never risk more than you can afford to lose

---

Built with ❤️ for traders starting with small capital.
