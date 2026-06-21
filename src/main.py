"""
IBD Full-Text Bot - エントリーポイント(PMC全文専用)

PubMedで全文(PMC無料全文)が参照できるIBD関連論文だけを対象に、
本文を精読した詳細要約を生成し、既存の論文DB(Notion)へ1日1回投稿する。

フロー:
1. PubMed検索 ("pubmed pmc"[sb] で全文ありに限定、信頼度高めの研究タイプ)
2. フィルタ&スコアリング(全文ありを優先、足切り緩和)
3. スコア降順にソート
4. 各論文をNotion DBで重複チェック
5. PMC全文を取得(取得不可ならスキップ: FULLTEXT_ONLY=True)
6. 全文をClaude APIで詳細要約
7. Notion DBに投稿
"""
import sys
import time
import traceback

import config
import pubmed
import fulltext
import filter as paper_filter
import ai_summarize
import notion_writer


def main():
    print("=" * 60)
    print("IBD Full-Text Bot started (PMC全文専用)")
    print(f"  Search window : past {config.PUBMED_RELDATE_DAYS} days")
    print(f"  Max per run   : {config.PAPERS_PER_RUN_MAX}")
    print(f"  Full-text only: {config.FULLTEXT_ONLY}")
    print("=" * 60)

    # Step 1: PubMed検索
    try:
        pmids = pubmed.search_recent_papers()
    except Exception as e:
        print(f"[FATAL] PubMed search failed: {e}")
        sys.exit(1)

    if not pmids:
        print("[INFO] No papers found. Exit.")
        return

    # Step 2: 詳細取得
    try:
        papers = pubmed.fetch_paper_details(pmids)
        print(f"[PubMed] Fetched {len(papers)} paper details")
    except Exception as e:
        print(f"[FATAL] PubMed fetch failed: {e}")
        sys.exit(1)

    # Step 3: フィルタ & スコアリング(上限なし、スコア順全件返却)
    ranked_papers = paper_filter.filter_and_rank(papers)

    if not ranked_papers:
        print("[INFO] No papers passed filter. Exit.")
        return

    print(f"\n[INFO] {len(ranked_papers)} candidates after filter, "
          f"will process up to {config.PAPERS_PER_RUN_MAX} new papers\n")

    # 既存PMIDを一括取得してメモリ上で重複判定(重複チェックのAPIコストを排除)
    # → 投稿済みが何件あっても、重複は打ち切りカウントに含めないため、
    #   未投稿論文に必ず到達でき「渉猟範囲の固定化」による投稿停止を防ぐ。
    existing_pmids = notion_writer.get_existing_pmids()
    use_bulk_dedup = existing_pmids is not None
    if not use_bulk_dedup:
        print("[WARN] Bulk dedup unavailable, falling back to per-PMID checks")
        existing_pmids = set()

    # Step 4-6: スコア上位から順に新規論文を探し、PAPERS_PER_RUN_MAX件追加するまで継続
    stats = {"added": 0, "duplicates": 0, "errors": 0, "skipped_low_importance": 0,
             "examined": 0, "new_examined": 0, "fulltext": 0, "abstract_only": 0,
             "skipped_no_fulltext": 0}
    target = config.PAPERS_PER_RUN_MAX
    # 「重複を除いた実処理件数」の上限。重複はこのカウントに含めないので、
    # 投稿済み論文がいくら積み上がっても上限に達して止まることはない。
    new_scan_limit = max(target * 8, 40)

    for paper in ranked_papers:
        # 目標件数に達したら打ち切り
        if stats["added"] >= target:
            print(f"\n[INFO] Reached target {target} new papers, stop scanning")
            break

        # 安全装置: 「重複を除いた」実処理がこの件数に達したら打ち切り
        # (重複はカウントしないので、固定化による早期停止は起きない)
        if stats["new_examined"] >= new_scan_limit:
            print(f"\n[INFO] Examined {stats['new_examined']} non-duplicate candidates, "
                  f"stopping for safety")
            break

        stats["examined"] += 1
        pmid = paper["pmid"]
        title_short = paper["title"][:60]

        try:
            # 重複チェック(まずメモリ上の集合で判定。重複はAPIコスト・カウントともゼロ)
            is_dup = pmid in existing_pmids
            # 一括取得に失敗していた場合のみ、個別APIでフォールバック確認
            if not is_dup and not use_bulk_dedup:
                is_dup = notion_writer.is_pmid_exists(pmid)
                time.sleep(0.4)

            if is_dup:
                stats["duplicates"] += 1
                continue

            # ここから先は「新規(未投稿)論文」。実処理カウントを増やす
            stats["new_examined"] += 1

            # 全文(PMC)取得を試みる(全文があれば全文ベースで詳細要約)
            if paper.get("has_pmc"):
                fulltext.attach_full_text(paper)
                time.sleep(0.4)  # PubMed/PMC レート制限対策

            # 全文必須モードで全文が無ければスキップ
            if config.FULLTEXT_ONLY and not paper.get("has_full_text"):
                print(f"[SKIP-NOFT] [{stats['examined']:>3}] PMID {pmid}: 全文取得不可")
                stats["skipped_no_fulltext"] += 1
                continue

            src = "全文" if paper.get("has_full_text") else "abstract"
            if paper.get("has_full_text"):
                stats["fulltext"] += 1
            else:
                stats["abstract_only"] += 1

            # AI要約
            print(f"[SUMMARIZE] [{stats['examined']:>3}] PMID {pmid} "
                  f"(score {paper['score']:.1f}, {src}): {title_short}")
            summary = ai_summarize.summarize_paper(paper)
            if summary is None:
                print(f"  → summarize failed, skip")
                stats["errors"] += 1
                continue

            # 低Importanceスキップ判定
            importance = summary.get("importance", "★")
            if config.SKIP_LOW_IMPORTANCE and importance == "★":
                print(f"  → skipped (low importance ★)")
                stats["skipped_low_importance"] += 1
                continue

            # Notion投稿
            page_url = notion_writer.create_paper_page(paper, summary)
            if page_url:
                print(f"  → ADDED: {page_url} [Importance: {importance}]")
                stats["added"] += 1
                existing_pmids.add(pmid)  # 同一実行内の重複防止
            else:
                print(f"  → Notion create failed")
                stats["errors"] += 1

            # レート制限対策
            time.sleep(1)

        except Exception as e:
            print(f"[ERROR] PMID {pmid}: {e}")
            traceback.print_exc()
            stats["errors"] += 1
            continue

    # サマリ
    print("\n" + "=" * 60)
    print("Run summary:")
    print(f"  Scanned            : {stats['examined']}")
    print(f"  New (non-dup) exam : {stats['new_examined']}")
    print(f"  Added              : {stats['added']}")
    print(f"    - 全文ベース     : {stats['fulltext']}")
    print(f"    - abstractベース : {stats['abstract_only']}")
    print(f"  Duplicates skipped : {stats['duplicates']}")
    print(f"  No full text skip  : {stats['skipped_no_fulltext']}")
    print(f"  Low importance     : {stats['skipped_low_importance']}")
    print(f"  Errors             : {stats['errors']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
