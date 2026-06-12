---
source: project/av-actress-blog/README.md
migrated_at: 2026-05-16
---

<!-- ============================================
     このファイルは Vault からの自動ミラーです。
     編集は vault\01_Project\av-actress-blog\README.md で行ってください。
     ここを直接編集しても次回同期で上書きされます。
     最終同期: 2026-05-22 03:15:02
============================================ -->
# av-actress-blog

FANZA Affiliate API を使った静的HTMLサイトの自動生成システム。
Cloudflare Pages にホストし、毎日 Windows タスクスケジューラから更新する。

## セットアップ

```powershell
# 仮想環境
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 依存
pip install -r requirements.txt

# .env ファイル作成（.env.example をコピーして編集）
Copy-Item .env.example .env
# DMM_API_ID, DMM_AFFILIATE_ID を設定
```

`.env` の例:
```
DMM_API_ID=your_api_id
DMM_AFFILIATE_ID=ProjectIYASA-990
SITE_BASE_URL=https://av-actress-navi.pages.dev
DISCORD_WEBHOOK_URL=
```

## 初回ブートストラップ

`config/actresses.yaml` に生成対象の女優IDを書き込む。
DMM API から人気上位の女優を自動収集する場合：

```powershell
python scripts\generate.py --bootstrap-actresses 50
```

→ `config/actresses.yaml` に50名分のIDが書かれる。手動で見直して、不要なIDは削除してコミット。

## 生成

少数で動作確認:
```powershell
python scripts\generate.py --limit-actresses 3
```

全ページ生成:
```powershell
python scripts\generate.py
```

成功すると以下が更新される:
- `actress/{id}.html` — 女優個別ページ（バリデーション通過分のみ原子的置換）
- `ranking-top10.html` — ランキング記事
- `index.html` — トップページ
- `sitemap.xml` — サイトマップ
- `manifest.json` — 生成管理ファイル（前回比較用）

## 定期実行（Windows タスクスケジューラ）

1. タスクスケジューラを開く
2. 「タスクの作成」 → 名前: `av-actress-blog-generate`
3. トリガー: 毎日 9:00、「タスクの実行を逃した場合に再実行する」を有効化
4. 操作: プログラムの開始 → `C:\Users\haruc\projects\av-actress-blog\run_generate.bat`
5. 「ユーザーがログオンしているときのみ実行する」でOK

`run_generate.bat` は以下を実施:
- `python scripts/generate.py` 実行
- 失敗時は Discord Webhook に通知（`DISCORD_WEBHOOK_URL` 設定時）
- 成功時のみ `git add` → `git commit` → `git push`（生成物のみホワイトリストで add）
- Cloudflare Pages が GitHub 連携で自動デプロイ

## 設計判断（プラン参照）

詳細な設計判断・障害対応ルールは
`C:\Users\haruc\.claude\plans\c-users-haruc-projects-av-actress-blog-rosy-sutherland.md` を参照。

主な特徴:
- **DTO正規化層** で API スキーマ揺れを吸収（`scripts/dmm_client.py`）
- **`tmp/build/` → `os.replace()` で原子的置換** によりページ単位で安全に部分更新
- **24時間 TTL キャッシュ** で API 呼び出し回数を削減（`data/cache/`）
- **指数バックオフリトライ**（429/5xx 時、最大3回）
- **公開前バリデーション**: title/canonical/affiliate link/img の存在チェック
- **異常検知**: 前回比 -20% で push 中止（`--force` で上書き可）
- **manifest.json** で各ページの生成結果と前回比較を追跡

## ディレクトリ構成

```
.
├── scripts/             # ジェネレーター
├── templates/           # Jinja2 テンプレート
├── config/              # YAML 設定（女優ID・ジャンル）
├── actress/             # 自動生成: 女優個別ページ
├── data/cache/          # API レスポンスキャッシュ（gitignore）
├── logs/                # 実行ログ（gitignore）
├── tmp/build/           # 生成物の中間置き場（gitignore）
├── *.html               # 自動生成 + 手書き (contact/privacy)
├── style.css            # 既存スタイル（変更なし）
├── manifest.json        # 生成管理ファイル
├── sitemap.xml          # 自動生成
└── run_generate.bat     # タスクスケジューラ起動バッチ
```

## API 利用上の注意

- **クレジット表記**: フッターに `Powered by DMM Web Service` を表示
- **キャッシュ TTL**: 24 時間
- **レート制限**: リクエスト間 0.5 秒 sleep、429/5xx で指数バックオフ
- **アフィリエイトID**: 通常サイト ID（`xxx-001`）ではなく API 専用枠（`xxx-990`〜`xxx-999`）を使用

## トラブルシューティング

- **API 認証エラー**: `.env` の `DMM_API_ID` / `DMM_AFFILIATE_ID` を確認
- **作品が取れない**: アフィリエイトID審査が通っていない可能性 → DMMアフィリエイト管理画面で確認
- **異常検知で止まる**: ログを確認、問題なければ `run_generate.bat --force`
- **キャッシュ古い**: `data/cache/` を空にしてから再実行
