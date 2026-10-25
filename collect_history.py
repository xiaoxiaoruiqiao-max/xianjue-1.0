"""Cache public, timestamped CLS search archives for the historical review.

This collector does not publish scores or alter existing observations.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import hashlib
import html
import json
from pathlib import Path
import re
import time
import urllib.parse
import urllib.request

from update import DATA, TZ, now_cn, read_json, write_json

FOLDER = DATA / "backfill"
QUERIES = {
    "close": ("telegram", "收评"),
    "limits": ("telegram", "涨停分析"),
    "leaders": ("telegram", "连板股分析"),
    "morning": ("telegram", "早间新闻精选"),
    "focus": ("depth", "焦点复盘"),
    "global": ("depth", "环球市场"),
}


def plain(value):
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()


def search(category, keyword, page):
    params = {"app": "CailianpressWeb", "os": "web", "sv": "8.7.9"}
    canonical = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    sign = hashlib.md5(hashlib.sha1(canonical.encode()).hexdigest().encode()).hexdigest()
    url = "https://www.cls.cn/api/sw?" + urllib.parse.urlencode({**params, "sign": sign})
    body = {**params, "type": category, "keyword": keyword, "rn": 20, "page": page}
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={
        "User-Agent": "Mozilla/5.0", "Content-Type": "application/json",
        "Referer": "https://www.cls.cn/searchPage"})
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read()
    payload = json.loads(raw)
    if payload.get("errno") != 0:
        raise ValueError(f"Search failed: {payload.get('errno')}")
    rows = payload["data"][category].get("data") or []
    return {"url": url, "request": body, "retrieved_at": now_cn().isoformat(),
            "sha256": hashlib.sha256(raw).hexdigest(), "rows": rows}


def collect(name, start):
    category, keyword = QUERIES[name]
    all_rows, seen, pages = [], set(), 0
    error = None
    for page in range(120):
        path = FOLDER / "search" / f"{name}-{page:03}.json"
        cached = read_json(path)
        if cached is None:
            try:
                cached = search(category, keyword, page)
                write_json(path, cached)
                time.sleep(.2)
            except Exception as exc:
                error = f"page {page}: {type(exc).__name__}: {exc}"
                break
        pages += 1
        rows = cached["rows"]
        fresh = [r for r in rows if r["id"] not in seen]
        if not fresh:
            break
        for row in fresh:
            seen.add(row["id"])
            stamp = datetime.fromtimestamp(row["time"], TZ)
            if stamp.date().isoformat() >= start and stamp <= now_cn():
                all_rows.append({"id": row["id"], "published_at": stamp.isoformat(),
                                 "title": plain(row["title"]), "text": plain(row["descr"]),
                                 "url": f"https://www.cls.cn/detail/{row['id']}",
                                 "cache": path.relative_to(DATA).as_posix(),
                                 "retrieved_at": cached["retrieved_at"]})
        if min(r["time"] for r in rows) < datetime.fromisoformat(start).replace(tzinfo=TZ).timestamp():
            break
    result = {"query": keyword, "start": start, "rows": all_rows, "pages": pages,
              "fetched_at": now_cn().isoformat(), "error": error}
    write_json(FOLDER / f"{name}.json", result)
    return {"query": name, "rows": len(all_rows), "pages": pages, "error": error}


def article(row):
    path = FOLDER / "articles" / f"{row['id']}.json"
    if path.exists():
        return read_json(path)
    request = urllib.request.Request(row["url"], headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read()
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', raw.decode("utf-8"), re.S)
    if not match:
        raise ValueError("No article payload")
    item = json.loads(match[1])["props"]["pageProps"]["articleDetail"]
    stamp = datetime.fromtimestamp(item["ctime"], TZ).isoformat()
    if str(item["id"]) != str(row["id"]) or stamp != row["published_at"]:
        raise ValueError("Article identity/time mismatch")
    result = {**row, "title": plain(item["title"]), "text": plain(item["content"]), "cache": path.relative_to(DATA).as_posix(),
              "retrieved_at": now_cn().isoformat(), "sha256": hashlib.sha256(raw).hexdigest()}
    write_json(path, result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=(now_cn().date() - timedelta(days=375)).isoformat())
    parser.add_argument("--query", choices=list(QUERIES), action="append")
    parser.add_argument("--articles", choices=list(QUERIES))
    args = parser.parse_args()
    if args.articles:
        rows = read_json(FOLDER / f"{args.articles}.json")["rows"]
        def download(row):
            try:
                result = article(row)
                time.sleep(.2)
                return {"id": row["id"], "date": row["published_at"][:10], "characters": len(result["text"])}
            except Exception as exc:
                return {"id": row["id"], "date": row["published_at"][:10], "error": str(exc)}
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(download, rows))
        write_json(FOLDER / "article-status.json", results)
        print(json.dumps({"downloaded": sum("error" not in x for x in results), "errors": [x for x in results if "error" in x]}, ensure_ascii=False))
        raise SystemExit(0)
    names = args.query or list(QUERIES)
    with ThreadPoolExecutor(max_workers=2) as pool:
        for item in pool.map(lambda name: collect(name, args.start), names):
            print(json.dumps(item, ensure_ascii=False), flush=True)
