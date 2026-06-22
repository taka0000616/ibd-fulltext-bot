"""
PMC(PubMed Central)からの全文取得・整形

PubMedで「全文参照できる」= PMC無料全文(Open Accessサブセット)が
公開されている論文を対象に、本文(背景〜考察)をプレーンテキストとして抽出する。

取得経路:
  efetch (db=pmc, retmode=xml) で JATS XML を取得し、<body> 配下の
  セクション/段落テキストを連結する。OAサブセット外の論文は本文が
  返らない(front matterのみ)ため、その場合は「全文なし」と判定する。

References・表・図キャプション・数式は要約ノイズになるため除去する。
"""
import re
import time
import requests
import xml.etree.ElementTree as ET
from typing import Dict, Optional

import config

BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

# 本文として有効と見なす最低文字数(これ未満なら全文取得失敗とみなす)
_MIN_BODY_CHARS = 800


def _build_params(**kwargs) -> dict:
    params = {"tool": "ibd-fulltext-bot", "email": config.PUBMED_EMAIL}
    if config.PUBMED_API_KEY:
        params["api_key"] = config.PUBMED_API_KEY
    params.update(kwargs)
    return params


def fetch_full_text(pmcid: str, retries: int = 2) -> Optional[Dict]:
    """
    PMCIDから全文を取得して整形済みテキストを返す。
    取得・整形に失敗(OA対象外など)した場合は None。

    返り値: {"text": str, "char_count": int, "sections": int}
    """
    if not pmcid:
        return None

    # "PMC1234567" → 数値部分のみでも可。efetchは両形式を受け付ける
    numeric_id = pmcid.replace("PMC", "").strip()
    if not numeric_id.isdigit():
        return None

    params = _build_params(db="pmc", id=numeric_id, retmode="xml")

    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = requests.get(f"{BASE_URL}/efetch.fcgi", params=params, timeout=120)
            resp.raise_for_status()
            return _parse_pmc_xml(resp.text)
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < retries:
                time.sleep(1.0 * (attempt + 1))
            continue

    print(f"[FullText] fetch failed for {pmcid}: {last_err}")
    return None


def _parse_pmc_xml(xml_text: str) -> Optional[Dict]:
    """JATS XMLの<body>から本文テキストを抽出"""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"[FullText] XML parse error: {e}")
        return None

    body = root.find(".//body")
    if body is None:
        # OAサブセット外: 本文が提供されていない
        return None

    sections = _extract_body_sections(body)
    text = "\n\n".join(s for s in sections if s.strip())
    text = _normalize_whitespace(text)

    if len(text) < _MIN_BODY_CHARS:
        return None

    # 長すぎる場合は打ち切り(段落境界で切る)
    max_chars = config.FULLTEXT_MAX_CHARS
    truncated = False
    if len(text) > max_chars:
        cut = text.rfind("\n\n", 0, max_chars)
        if cut < int(max_chars * 0.5):
            cut = max_chars
        text = text[:cut].rstrip() + "\n\n[...本文はトークン上限のため以降を省略...]"
        truncated = True

    return {
        "text": text,
        "char_count": len(text),
        "sections": len(sections),
        "truncated": truncated,
    }


# 本文から除外するタグ(参考文献・表・図・数式・脚注など)
_SKIP_TAGS = {
    "ref-list", "table-wrap", "fig", "disp-formula", "inline-formula",
    "tex-math", "table", "graphic", "media", "supplementary-material",
    "fn-group", "back",
}


def _extract_body_sections(body: ET.Element) -> list:
    """<body>直下の<sec>を見出し付きで抽出。<sec>が無ければ段落を直接集める"""
    sections = []

    secs = body.findall("sec")
    if secs:
        for sec in secs:
            sections.append(_render_section(sec))
    else:
        # フラットな構造(<p>が直下に並ぶ)
        for p in body.findall("p"):
            sections.append(_node_text(p))

    return sections


def _render_section(sec: ET.Element, depth: int = 0) -> str:
    """1つの<sec>を「見出し: 本文」形式の文字列にする(ネスト対応)"""
    parts = []
    title = sec.find("title")
    if title is not None:
        heading = _node_text(title)
        if heading:
            parts.append(("#" * min(depth + 1, 3)) + " " + heading)

    for child in sec:
        tag = _localname(child.tag)
        if tag in _SKIP_TAGS:
            continue
        if tag == "sec":
            parts.append(_render_section(child, depth + 1))
        elif tag == "p":
            txt = _node_text(child)
            if txt:
                parts.append(txt)
        elif tag == "list":
            for item in child.findall(".//list-item"):
                t = _node_text(item)
                if t:
                    parts.append("- " + t)

    return "\n".join(p for p in parts if p.strip())


def _node_text(elem: ET.Element) -> str:
    """要素配下のテキストを連結。除外タグ配下は飛ばす"""
    pieces = []

    def walk(node: ET.Element):
        if _localname(node.tag) in _SKIP_TAGS:
            return
        if node.text:
            pieces.append(node.text)
        for child in node:
            walk(child)
            if child.tail:
                pieces.append(child.tail)

    walk(elem)
    return _normalize_whitespace("".join(pieces))


def _localname(tag: str) -> str:
    """名前空間を除いたローカルタグ名"""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _normalize_whitespace(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def attach_full_text(paper: Dict) -> Dict:
    """
    paperにPMC全文を付与する(in-place)。
    取得できれば paper["full_text"], paper["has_full_text"]=True を設定。
    """
    paper["has_full_text"] = False
    paper["full_text"] = ""

    if not paper.get("pmcid"):
        return paper

    result = fetch_full_text(paper["pmcid"])
    if result:
        paper["full_text"] = result["text"]
        paper["has_full_text"] = True
        paper["full_text_chars"] = result["char_count"]
        paper["full_text_truncated"] = result.get("truncated", False)

    return paper
