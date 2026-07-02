#!/usr/bin/env python3
"""
US Stock Daily Technical Screener
==================================
S&P 500 + Nasdaq 100 (約520銘柄) を日足ベースでスクリーニングし、
テクニカル指標の複合スコアで購入候補をランキングする。

- データ取得: yfinance (APIキー不要)
- 銘柄リスト: Wikipedia (S&P 500 / Nasdaq-100 構成銘柄)
- 出力: reports/YYYY-MM-DD.md + reports/latest.md

免責: 本ツールは情報提供のみを目的とし、投資助言ではありません。
"""

import argparse
import sys
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd

# ============================================================
# 設定
# ============================================================
TOP_N = 15                      # レポートに載せる上位銘柄数
MIN_PRICE = 5.0                 # 最低株価 (USD)
MIN_DOLLAR_VOL = 20_000_000     # 最低平均売買代金 (20日平均, USD)
RSI_OVERBOUGHT_EXCLUDE = 78     # これを超える銘柄は過熱として除外
HISTORY_PERIOD = "15mo"         # 取得期間 (SMA200計算に1年+バッファ)
REPORTS_DIR = Path(__file__).parent / "reports"

WIKI_SP500 = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
WIKI_NDX = "https://en.wikipedia.org/wiki/Nasdaq-100"

UA = {"User-Agent": "Mozilla/5.0 (stock-screener; personal research use)"}


# ============================================================
# 銘柄ユニバース取得
# ============================================================
def fetch_universe() -> pd.DataFrame:
    """S&P 500 + Nasdaq 100 の構成銘柄を Wikipedia から取得して統合する。

    Returns: DataFrame [symbol, name, sector, index_membership]
    """
    import requests

    frames = []

    # --- S&P 500 ---
    try:
        html = requests.get(WIKI_SP500, headers=UA, timeout=30).text
        tables = pd.read_html(StringIO(html))
        sp = tables[0]
        sp = sp.rename(columns={"Symbol": "symbol", "Security": "name",
                                "GICS Sector": "sector"})
        sp = sp[["symbol", "name", "sector"]].copy()
        sp["idx"] = "SP500"
        frames.append(sp)
        print(f"[universe] S&P 500: {len(sp)} 銘柄")
    except Exception as e:
        print(f"[universe] S&P 500 取得失敗: {e}", file=sys.stderr)

    # --- Nasdaq 100 ---
    try:
        html = requests.get(WIKI_NDX, headers=UA, timeout=30).text
        tables = pd.read_html(StringIO(html))
        ndx = None
        for t in tables:
            cols = [str(c) for c in t.columns]
            if any("Ticker" in c or "Symbol" in c for c in cols) and \
               any("Company" in c for c in cols):
                ndx = t
                break
        if ndx is not None:
            colmap = {}
            for c in ndx.columns:
                cs = str(c)
                if "Ticker" in cs or "Symbol" in cs:
                    colmap[c] = "symbol"
                elif "Company" in cs:
                    colmap[c] = "name"
                elif "Sector" in cs:
                    colmap[c] = "sector"
            ndx = ndx.rename(columns=colmap)
            if "sector" not in ndx.columns:
                ndx["sector"] = ""
            ndx = ndx[["symbol", "name", "sector"]].copy()
            ndx["idx"] = "NDX"
            frames.append(ndx)
            print(f"[universe] Nasdaq 100: {len(ndx)} 銘柄")
    except Exception as e:
        print(f"[universe] Nasdaq 100 取得失敗: {e}", file=sys.stderr)

    if not frames:
        raise RuntimeError("銘柄ユニバースを取得できませんでした")

    uni = pd.concat(frames, ignore_index=True)
    uni["symbol"] = uni["symbol"].astype(str).str.strip()
    # yfinance形式に変換 (BRK.B -> BRK-B)
    uni["yf_symbol"] = uni["symbol"].str.replace(".", "-", regex=False)
    # 重複統合 (両指数所属は SP500+NDX 表記)
    uni = (uni.groupby("yf_symbol", as_index=False)
              .agg(symbol=("symbol", "first"),
                   name=("name", "first"),
                   sector=("sector", "first"),
                   idx=("idx", lambda s: "+".join(sorted(set(s))))))
    print(f"[universe] 統合後: {len(uni)} 銘柄")
    return uni


