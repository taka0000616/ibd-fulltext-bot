"""
論文の信頼度スコアリングと事前フィルタ
PubMedで拾った論文 → ジャーナル/研究タイプで足切り → 高スコア順にソート
"""
from typing import List, Dict
import config


# Publication Typeのエビデンスレベルスコア
# 大きいほど高エビデンス
PUB_TYPE_SCORE = {
    "Meta-Analysis": 10,
    "Systematic Review": 9,
    "Practice Guideline": 9,
    "Guideline": 8,
    "Randomized Controlled Trial": 8,
    "Clinical Trial, Phase III": 7,
    "Clinical Trial, Phase II": 5,
    "Multicenter Study": 5,
    "Validation Study": 5,
    "Clinical Trial": 4,
    "Observational Study": 3,
    "Comparative Study": 2,
}


def _recency_boost(pub_date: str) -> float:
    """
    新着論文への加点。pub_date は "YYYY-MM-DD" 形式
    過去30日以内: +20点
    過去90日以内: +10点
    過去180日以内: +5点
    それ以降: 0点
    """
    if not pub_date:
        return 0.0
    try:
        from datetime import datetime, timezone
        pub_dt = datetime.strptime(pub_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        days_ago = (datetime.now(timezone.utc) - pub_dt).days
        if days_ago <= 30:
            return 20.0
        elif days_ago <= 90:
            return 10.0
        elif days_ago <= 180:
            return 5.0
        else:
            return 0.0
    except (ValueError, TypeError):
        return 0.0


def calculate_score(paper: Dict) -> float:
    """論文の総合スコア。Journal IF + Pub Type score + Recency boost + 全文ボーナス"""
    # ジャーナルスコア(ホワイトリストになければ0)
    journal_if = config.JOURNAL_WHITELIST.get(paper["journal_iso"], 0)

    # Pub Typeスコア(最大値を採用)
    pub_type_score = max(
        (PUB_TYPE_SCORE.get(pt, 0) for pt in paper["pub_types"]),
        default=0,
    )

    # 新着ボーナス
    recency = _recency_boost(paper.get("pub_date", ""))

    # 全文ありボーナス(全文で詳細要約できる論文を優先)
    fulltext_bonus = config.FULLTEXT_SCORE_BONUS if paper.get("has_pmc") else 0.0

    # 重み付け: ジャーナルIFを主、Pub Typeを副、Recency/全文で優先度調整
    return journal_if + pub_type_score * 2 + recency + fulltext_bonus


def filter_and_rank(papers: List[Dict]) -> List[Dict]:
    """
    ジャーナルホワイトリスト/IF閾値で足切り。ただし全文(PMC)が
    参照できる論文は、FULLTEXT_RELAX_FILTER=True のとき足切りを免除する。
    残った論文をスコア降順にソートして返す(上限はmain側で制御)。
    """
    filtered = []
    passed_journal = 0
    passed_fulltext = 0

    for paper in papers:
        journal_if = config.JOURNAL_WHITELIST.get(paper["journal_iso"], 0)
        meets_journal = journal_if >= config.MIN_IF_THRESHOLD
        # 全文ありかつ緩和フラグONなら、ホワイトリスト外でも通す
        meets_fulltext = config.FULLTEXT_RELAX_FILTER and paper.get("has_pmc")

        if not (meets_journal or meets_fulltext):
            continue

        paper["score"] = calculate_score(paper)
        paper["estimated_if"] = journal_if  # ホワイトリスト外は0(IFプロパティ未設定扱い)
        filtered.append(paper)

        if meets_journal:
            passed_journal += 1
        elif meets_fulltext:
            passed_fulltext += 1

    # スコア降順 → 同点なら新しい順
    filtered.sort(
        key=lambda p: (p["score"], p["pub_date"] or ""),
        reverse=True,
    )

    print(
        f"[Filter] {len(papers)} papers → {len(filtered)} "
        f"(journal/IF: {passed_journal}, 全文緩和: {passed_fulltext})"
    )

    return filtered


def map_pub_type_to_study_type(pub_types: List[str]) -> str:
    """PubMedのPublication TypeをNotionのStudy Type selectにマッピング"""
    pt_set = set(pub_types)

    # 優先度順
    if "Meta-Analysis" in pt_set:
        return "Meta-analysis"
    if "Systematic Review" in pt_set:
        return "Systematic Review"
    if "Practice Guideline" in pt_set or "Guideline" in pt_set:
        return "Guideline"
    if "Randomized Controlled Trial" in pt_set:
        return "RCT"
    if any("Clinical Trial" in pt for pt in pt_set):
        return "RCT"  # Clinical TrialはRCTに準じる扱い
    if "Validation Study" in pt_set:
        return "Cohort"
    if "Multicenter Study" in pt_set or "Observational Study" in pt_set:
        return "Cohort"
    if "Comparative Study" in pt_set:
        return "Case-control"
    if "Review" in pt_set:
        return "Review"
    return "Other"


def map_journal_to_select(journal_iso: str) -> str:
    """ジャーナルISO略称をNotionのJournal selectにマッピング"""
    # NotionのJournal selectで定義済みのもの
    mapping = {
        "N Engl J Med": "NEJM",
        "Lancet": "Lancet",
        "JAMA": "JAMA",
        "Nature": "Nature",
        "Cell": "Cell",
        "Nat Med": "Nature Medicine",
        "Gastroenterology": "Gastroenterology",
        "Gut": "Gut",
        "Hepatology": "Hepatology",
        "J Hepatol": "J Hepatol",
        "Am J Gastroenterol": "Am J Gastroenterol",
        "Clin Gastroenterol Hepatol": "CGH",
        "J Crohns Colitis": "JCC",
        "Inflamm Bowel Dis": "IBD Journal",
        "Aliment Pharmacol Ther": "Aliment Pharmacol Ther",
        "Endoscopy": "Endoscopy",
        "Gastrointest Endosc": "GIE",
    }
    return mapping.get(journal_iso, "Other")


def infer_category(paper: Dict) -> List[str]:
    """論文タイトル/アブストラクトからCategoryを推測"""
    text = (paper["title"] + " " + paper["abstract"]).lower()
    categories = []

    # IBD
    has_uc = any(k in text for k in ["ulcerative colitis", "uc patient"])
    has_cd = any(k in text for k in ["crohn", "crohn's"])
    if has_uc and not has_cd:
        categories.append("UC")
    elif has_cd and not has_uc:
        categories.append("Crohn")
    elif has_uc and has_cd:
        categories.append("IBD-general")
    elif "inflammatory bowel disease" in text or "ibd" in text:
        categories.append("IBD-general")

    # 関連領域
    if "primary sclerosing cholangitis" in text or "psc" in text:
        categories.append("肝臓")
    if "checkpoint inhibitor" in text or "irae" in text:
        categories.append("腫瘍")

    return categories if categories else ["IBD-general"]
