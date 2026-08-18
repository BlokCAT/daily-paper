# -*- coding: utf-8 -*-
"""
每日论文推送脚本
每天选一篇经典/里程碑论文（按 渲染 → 3DGS → 重建 三类轮流），
抓取摘要 -> DeepSeek 翻译+总结 -> 附上今日 arXiv 新论文速递 -> 发邮件。
运行环境: GitHub Actions (定时) 或本地 (python main.py --test 本地试跑不寄信)
"""
import os
import sys
import re
import json
import ssl
import smtplib
import difflib
import xml.etree.ElementTree as ET
from datetime import date
from email.message import EmailMessage
from pathlib import Path

import time

import requests

# Windows 本地控制台默认 GBK，打印 emoji 会报错，统一转 UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = Path(__file__).resolve().parent
STATE_PATH = BASE / "state.json"
ARXIV_API = "http://export.arxiv.org/api/query"
S2_API = "https://api.semanticscholar.org/graph/v1/paper/search"
CROSSREF_API = "https://api.crossref.org/works"
DEEPSEEK_API = "https://api.deepseek.com/chat/completions"

CATS = ["rendering", "gaussian", "reconstruction"]
CAT_CN = {"rendering": "渲染", "gaussian": "3DGS", "reconstruction": "重建"}
EPOCH = date(2025, 1, 1)  # 用于按天轮换分类


def log(msg):
    print(msg, flush=True)


# ---------- 论文元数据获取 ----------

def arxiv_fetch(arxiv_id):
    """按 arXiv ID 拿标题和摘要。失败返回 (None, None)。"""
    for attempt in range(2):
        try:
            r = requests.get(ARXIV_API, params={"id_list": arxiv_id, "max_results": 1}, timeout=30)
            r.raise_for_status()
            ns = {"a": "http://www.w3.org/2005/Atom"}
            entry = ET.fromstring(r.text).find("a:entry", ns)
            if entry is None:
                return None, None
            title = " ".join(entry.find("a:title", ns).text.split())
            summary = " ".join(entry.find("a:summary", ns).text.split())
            return title, summary
        except Exception as e:
            log(f"[warn] arXiv 抓取失败第{attempt+1}次 ({arxiv_id}): {e}")
            time.sleep(3)
    return None, None


def s2_fetch(title_query):
    """按标题在 Semantic Scholar 搜索（老论文没有 arXiv 时的兜底）。带重试。"""
    for attempt in range(3):
        try:
            r = requests.get(S2_API, params={
                "query": title_query, "limit": 1,
                "fields": "title,abstract,year,venue,url",
            }, timeout=30)
            if r.status_code == 429:  # 限流，等一会儿重试
                log(f"[warn] Semantic Scholar 限流(429)，第{attempt+1}次，等待重试")
                time.sleep(8 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json().get("data") or []
            if not data:
                return None, None, None
            p = data[0]
            return p.get("title"), p.get("abstract"), p.get("url")
        except Exception as e:
            log(f"[warn] Semantic Scholar 搜索失败第{attempt+1}次: {e}")
            time.sleep(5)
    return None, None, None


def crossref_fetch(title_query):
    """按标题在 Crossref 搜索（老论文的 DOI 兜底，免费且不限流）。"""
    try:
        r = requests.get(CROSSREF_API, params={
            "query.bibliographic": title_query, "rows": 1,
        }, headers={"User-Agent": "daily-paper-bot/1.0 (mailto:blencatlar@outlook.com)"}, timeout=30)
        r.raise_for_status()
        items = r.json().get("message", {}).get("items", [])
        if not items:
            return None, None, None
        it = items[0]
        title = (it.get("title") or [None])[0]
        abstract = it.get("abstract") or ""
        abstract = re.sub(r"<[^>]+>", " ", abstract).strip()  # 去掉 XML 标签
        url = it.get("URL")
        if not url and it.get("DOI"):
            url = "https://doi.org/" + it["DOI"]
        return title, abstract or None, url
    except Exception as e:
        log(f"[warn] Crossref 搜索失败: {e}")
        return None, None, None


def titles_match(a, b, threshold=0.35):
    """比较两个标题是否指同一篇论文（防 arXiv 编号写错时抓到别的论文）。"""
    def norm(t):
        return re.sub(r"[^a-z0-9]+", "", t.lower())
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio() >= threshold


def fetch_paper(paper):
    """拿一篇论文的标题+摘要+链接。返回 (标题, 摘要, 链接)，抓不到时对应项为 None。
    顺序：arXiv → Semantic Scholar → Crossref，每级都校验标题是否匹配。"""
    if paper.get("arxiv"):
        title, abstract = arxiv_fetch(paper["arxiv"])
        if title and titles_match(title, paper["title"]):
            return title, abstract, f"https://arxiv.org/abs/{paper['arxiv']}"
        if title:
            log(f"[warn] arXiv 返回标题与论文库不符，已忽略: {title[:60]}...")
        else:
            log(f"[warn] arXiv 未返回结果 ({paper['arxiv']})，转 Semantic Scholar")
    title, abstract, url = s2_fetch(paper["title"])
    if title and titles_match(title, paper["title"]):
        return title, abstract, url
    title, abstract, url = crossref_fetch(paper["title"])
    if title and titles_match(title, paper["title"]):
        return title, abstract, url
    return None, None, None


# ---------- DeepSeek 翻译 + 总结 ----------

def deepseek_process(title, abstract):
    """一次调用：翻译摘要 + 3 条核心贡献总结。失败返回 None。"""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        log("[warn] 未设置 DEEPSEEK_API_KEY，跳过翻译")
        return None
    prompt = f"""你是计算机图形学方向的学术助手。请完成两件事：
1. 把下面论文的英文摘要翻译成专业、流畅的中文；
2. 用 3 条要点总结论文的核心贡献（中文，每条不超过 40 字）。

标题: {title}

摘要:
{abstract}

严格按以下格式输出：
【摘要翻译】
<中文翻译>
【核心贡献】
1. <要点1>
2. <要点2>
3. <要点3>"""
    try:
        r = requests.post(DEEPSEEK_API, headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }, json={
            "model": "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
            "max_tokens": 3000,
        }, timeout=180)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        log(f"[warn] DeepSeek 调用失败: {e}")
        return None