# ============================================================
# 株価データ取得
# ============================================================
def fetch_prices(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """yfinanceで日足OHLCVを一括取得し、銘柄ごとのDataFrame辞書を返す。"""
    import yfinance as yf

    print(f"[prices] {len(symbols)} 銘柄をダウンロード中...")
    raw = yf.download(symbols, period=HISTORY_PERIOD, interval="1d",
                      group_by="ticker", auto_adjust=True,
                      threads=True, progress=False)
    out = {}
    for sym in symbols:
        try:
            df = raw[sym].dropna(subset=["Close"])
            if len(df) >= 210:  # SMA200 + 直近判定に必要な最低本数
                out[sym] = df
        except (KeyError, TypeError):
            continue
    print(f"[prices] 有効データ: {len(out)} 銘柄")
    return out


# ============================================================
# テクニカル指標
# ============================================================
def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def compute_metrics(df: pd.DataFrame) -> dict | None:
    """1銘柄の指標一式を計算する。データ不足時は None。"""
    c = df["Close"]
    v = df["Volume"]

    sma20 = c.rolling(20).mean()
    sma50 = c.rolling(50).mean()
    sma200 = c.rolling(200).mean()
    r = rsi(c)

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal

    last = -1
    price = float(c.iloc[last])
    if np.isnan(sma200.iloc[last]):
        return None

    dollar_vol20 = float((c * v).rolling(20).mean().iloc[last])
    vol_ratio = float(v.iloc[last] / max(v.rolling(20).mean().iloc[last], 1))
    ret_1d = float(c.iloc[-1] / c.iloc[-2] - 1)
    ret_20d = float(c.iloc[-1] / c.iloc[-21] - 1)
    high_52w = float(c.rolling(252, min_periods=200).max().iloc[last])

    # MACDブルクロス: 直近5営業日以内にヒストグラムが負→正転換
    h5 = hist.iloc[-6:]
    macd_cross = bool((h5.iloc[-1] > 0) and (h5.min() < 0))

    # RSIリバウンド: 直近10営業日で40未満→現在45超
    r10 = r.iloc[-10:]
    rsi_rebound = bool((r10.min() < 40) and (r10.iloc[-1] > 45))

    # SMA50上向き (10営業日前比)
    sma50_up = bool(sma50.iloc[-1] > sma50.iloc[-11])

    # 押し目: 上昇トレンド中に SMA20 または SMA50 の ±3% 以内
    near_ma = (abs(price / float(sma20.iloc[last]) - 1) <= 0.03 or
               abs(price / float(sma50.iloc[last]) - 1) <= 0.03)

    return {
        "price": price,
        "ret_1d": ret_1d,
        "ret_20d": ret_20d,
        "rsi": float(r.iloc[last]),
        "above_sma50": price > float(sma50.iloc[last]),
        "sma50_above_200": float(sma50.iloc[last]) > float(sma200.iloc[last]),
        "sma50_up": sma50_up,
        "near_ma_pullback": bool(near_ma),
        "macd_cross": macd_cross,
        "rsi_rebound": rsi_rebound,
        "dist_52w_high": price / high_52w - 1,   # 0に近いほど高値圏
        "vol_ratio": vol_ratio,
        "dollar_vol20": dollar_vol20,
    }


# ============================================================
# スコアリング
# ============================================================
def score_all(metrics: dict[str, dict]) -> pd.DataFrame:
    df = pd.DataFrame(metrics).T

    # 流動性・価格・過熱フィルタ
    df = df[(df["price"] >= MIN_PRICE) &
            (df["dollar_vol20"] >= MIN_DOLLAR_VOL) &
            (df["rsi"] < RSI_OVERBOUGHT_EXCLUDE)].copy()

    # モメンタムはユニバース内パーセンタイル (0-1)
    mom_pct = df["ret_20d"].rank(pct=True)

    score = pd.Series(0.0, index=df.index)
    signals = pd.Series([[] for _ in range(len(df))], index=df.index)

    def add(cond, pts, label):
        nonlocal score
        cond = cond.astype(bool)
        score = score + cond * pts
        for i in df.index[cond]:
            signals[i].append(label)

    # --- トレンド (30点) ---
    add(df["above_sma50"], 10, "終値>SMA50")
    add(df["sma50_above_200"], 10, "SMA50>SMA200")
    add(df["sma50_up"], 10, "SMA50上向き")

    # --- モメンタム (25点) ---
    score = score + mom_pct * 15  # 20日リターンの相対順位
    add((df["rsi"] >= 50) & (df["rsi"] <= 70), 10, "RSI 50-70")

    # --- タイミング (25点) ---
    add(df["near_ma_pullback"] & df["above_sma50"] & df["sma50_above_200"],
        10, "押し目(MA近接)")
    add(df["macd_cross"], 10, "MACDブルクロス")
    add(df["rsi_rebound"], 5, "RSIリバウンド")

    # --- ブレイクアウト (10点) ---
    add(df["dist_52w_high"] >= -0.05, 10, "52週高値圏(-5%以内)")

    # --- 出来高 (10点) ---
    add((df["vol_ratio"] >= 1.3) & (df["ret_1d"] > 0), 10, "出来高急増+陽線")

    df["score"] = score.round(1)
    df["signals"] = signals
    return df.sort_values(["score", "ret_20d"], ascending=False)


# ============================================================
# レポート生成
# ============================================================
def build_report(ranked: pd.DataFrame, universe: pd.DataFrame,
                 n_scanned: int) -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    meta = universe.set_index("yf_symbol")
    top = ranked.head(TOP_N)

    lines = [
        f"# 米国株スクリーニング {today} (UTC)",
        "",
        f"- 対象: S&P 500 + Nasdaq 100 / スキャン {n_scanned} 銘柄 → "
        f"フィルタ通過 {len(ranked)} 銘柄",
        f"- フィルタ: 株価≥${MIN_PRICE:.0f}, 20日平均売買代金≥"
        f"${MIN_DOLLAR_VOL/1e6:.0f}M, RSI<{RSI_OVERBOUGHT_EXCLUDE}",
        "- スコア: トレンド30 + モメンタム25 + タイミング25 + "
        "ブレイクアウト10 + 出来高10 (満点100)",
        "",
        f"## 上位 {len(top)} 銘柄",
        "",
        "| # | Ticker | 銘柄名 | セクター | Score | 株価 | 前日比 | "
        "20日 | RSI | 52w高値比 | シグナル |",
        "|---|--------|--------|----------|-------|------|--------|"
        "------|-----|-----------|----------|",
    ]
    for rank, (sym, row) in enumerate(top.iterrows(), 1):
        name = meta.loc[sym, "name"] if sym in meta.index else ""
        sector = meta.loc[sym, "sector"] if sym in meta.index else ""
        lines.append(
            f"| {rank} | **{sym}** | {name} | {sector} | "
            f"{row['score']:.0f} | ${row['price']:.2f} | "
            f"{row['ret_1d']*100:+.1f}% | {row['ret_20d']*100:+.1f}% | "
            f"{row['rsi']:.0f} | {row['dist_52w_high']*100:+.1f}% | "
            f"{', '.join(row['signals'])} |"
        )
    lines += [
        "",
        "---",
        "*本レポートはテクニカル指標に基づく機械的スクリーニングであり、"
        "投資助言ではありません。売買判断はご自身の責任で行ってください。*",
    ]
    return "\n".join(lines)


# ============================================================
# デモモード (オフライン動作確認用)
# ============================================================
def make_demo_data(n: int = 40, days: int = 320, seed: int = 42):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=datetime.now(), periods=days)
    prices, uni_rows = {}, []
    for i in range(n):
        sym = f"DEMO{i:02d}"
        drift = rng.normal(0.0005, 0.0008)
        rets = rng.normal(drift, 0.02, days)
        close = 50 * np.exp(np.cumsum(rets))
        vol = rng.integers(2e6, 2e7, days).astype(float)
        prices[sym] = pd.DataFrame({
            "Open": close, "High": close * 1.01, "Low": close * 0.99,
            "Close": close, "Volume": vol}, index=idx)
        uni_rows.append({"yf_symbol": sym, "symbol": sym,
                         "name": f"Demo Corp {i}", "sector": "Demo",
                         "idx": "TEST"})
    return prices, pd.DataFrame(uni_rows)


# ============================================================
# メイン
# ============================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true",
                    help="合成データでオフライン動作確認")
    args = ap.parse_args()

    if args.demo:
        prices, universe = make_demo_data()
    else:
        universe = fetch_universe()
        prices = fetch_prices(universe["yf_symbol"].tolist())

    metrics = {}
    for sym, df in prices.items():
        try:
            m = compute_metrics(df)
            if m:
                metrics[sym] = m
        except Exception as e:
            print(f"[metrics] {sym}: {e}", file=sys.stderr)

    if not metrics:
        raise RuntimeError("指標を計算できた銘柄がありません")

    ranked = score_all(metrics)
    report = build_report(ranked, universe, n_scanned=len(prices))

    REPORTS_DIR.mkdir(exist_ok=True)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    (REPORTS_DIR / f"{today}.md").write_text(report, encoding="utf-8")
    (REPORTS_DIR / "latest.md").write_text(report, encoding="utf-8")
    print(f"[done] reports/{today}.md を出力しました")
    print(report[:1500])


if __name__ == "__main__":
    main()
