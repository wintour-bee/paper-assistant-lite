"""
文献搜索与入池工具（非事后审计工具）
用于在写作之前批量搜索验证文献，建立已验证文献池。

工作流：搜索 → 确认存在 → 提取元数据 → 入池 → 可供引用

使用方法：
    python verify_references.py <search_queries.txt>
    或导入为模块：from verify_references import build_reference_pool

注意：本工具应在写作阶段之前运行，不应作为"写完后再验证"的补救手段。
"""

import json
import re
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from typing import Optional


def extract_doi(text: str) -> Optional[str]:
    """从文本中提取 DOI"""
    pattern = r'10\.\d{4,}/[^\s]+'
    match = re.search(pattern, text)
    return match.group(0).rstrip('.') if match else None


def verify_by_doi(doi: str) -> Optional[dict]:
    """通过 Crossref API 验证 DOI 并获取元数据"""
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PaperAssistantLite/2.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            msg = data.get("message", {})
            authors = []
            for a in msg.get("author", []):
                family = a.get("family", "")
                given = a.get("given", "")
                authors.append(f"{family}, {given}" if family else given)
            return {
                "status": "verified",
                "source": "Crossref",
                "doi": doi,
                "title": msg.get("title", [""])[0],
                "authors": authors,
                "journal": msg.get("container-title", [""])[0] if msg.get("container-title") else "",
                "year": msg.get("published-print", {}).get("date-parts", [[None]])[0][0]
                         or msg.get("created", {}).get("date-parts", [[None]])[0][0],
                "type": msg.get("type", ""),
                "publisher": msg.get("publisher", ""),
            }
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"status": "not_found", "source": "Crossref", "doi": doi, "error": "DOI未在Crossref注册"}
        return {"status": "error", "source": "Crossref", "doi": doi, "error": str(e)}
    except Exception as e:
        return {"status": "error", "source": "Crossref", "doi": doi, "error": str(e)}


def verify_by_title(title: str, max_retries: int = 2) -> Optional[dict]:
    """通过 Semantic Scholar API 按标题验证文献"""
    encoded = urllib.parse.quote(title[:200])
    url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={encoded}&limit=3&fields=title,authors,year,journal,externalIds,publicationTypes"
    for attempt in range(max_retries + 1):
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                papers = data.get("data", [])
                if not papers:
                    return {"status": "not_found", "source": "Semantic Scholar", "query": title, "error": "未找到匹配文献"}
                best = papers[0]
                authors = [a.get("name", "") for a in best.get("authors", [])]
                return {
                    "status": "verified",
                    "source": "Semantic Scholar",
                    "title": best.get("title", ""),
                    "authors": authors,
                    "year": best.get("year"),
                    "journal": best.get("journal", {}).get("name", "") if best.get("journal") else "",
                    "external_ids": best.get("externalIds", {}),
                    "match_count": len(papers),
                    "citation_count": best.get("citationCount"),
                }
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries:
                time.sleep(3)
                continue
            return {"status": "error", "source": "Semantic Scholar", "query": title, "error": f"HTTP {e.code}"}
        except Exception as e:
            if attempt < max_retries:
                time.sleep(2)
                continue
            return {"status": "error", "source": "Semantic Scholar", "query": title, "error": str(e)}
    return None


def verify_reference(ref_text: str) -> dict:
    """
    对单条参考文献进行多源验证
    优先 DOI 验证，其次标题搜索
    """
    result = {
        "input": ref_text.strip(),
        "doi_found": None,
        "crossref_result": None,
        "s2_result": None,
        "final_status": "unverified",
        "verified_metadata": None,
    }

    # Step 1: 尝试提取 DOI
    doi = extract_doi(ref_text)
    if doi:
        result["doi_found"] = doi
        crossref = verify_by_doi(doi)
        result["crossref_result"] = crossref
        if crossref.get("status") == "verified":
            result["final_status"] = "verified"
            result["verified_metadata"] = crossref
            return result

    # Step 2: 按标题搜索
    title = ref_text.split(".")[0].split("]")[0]
    title = re.sub(r'^[\d\s\[\(\).,\-:;]+', '', title).strip()
    if len(title) > 10:
        s2 = verify_by_title(title)
        result["s2_result"] = s2
        if s2 and s2.get("status") == "verified":
            result["final_status"] = "verified_by_title"
            result["verified_metadata"] = s2
            return result

    result["final_status"] = "unverified"
    return result


def build_reference_pool(refs: list[str]) -> dict:
    """搜索验证文献列表并建立可引用文献池"""
    results = []
    pool = []
    not_found = []

    for i, ref in enumerate(refs):
        if not ref.strip():
            continue
        print(f"  [{i+1}/{len(refs)}] 搜索中...", end=" ")
        r = verify_reference(ref)
        results.append(r)
        if r["final_status"].startswith("verified"):
            pool.append(r)
            print(f"✅ 已入池: {r['verified_metadata'].get('title', '')[:60]}")
        else:
            not_found.append(ref)
            print(f"❌ 未找到，不入池")

    return {
        "total_queried": len(refs),
        "in_pool": len(pool),
        "not_found": len(not_found),
        "pool": pool,
        "not_found_queries": not_found,
        "results": results,
    }


def verify_reference_list(refs: list[str]) -> dict:
    """（已过时，请使用 build_reference_pool）"""
    return build_reference_pool(refs)


def format_report(verification: dict) -> str:
    """生成文献池建立报告"""
    lines = []
    lines.append("=" * 70)
    lines.append("  文献搜索入池报告")
    lines.append("=" * 70)
    lines.append(f"  搜索总计: {verification['total_queried']} 条")
    lines.append(f"  已入池（可用引用）: {verification['in_pool']} 条 ✅")
    lines.append(f"  未找到（不可引用）: {verification['not_found']} 条 ❌")
    lines.append("=" * 70)

    for i, r in enumerate(verification["results"]):
        status_icon = "✅ 已入池" if r["final_status"].startswith("verified") else "❌ 未入池"
        lines.append(f"\n[{i+1}] {status_icon}")
        lines.append(f"    搜索词: {r['input'][:100]}")
        if r["verified_metadata"]:
            meta = r["verified_metadata"]
            lines.append(f"    标题: {meta.get('title', 'N/A')}")
            if meta.get("authors"):
                lines.append(f"    作者: {', '.join(meta['authors'][:3])}")
            lines.append(f"    年份: {meta.get('year', 'N/A')}")
            lines.append(f"    来源: {meta.get('journal', 'N/A')}")
            if meta.get("doi"):
                lines.append(f"    DOI: {meta.get('doi', 'N/A')}")
        if r["final_status"] == "unverified":
            lines.append(f"    → 该文献无法在已知数据库中确认，不可用于引用。")

    lines.append(f"\n⚠️ 只有标记为'已入池'的文献才能被论文引用。")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python verify_references.py <search_queries.txt>")
        print("每行一个文献标题或搜索关键词")
        print("输出：可引用的文献池 + 无法确认的条目")
        sys.exit(1)

    with open(sys.argv[1], "r", encoding="utf-8") as f:
        refs = [line.strip() for line in f if line.strip()]

    print(f"开始搜索 {len(refs)} 条文献...\n")
    result = build_reference_pool(refs)
    print("\n" + format_report(result))
