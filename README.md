# IBD Full-Text Bot

PubMedで **全文(PMC無料全文)が参照できる** IBD関連論文だけを抽出し、本文を精読した
詳細な日本語要約を生成して、既存の Notion 論文DBへ **1日1回** 自動投稿する全文専用bot。

> 既存の `ibd-paper-bot`(abstractベース)とは独立した別botです。投稿先の Notion DB は同じ
> 統合論文DB(`NOTION_DATABASE_ID`)で、`Source Bot` プロパティは `IBD fulltext bot` で区別します。

## abstract版との違い

| | ibd-paper-bot(既存) | ibd-fulltext-bot(本bot) |
|---|---|---|
| 対象 | abstractが取れる論文 | **PMC全文が取れる論文のみ** |
| 検索 | 研究タイプで絞り込み | + `"pubmed pmc"[sb]` で全文ありに限定 |
| 要約根拠 | abstract | **本文全体(背景〜考察)** |
| 要約の厚み | 標準 | 方法の詳細・結果の具体的数値・Discussionの限界まで |
| 投稿頻度 | 1日4回 | **1日1回** |
| 全文なし論文 | 投稿する | 投稿しない(`FULLTEXT_ONLY=True`) |

## スコープ

- IBD (UC, Crohn's disease)
- PSC-IBD
- Immune checkpoint inhibitor関連の colitis / cholangitis / enterocolitis
- irAE (gastrointestinal)

## 全文(PMC Full Text)取得の仕組み

1. PubMed検索を `"pubmed pmc"[sb]` で PMC収載論文に限定し、高エビデンスの研究タイプに絞る
2. efetchメタデータから PMC ID を抽出
3. `fulltext.py` が efetch(db=pmc) の JATS XML から本文(背景〜考察)を取得・整形
   - References・図表・数式・脚注は要約ノイズになるため除去
   - Open Accessサブセット外で本文が返らない論文は「全文なし」と判定 → スキップ
4. 取得した全文を Claude API に渡し、本文精読の詳細プロンプトで要約
5. Notion ページに本文(`PMC Full Text` トグル)、全文バッジ、PMCリンクを付けて投稿

## 信頼度フィルタ

1. **PubMed検索段階**: Publication Type を RCT / Meta-analysis / Systematic Review / Multicenter Study / Clinical Trial / Validation Study / Guideline などに限定。Case Report, Letter, Editorial は除外。さらに `"pubmed pmc"[sb]` で全文ありに限定
2. **スコアリング**: ジャーナルIF + 研究タイプ + 新着 + 全文ボーナス。全文ありはホワイトリスト/IF足切りを免除(`FULLTEXT_RELAX_FILTER`)
3. **AI判定**: Claudeが研究デザインとサンプルサイズから★★★/★★/★を自動判定

## 実行スケジュール

GitHub Actions cron で **1日1回**(JST 07:00 / UTC 22:00)。`workflow_dispatch` で手動実行も可能。

## セットアップ

### 1. リポジトリ作成

```bash
git init ibd-fulltext-bot
cd ibd-fulltext-bot
# このコードベース一式をコピー
git add .
git commit -m "Initial commit"
git remote add origin git@github.com:<your-account>/ibd-fulltext-bot.git
git push -u origin main
```

### 2. GitHub Secrets設定

リポジトリの Settings → Secrets and variables → Actions で以下を設定:

| Secret名 | 値 |
|---|---|
| `NOTION_TOKEN` | Notion Integration token (`secret_...`) |
| `NOTION_DATABASE_ID` | `a65693b6-4d0d-4050-8713-8300ee4a7fba`(統合論文DBのDatabase ID) |
| `ANTHROPIC_API_KEY` | Anthropic API key (`sk-ant-...`) |
| `PUBMED_EMAIL` | NCBIに登録したメールアドレス |
| `PUBMED_API_KEY` | (任意) NCBI API key |

### 3. Notion Integration設定

- https://www.notion.so/profile/integrations から Internal Integration を作成
- 統合論文DBページに Integration を接続(右上 ... → Connections)
- `Source Bot` select に `IBD fulltext bot` オプションが自動追加される

### 4. ローカルテスト実行

```bash
cp .env.example .env
# .envに各種シークレットを記入
pip install -r requirements.txt
export $(cat .env | xargs)
python src/main.py
```

## チューニング

`src/config.py` で調整可能:

| 設定 | 説明 | デフォルト |
|---|---|---|
| `PUBMED_RELDATE_DAYS` | 検索範囲(日) | `730` |
| `PAPERS_PER_RUN_MAX` | 1回の処理上限 | `5` |
| `FULLTEXT_ONLY` | 全文がある論文だけ投稿 | `True` |
| `FULLTEXT_RELAX_FILTER` | 全文ありの論文を足切り免除 | `True` |
| `FULLTEXT_SCORE_BONUS` | 全文ありに加算するスコア | `8.0` |
| `FULLTEXT_MAX_CHARS` | Claudeへ渡す本文の最大文字数 | `24000` |
| `CLAUDE_MAX_TOKENS_FULLTEXT` | 全文要約時の出力上限トークン | `3500` |
| `SKIP_LOW_IMPORTANCE` | Trueで★評価の論文は投稿しない | `False` |

## ファイル構成

```
src/
├── main.py          # エントリーポイント(全文専用フロー)
├── config.py        # 設定値(FULLTEXT_ONLY=True, PMC限定クエリ)
├── pubmed.py        # PubMed E-utilities ラッパー(PMC ID抽出を含む)
├── fulltext.py      # PMC全文の取得・整形(JATS XML → 本文テキスト)
├── filter.py        # スコアリングとフィルタ(全文優先)
├── ai_summarize.py  # Claude API要約(全文ベースの詳細要約)
└── notion_writer.py # Notion API投稿(全文トグル・ソースバッジ)
```