# ---------- 今日新论文速递 ----------

def recent_arxiv(n=5):
    """arXiv 上最近提交的相关论文，只列标题+链接。"""
    query = ('(cat:cs.CV OR cat:cs.GR) AND (abs:"gaussian splatting" '
             'OR abs:"point cloud" OR abs:"3D reconstruction" '
             'OR abs:"neural rendering" OR abs:"path tracing")')
    try:
        r = requests.get(ARXIV_API, params={
            "search_query": query,
            "sortBy": "submittedDate", "sortOrder": "descending",
            "max_results": n,
        }, timeout=30)
        r.raise_for_status()
        ns = {"a": "http://www.w3.org/2005/Atom"}
        out = []
        for entry in ET.fromstring(r.text).findall("a:entry", ns):
            title = " ".join(entry.find("a:title", ns).text.split())
            link = entry.find("a:id", ns).text.strip()
            out.append((title, link))
        return out
    except Exception as e:
        log(f"[warn] 新论文速递抓取失败: {e}")
        return []


# ---------- 状态管理 ----------

def load_state():
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {"rendering_idx": 0, "gaussian_idx": 0, "reconstruction_idx": 0}


def save_state(state):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def pick_today_paper(papers, state):
    """按天轮换分类：day % 3 -> 渲染/3DGS/重建，再按该分类的队列取下一篇。"""
    day = (date.today() - EPOCH).days
    cat = CATS[day % 3]
    idx = state.get(f"{cat}_idx", 0)
    pool = papers[cat]
    paper = pool[idx % len(pool)]
    state[f"{cat}_idx"] = idx + 1
    return cat, paper


# ---------- 邮件 ----------

def build_email(cat, paper, title, abstract, ai_text, recents, url):
    subject = f"【每日论文·{CAT_CN[cat]}】{title[:80]}"
    lines = [
        f"📄 {title}",
        f"🏷 分类：{CAT_CN[cat]} ｜ 出处：{paper.get('venue', '')} {paper.get('year', '')}",
    ]
    if url:
        lines.append(f"🔗 链接: {url}")
    elif paper.get("arxiv"):
        lines.append(f"🔗 链接: https://arxiv.org/abs/{paper['arxiv']}")
    lines += [
        "",
        "【为什么值得读】",
        paper.get("why", ""),
        "",
        "【英文摘要】",
        abstract if abstract else ("（摘要获取失败，请通过上面的链接查看）" if url else "（摘要获取失败，可按标题自行搜索）"),
        "",
    ]
    if ai_text:
        lines.append("【DeepSeek 翻译与总结】")
        lines.append(ai_text)
        lines.append("")
    if recents:
        lines += ["【今日新论文速递】(arXiv 最近提交)", ""]
        for t, u in recents:
            lines.append(f"- {t}")
            lines.append(f"  {u}")
            lines.append("")
    lines.append("—— 每日论文推送机器人（GitHub Actions 免费运行）")
    return subject, "\n".join(lines)


def send_email(subject, body):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["TO_EMAIL"]
    msg.set_content(body)
    ctx = ssl.create_default_context()
    with smtplib.SMTP_SSL(os.environ["SMTP_HOST"],
                          int(os.environ.get("SMTP_PORT", "465")),
                          context=ctx) as s:
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg)


# ---------- 主流程 ----------

def main():
    test_mode = "--test" in sys.argv
    papers = json.loads((BASE / "papers.json").read_text(encoding="utf-8"))
    state = load_state()

    cat, paper = pick_today_paper(papers, state)
    log(f"今日分类: {CAT_CN[cat]} | 论文: {paper['title']}")

    title, abstract, url = fetch_paper(paper)
    if title is None:
        title, abstract, url = paper["title"], None, None
        log("[warn] 摘要获取失败，邮件将只含题录信息（不含外部链接）")

    ai_text = deepseek_process(title, abstract) if abstract else None
    recents = recent_arxiv()
    subject, body = build_email(cat, paper, title, abstract, ai_text, recents, url)

    if test_mode:
        print("=" * 60)
        print(subject)
        print("=" * 60)
        print(body)
        print("=" * 60)
        print("[test] 本地试跑完成，未发送邮件、未推进队列")
        return

    send_email(subject, body)
    save_state(state)
    log(f"✅ 已发送: {subject}")


if __name__ == "__main__":
    main()
