# 米国株デイリー・テクニカルスクリーナー

S&P 500 + Nasdaq 100(約520銘柄)を毎日自動スクリーニングし、テクニカル複合スコアで購入候補の上位15銘柄をレポートするGitHub Actionsパイプライン。**APIキー不要**(yfinance + Wikipedia)。

## 仕組み

1. 平日 22:30 UTC(日本時間 翌朝7:30、米国市場クローズ後)に自動実行
2. Wikipediaから最新の指数構成銘柄を取得 → yfinanceで日足15ヶ月分を一括ダウンロード
3. 指標計算・スコアリング(満点100)
   - トレンド 30点: 終値>SMA50 / SMA50>SMA200 / SMA50上向き
   - モメンタム 25点: 20日リターンのユニバース内順位 / RSI 50-70
   - タイミング 25点: 押し目(SMA20/50近接) / MACDブルクロス / RSIリバウンド
   - ブレイクアウト 10点: 52週高値-5%以内
   - 出来高 10点: 出来高1.3倍以上+陽線
4. フィルタ: 株価≥$5、20日平均売買代金≥$20M、RSI≥78の過熱銘柄は除外
5. `reports/YYYY-MM-DD.md` をコミット + **GitHub Issueを自動作成**(これが通知になる)

## セットアップ

1. GitHubで新規リポジトリを作成(PrivateでOK)
2. このフォルダの中身をpush
   ```bash
   git init && git add -A && git commit -m "initial"
   git branch -M main
   git remote add origin git@github.com:<user>/<repo>.git
   git push -u origin main
   ```
3. リポジトリの **Settings → Actions → General → Workflow permissions** で
   **Read and write permissions** を選択して保存
4. **Actions タブ → Daily US Stock Screener → Run workflow** で手動テスト実行
5. 通知を受け取る設定:
   - リポジトリ右上 **Watch → All Activity**
   - GitHubモバイルアプリを入れておくと、Issue作成時にプッシュ通知が届く

## カスタマイズ

`screener.py` 冒頭の設定値を変更:

| 変数 | 既定値 | 意味 |
|------|--------|------|
| `TOP_N` | 15 | レポート掲載銘柄数 |
| `MIN_PRICE` | 5.0 | 最低株価 |
| `MIN_DOLLAR_VOL` | 20M | 最低平均売買代金 |
| `RSI_OVERBOUGHT_EXCLUDE` | 78 | 過熱除外ライン |

実行時刻は `.github/workflows/daily.yml` の cron を変更。

## ローカルテスト

```bash
pip install -r requirements.txt
python screener.py          # 実データで実行
python screener.py --demo   # 合成データでオフライン動作確認
```

## 注意事項

- yfinanceはYahoo Financeの非公式APIです。仕様変更で動かなくなる可能性があり、その場合はyfinanceのアップデート(`pip install -U yfinance`)で解消することが多いです
- 本ツールはテクニカル指標に基づく機械的スクリーニングであり、**投資助言ではありません**。売買判断は自己責任で行ってください
