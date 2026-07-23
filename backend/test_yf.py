import yfinance as yf
ticker = yf.Ticker("^BSESN")
df = ticker.history(period="1d", interval="5m")
df["ema9"] = df["Close"].ewm(span=9, adjust=False).mean()
df["ema21"] = df["Close"].ewm(span=21, adjust=False).mean()
print(df[["Close", "ema9", "ema21"]].tail(20))
