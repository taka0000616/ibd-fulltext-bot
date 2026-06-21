"""
Claude APIで論文の日本語要約とImportance判定を行う
"""
import json
import requests
from typing import Dict, Optional

import config


ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


# 出力スキーマ・Importance基準・注意は全文/abstract共通
_OUTPUT_SPEC = """# 出力形式(必ずJSONのみ、コードブロックなし)
{{
  "title_jp": "日本語タイトル(簡潔に、医学用語は適切に)",
  "key_findings": "{key_findings_spec}",
  "ai_summary": "100字以内で論文の核心を一文で。「何の集団に、何を検証し、どんな結果だったか」を凝縮",
  "importance": "★★★" or "★★" or "★",
  "importance_reason": "なぜそのImportanceを付けたか(1文)",
  "relevance_tags": ["ASUC risk stratification" | "single-cell/spatial" | "refractory UC subtyping" | "biologic combo therapy" | "ML/AI in IBD" | "pharmacogenomics" | "その他" のうち該当するもののリスト、無ければ空配列]
}}

# Importance判定基準
- ★★★: NEJM/Lancet/Nature級、または領域の診療を変えるpivotal trial、ガイドライン
- ★★: GI top tier (Gastroenterology/Gut/Lancet GH等)、大規模RCT、重要なMeta-analysis
- ★: Specialty journal、観察研究、研究デザインに限界あり

# 注意
- 数値・統計値(HR, OR, p値, CI, n数)は正確に引用すること。Key Findingsの「結果」セクションでは特に丁寧に
- 不明確な点を捏造しないこと、本文/abstractに無い情報は推測で書かない
- 日本語の医学用語を使用(例: "ulcerative colitis"→"潰瘍性大腸炎")
- ai_summary は厳密に100字以内。冗長な前置きや「本研究では」等の表現は省く
- key_findings は厚みを持たせる。特に「臨床的解釈」セクションでは、論文を読んだだけでは見えにくい既存研究との関係や、limitations の意義、IBDの病態理解や鷹将さん的なリサーチクエスチョン(ASUC risk, single-cell/spatial, biologic combo, ML/AI 等)との関連を意識して補足する"""


# Abstractのみの場合のkey_findings仕様
_KF_ABSTRACT = "**背景・目的**(2-3文): 臨床的疑問と研究の位置づけ\\n\\n**方法**(2-3文): デザイン、対象集団のn数と特徴、主要評価項目、解析手法\\n\\n**結果**(3-5文): 主要評価項目の結果を具体的な数値(HR/OR/RR/CI/p値)とともに記載。重要な副次評価項目や安全性データも含む\\n\\n**結論**(2-3文): 著者の結論と、それを支持/制限するデータ上のポイント\\n\\n**臨床的解釈**(3-4文): 既存エビデンスとの位置づけ、研究のstrengths、limitations、日常診療や今後のIBD研究への含意"

# 全文がある場合のkey_findings仕様(本文を読めるため、より厚く・具体的に)
_KF_FULLTEXT = "**背景・目的**(3-4文): 臨床的疑問、既存研究のギャップ、本研究の位置づけ\\n\\n**方法**(4-6文): デザイン、対象集団のn数・組み入れ/除外基準、介入/曝露、主要・副次評価項目、統計解析手法、サブグループ解析の有無\\n\\n**結果**(6-9文): 主要評価項目を具体的な数値(HR/OR/RR/95%CI/p値、絶対差)とともに記載。副次評価項目、サブグループ、安全性・有害事象、感度分析など本文中の重要結果も網羅\\n\\n**結論**(2-3文): 著者の結論と、それを支持/制限する本文中のデータ\\n\\n**臨床的解釈**(4-6文): 既存エビデンスとの位置づけ、strengths、limitations(本文Discussionで著者が認める限界を含む)、日常診療・今後のIBD研究への含意。鷹将さん的リサーチクエスチョン(ASUC risk, single-cell/spatial, biologic combo, ML/AI 等)との関連も補足"


SUMMARIZE_PROMPT_ABSTRACT = """あなたは消化器内科専門医(特にIBD臨床・研究)向けに最新論文を解説する医学エディタです。
以下の論文のabstractを読み、日本語で構造化された要約を作成してください。

# 論文情報
- タイトル: {title}
- ジャーナル: {journal} (推定IF: {if_score})
- 発行日: {pub_date}
- 研究タイプ: {pub_types}
- アブストラクト:
{abstract}

""" + _OUTPUT_SPEC


SUMMARIZE_PROMPT_FULLTEXT = """あなたは消化器内科専門医(特にIBD臨床・研究)向けに最新論文を解説する医学エディタです。
以下の論文は **全文(本文)** が参照できます。abstractではなく本文全体を精読し、
方法の詳細・結果の具体的数値・考察での限界まで踏み込んだ、密度の高い日本語要約を作成してください。

# 論文情報
- タイトル: {title}
- ジャーナル: {journal} (推定IF: {if_score})
- 発行日: {pub_date}
- 研究タイプ: {pub_types}
- アブストラクト:
{abstract}

# 本文(PMC全文。References/図表は除去済み){truncated_note}
{full_text}

""" + _OUTPUT_SPEC


def _call_claude(prompt: str, max_tokens: int, pmid: str) -> Optional[Dict]:
    """Claude APIを呼び、JSONをパースして返す共通処理"""
    headers = {
        "x-api-key": config.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": config.CLAUDE_MODEL,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    content = ""
    try:
        resp = requests.post(ANTHROPIC_URL, headers=headers, json=payload, timeout=180)
        resp.raise_for_status()
        data = resp.json()
        content = data["content"][0]["text"].strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        return json.loads(content)
    except json.JSONDecodeError as e:
        print(f"[Claude] JSON parse error for PMID {pmid}: {e}")
        print(f"Raw content: {content[:500]}")
        return None
    except Exception as e:  # noqa: BLE001
        print(f"[Claude] API error for PMID {pmid}: {e}")
        return None


def summarize_paper(paper: Dict) -> Optional[Dict]:
    """論文を要約してImportance判定する。全文があれば全文ベースで詳細に要約"""
    has_full_text = paper.get("has_full_text") and paper.get("full_text")

    if has_full_text:
        truncated_note = "(※本文が長いため後半を一部省略)" if paper.get("full_text_truncated") else ""
        prompt = SUMMARIZE_PROMPT_FULLTEXT.format(
            title=paper["title"],
            journal=paper["journal_iso"],
            if_score=paper.get("estimated_if", "?"),
            pub_date=paper["pub_date"] or "unknown",
            pub_types=", ".join(paper["pub_types"]),
            abstract=paper["abstract"] or "(no abstract available)",
            full_text=paper["full_text"],
            truncated_note=truncated_note,
            key_findings_spec=_KF_FULLTEXT,
        )
        max_tokens = config.CLAUDE_MAX_TOKENS_FULLTEXT
    else:
        prompt = SUMMARIZE_PROMPT_ABSTRACT.format(
            title=paper["title"],
            journal=paper["journal_iso"],
            if_score=paper.get("estimated_if", "?"),
            pub_date=paper["pub_date"] or "unknown",
            pub_types=", ".join(paper["pub_types"]),
            abstract=paper["abstract"] or "(no abstract available)",
            key_findings_spec=_KF_ABSTRACT,
        )
        max_tokens = config.CLAUDE_MAX_TOKENS

    result = _call_claude(prompt, max_tokens, paper["pmid"])
    if result is not None:
        result["_source"] = "fulltext" if has_full_text else "abstract"
    return result
