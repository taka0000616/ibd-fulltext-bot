"""
IBD Paper Bot - 設定
全パラメータをここに集約。動作変更したい時はこのファイルだけ編集する。
"""
import os

# ============================================================
# Notion設定
# ============================================================
NOTION_TOKEN = os.environ["NOTION_TOKEN"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
# 統合論文DBの Database ID: a65693b6-4d0d-4050-8713-8300ee4a7fba
NOTION_VERSION = "2022-06-28"

# ============================================================
# Claude API設定
# ============================================================
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
CLAUDE_MODEL = "claude-sonnet-4-5"
CLAUDE_MAX_TOKENS = 2000
# 全文ベースで要約する場合はより長い出力を許可
CLAUDE_MAX_TOKENS_FULLTEXT = 3500

# ============================================================
# PubMed設定
# ============================================================
PUBMED_EMAIL = os.environ.get("PUBMED_EMAIL", "your-email@example.com")
PUBMED_API_KEY = os.environ.get("PUBMED_API_KEY", "")  # 任意。あれば3req/sec→10req/sec

# --- 検索範囲 ---
PUBMED_RELDATE_DAYS = 730  # 過去2年以内

# --- 検索クエリ: IBD + 関連領域 ---
# Publication Type フィルタで信頼度の高い研究デザインに限定
PUBMED_QUERY = """(
  "inflammatory bowel disease"[MeSH] OR "ulcerative colitis"[MeSH] OR "Crohn disease"[MeSH]
  OR "IBD"[tiab] OR "ulcerative colitis"[tiab] OR "Crohn's disease"[tiab] OR "Crohn disease"[tiab]
  OR "primary sclerosing cholangitis"[MeSH] OR "PSC-IBD"[tiab]
  OR (("immune checkpoint inhibitor"[tiab] OR "checkpoint inhibitor"[tiab] OR "ICI"[tiab] OR "anti-PD-1"[tiab] OR "anti-CTLA-4"[tiab])
      AND ("colitis"[tiab] OR "cholangitis"[tiab] OR "enterocolitis"[tiab] OR "hepatitis"[tiab]))
  OR "checkpoint inhibitor colitis"[tiab]
  OR ("irAE"[tiab] AND ("colitis"[tiab] OR "cholangitis"[tiab]))
)
AND (
  Randomized Controlled Trial[ptyp]
  OR Meta-Analysis[ptyp]
  OR Systematic Review[ptyp]
  OR Multicenter Study[ptyp]
  OR Clinical Trial, Phase III[ptyp]
  OR Clinical Trial, Phase II[ptyp]
  OR Clinical Trial[ptyp]
  OR Observational Study[ptyp]
  OR Comparative Study[ptyp]
  OR Validation Study[ptyp]
  OR Practice Guideline[ptyp]
  OR Guideline[ptyp]
)
AND English[lang]
AND "pubmed pmc"[sb]
NOT (Editorial[ptyp] OR Letter[ptyp] OR Comment[ptyp] OR News[ptyp] OR Case Reports[ptyp])"""
# ↑ "pubmed pmc"[sb] = PMC(PubMed Central)に本文がある論文に限定。
#   全文専用botのため、検索段階から全文参照可能な論文へ絞り込む。

# 1回の実行で処理する最大件数(1日1回・全文精読でコスト重め → 控えめに設定)
PAPERS_PER_RUN_MAX = 5

# ============================================================
# ジャーナルホワイトリスト: 信頼度の高いジャーナル
# ============================================================
# キーは ISO略称(PubMedのISOAbbreviation)、値はおおよそのIF
# IFスコアリングと「Top tier優先」判定に使用
JOURNAL_WHITELIST = {
    # Top tier general
    "N Engl J Med": 96.0,
    "Lancet": 98.0,
    "JAMA": 63.0,
    "BMJ": 105.0,
    # Top basic/translational
    "Nature": 50.0,
    "Cell": 45.0,
    "Nat Med": 58.0,
    "Sci Transl Med": 17.0,
    "Cell Host Microbe": 30.0,
    "Immunity": 25.0,
    "Nat Immunol": 27.0,
    "Sci Immunol": 18.0,
    "Mucosal Immunol": 8.0,
    # GI top tier
    "Gastroenterology": 29.0,
    "Gut": 24.0,
    "Hepatology": 13.0,
    "J Hepatol": 26.0,
    "Lancet Gastroenterol Hepatol": 30.0,
    # IBD/GI specialty
    "Am J Gastroenterol": 9.0,
    "Clin Gastroenterol Hepatol": 11.0,
    "J Crohns Colitis": 8.0,
    "Inflamm Bowel Dis": 5.0,
    "Aliment Pharmacol Ther": 6.0,
    # 中堅GI誌(中堅IBD/消化器)
    "United European Gastroenterol J": 5.0,
    "Therap Adv Gastroenterol": 4.0,
    "Dig Dis Sci": 3.5,
    "J Gastroenterol": 6.0,
    "J Gastroenterol Hepatol": 4.0,
    "Dig Liver Dis": 4.0,
    "Scand J Gastroenterol": 3.0,
    "Eur J Gastroenterol Hepatol": 3.0,
    "World J Gastroenterol": 4.0,
    "BMC Gastroenterol": 3.0,
    "Intest Res": 3.5,
    "Crohns Colitis 360": 3.0,
    # Endoscopy
    "Endoscopy": 11.0,
    "Gastrointest Endosc": 7.0,
    "Dig Endosc": 6.0,
    "Endosc Int Open": 3.0,
    # Oncology(irAE関連)
    "J Clin Oncol": 45.0,
    "Lancet Oncol": 51.0,
    "Ann Oncol": 50.0,
}

# IFスコア閾値: これ未満のジャーナルはスキップ
MIN_IF_THRESHOLD = 3.0

# ============================================================
# 全文(PMC フルテキスト)設定
# ============================================================
# PMC無料全文(Open Accessサブセット)が取得できる論文を優先し、
# 全文があれば全文ベースで詳細要約、無ければabstractで要約する。

# 全文が取得できた論文は、ジャーナルホワイトリスト/IF閾値の足切りを免除する
# (= 全文要件でフィルタを緩和し、網羅性を上げる)
FULLTEXT_RELAX_FILTER = True

# 全文があるものだけを投稿対象にする(True)か、abstractのみの論文も許容する(False)
# このbotは「全文専用bot」なので True(全文が取得できない論文は投稿しない)
FULLTEXT_ONLY = True

# 並べ替え時、全文ありの論文に加算するスコアボーナス
FULLTEXT_SCORE_BONUS = 8.0

# Claudeに渡す全文の最大文字数(トークン・コスト対策)
# References/図表キャプションを除いた本文をこの長さで打ち切る
FULLTEXT_MAX_CHARS = 24000

# Importance判定にAIを使うか
USE_AI_IMPORTANCE_JUDGMENT = True
# ★以下はNotion投稿をスキップ (False = ★も投稿、True = ★★以上のみ投稿)
SKIP_LOW_IMPORTANCE = False
