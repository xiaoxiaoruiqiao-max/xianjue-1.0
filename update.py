"""Fetch public prices; preserve observations; build the local dashboard atomically."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys
import urllib.request

from scoring import assess, quantitative, MODEL_VERSION

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
TZ = timezone(timedelta(hours=8))
INDICES = {"sh": ("上证指数", "1.000001", "sh000001"),
           "sz": ("深证成指", "0.399001", "sz399001"),
           "cyb": ("创业板指", "0.399006", "sz399006")}


def now_cn():
    return datetime.now(TZ)


def read_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    os.replace(temp, path)


def fetch(url, referer="https://quote.eastmoney.com/"):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": referer})
    with urllib.request.urlopen(request, timeout=18) as response:
        return response.read()


def parse_kline(payload):
    rows = json.loads(payload)["data"]["klines"]
    result = []
    for row in rows:
        a = row.split(",")
        datetime.strptime(a[0], "%Y-%m-%d")
        bar = dict(zip(["date", "open", "close", "high", "low", "volume", "turnover", "amplitude", "change_pct"],
                       [a[0]] + [float(x) for x in a[1:9]]))
        validate_bar(bar)
        result.append(bar)
    if len(result) < 220 or len({b["date"] for b in result}) != len(result):
        raise ValueError("历史返回不完整或包含重复日期")
    return sorted(result, key=lambda x: x["date"])


def validate_bar(bar):
    keys = ("open", "close", "high", "low", "volume", "turnover", "change_pct")
    if any(not math.isfinite(bar[k]) for k in keys):
        raise ValueError("行情包含非有限数值")
    if any(bar[k] <= 0 for k in ("open", "close", "high", "low", "turnover")) or bar["volume"] < 0:
        raise ValueError("行情价格、成交量或成交额无效")
    if not bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]:
        raise ValueError("行情高低价与开收盘价矛盾")


def fetch_index(key, end):
    secid = INDICES[key][1]
    beg = (end - timedelta(days=465)).strftime("%Y%m%d")
    url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get?secid=" + secid +
           "&klt=101&fqt=0&beg=" + beg + "&end=" + end.strftime("%Y%m%d") +
           "&fields1=f1,f2,f3,f4,f5,f6&fields2=f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61")
    raw = fetch(url)
    bars = parse_kline(raw)
    return key, bars, {"url": url, "sha256": hashlib.sha256(raw).hexdigest(), "rows": len(bars)}, raw


def parse_quotes(raw):
    result = {}
    symbols = {v[2]: k for k, v in INDICES.items()}
    for symbol, body in re.findall(r'v_(\w+)="([^"]+)"', raw.decode("gb18030")):
        if symbol not in symbols:
            continue
        a = body.split("~")
        stamp = datetime.strptime(a[30], "%Y%m%d%H%M%S").replace(tzinfo=TZ)
        amount = a[35].split("/")
        result[symbols[symbol]] = {"date": stamp.date().isoformat(), "asof": stamp.isoformat(),
                                  "open": float(a[5]), "close": float(a[3]), "high": float(a[33]),
                                  "low": float(a[34]), "volume": float(a[6]),
                                  "turnover": float(amount[2]) if len(amount) == 3 else float(a[37]) * 10000,
                                  "change_pct": float(a[32])}
    if set(result) != set(INDICES):
        raise ValueError("实时行情缺少必要指数")
    for row in result.values():
        validate_bar(row)
    return result


def parse_sina(raw):
    result = {}
    symbols = {v[2]: k for k, v in INDICES.items()}
    for symbol, body in re.findall(r'hq_str_(\w+)="([^"]+)"', raw.decode("gb18030")):
        if symbol not in symbols:
            continue
        a = body.split(",")
        result[symbols[symbol]] = {"asof": a[30] + "T" + a[31] + "+08:00",
                                  "close": float(a[3]), "turnover": float(a[9])}
    return result


def phase_for(now):
    clock = now.strftime("%H:%M")
    if "11:30" <= clock < "13:00":
        return "midday"
    if clock >= "15:10":
        return "close"
    return None


def valid_current_quotes(quotes, now, phase):
    if not phase or set(quotes) != set(INDICES):
        return False
    for row in quotes.values():
        stamp = datetime.fromisoformat(row["asof"])
        if stamp.date() != now.date() or stamp > now:
            return False
        clock = stamp.strftime("%H:%M")
        if phase == "midday" and not "11:30" <= clock < "13:00":
            return False
        if phase == "close" and clock < "15:00":
            return False
    return True


def close_prices_agree(fetched, quotes, date):
    for key in INDICES:
        rows = [r for r in fetched.get(key, []) if r["date"] == date]
        if not rows or key not in quotes or abs(rows[-1]["close"] - quotes[key]["close"]) > .03:
            return False
    return True


def backup_quotes_agree(quotes, other):
    return set(quotes) == set(other) == set(INDICES) and all(
        all(math.isfinite(row[k][field]) and row[k][field] > 0
            for row in (quotes, other) for field in ("close", "turnover")) and
        abs(quotes[k]["close"] - other[k]["close"]) <= .03 and
        quotes[k]["turnover"] > 0 and abs(quotes[k]["turnover"] / other[k]["turnover"] - 1) <= .005
        for k in INDICES)


def make_record(indices, date, phase, prices_asof, retrieved_at, quotes=None, trade_days=None):
    adjusted = indices
    if phase == "midday":
        adjusted = {k: [r for r in indices[k] if r["date"] < date] + [quotes[k]] for k in INDICES}
    quant = quantitative(adjusted, date, phase)
    expected = set([d for d in (trade_days or []) if d <= date][-21:])
    if any(not expected.issubset({r["date"] for r in rows}) for rows in adjusted.values()):
        quant = None
    review = read_json(DATA / "reviews" / f"{date}-{phase}.json")
    assessment = assess(review, date, phase, quant)
    selected = quotes or {k: next(b for b in indices[k] if b["date"] == date) for k in INDICES}
    return {"date": date, "phase": phase, "prices_asof": prices_asof, "retrieved_at": retrieved_at,
            "quotes": {k: {**selected[k], "name": INDICES[k][0]} for k in INDICES},
            "metrics": (review or {}).get("metrics", {}), "review_kind": (review or {}).get("kind"), **assessment}


def build_dashboard(now=None):
    now = now or now_cn()
    cache = read_json(DATA / "indices.json", {})
    indices = cache.get("indices", {})
    cutoff = (now.date() - timedelta(days=365)).isoformat()
    calendar = read_json(DATA / "calendar.json", {})
    records = []
    if all(indices.get(k) for k in INDICES):
        dates = sorted(set.intersection(*({b["date"] for b in indices[k]} for k in INDICES)))
        for date in dates:
            if cutoff <= date <= now.date().isoformat():
                observed = cache.get("day_updates", {}).get(date, {})
                records.append(make_record(indices, date, "close", date + "T15:00:00+08:00", observed.get("fetched_at", cache.get("fetched_at")), trade_days=calendar.get("trade_days")))
                records[-1]["price_origin"] = observed.get("source", "东方财富指数日线")
        for path in sorted((DATA / "snapshots").glob("*-midday.json")):
            snapshot = read_json(path)
            date = snapshot["date"]
            if cutoff <= date <= now.date().isoformat():
                records.append(make_record(indices, date, "midday", snapshot["asof"], snapshot["fetched_at"], snapshot["quotes"], calendar.get("trade_days")))
                records[-1]["price_origin"] = "腾讯财经午间快照"
    records.sort(key=lambda r: (r["date"], r["phase"] == "close"))
    dates = sorted({r["date"] for r in records})
    current = now.date().isoformat()
    trade_days = sorted(set(calendar.get("trade_days", [])) | set(dates))
    result = {"app": "position-guide", "version": 1, "generated_at": now.isoformat(), "today": current,
              "history_start": cutoff, "calendar_end": calendar.get("end"), "trade_days": trade_days,
              "records": records, "latest_date": dates[-1] if dates else None,
              "coverage": {"trading_days": len(dates), "full_reviews": sum(r["complete"] for r in records),
                           "midday_snapshots": sum(r["phase"] == "midday" for r in records)},
              "price_source": {"name": "东方财富指数日线", "url": "https://quote.eastmoney.com/center/hszs.html"},
              "model": MODEL_VERSION, "schedule": read_json(DATA / "schedule.json", {"enabled": False}),
              "update": read_json(DATA / "update-status.json", {"state": "never"})}
    write_json(DATA / "dashboard.json", result)
    return result


def update(now=None):
    now = now or now_cn()
    DATA.mkdir(exist_ok=True)
    lock = DATA / "update.lock"
    # The OS releases this advisory lock even if a worker is interrupted.
    if os.name == "nt":
        import msvcrt
    else:
        import fcntl
    handle = lock.open("a+b")
    handle.seek(0)
    if lock.stat().st_size == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return {"state": "busy", "message": "已有更新正在运行"}
    previous = read_json(DATA / "update-status.json", {})
    write_json(DATA / "update-status.json", {**previous, "state": "running", "started_at": now.isoformat()})
    try:
        old = read_json(DATA / "indices.json", {"indices": {}})
        indices = dict(old["indices"])
        errors, provenance, warnings = [], [], []
        fetched = {}
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {k: pool.submit(fetch_index, k, now.date()) for k in INDICES}
            for key, future in futures.items():
                try:
                    k, bars, source, raw = future.result()
                    fetched[k] = bars
                    provenance.append({"key": k, **source})
                    raw_path = DATA / "raw" / now.strftime("%Y%m%d-%H%M%S") / f"{k}.json"
                    raw_path.parent.mkdir(parents=True, exist_ok=True)
                    raw_path.write_bytes(raw)
                except Exception as exc:
                    errors.append(f"{INDICES[key][0]}：{type(exc).__name__} {str(exc)[:180]}")
        phase = phase_for(now)
        cal = read_json(DATA / "calendar.json", {})
        today = now.date().isoformat()
        if len(fetched) == len(INDICES):
            date_sets = [{r["date"] for r in fetched[k]} for k in INDICES]
            if any(dates != date_sets[0] for dates in date_sets[1:]):
                errors.append("三指数历史日期不一致，保留原缓存")
                fetched = {}
            expected = [d for d in cal.get("trade_days", []) if d < today or (d == today and phase == "close")]
            if fetched and expected and today <= cal.get("end", "") and max(date_sets[0]) < max(expected):
                errors.append(f"历史源未覆盖最近已收盘交易日 {max(expected)}，保留原缓存")
                fetched = {}
        quote_date, quote_phase, quote_now = today, phase, now
        if today <= cal.get("end", "") and today not in cal.get("trade_days", []):
            previous_days = [d for d in cal.get("trade_days", []) if d < today]
            if previous_days:
                quote_date, quote_phase = max(previous_days), "close"
                quote_now = datetime.fromisoformat(quote_date + "T23:59:59+08:00")
        current_ok, quotes = False, {}
        try:
            quote_raw = fetch("https://qt.gtimg.cn/q=" + ",".join(v[2] for v in INDICES.values()))
            quotes = parse_quotes(quote_raw)
            current_ok = valid_current_quotes(quotes, quote_now, quote_phase)
        except Exception as exc:
            errors.append(f"时点核验：{type(exc).__name__} {str(exc)[:180]}")
        backup_used = False
        if len(fetched) < len(INDICES) and current_ok and all(indices.get(k) for k in INDICES):
            try:
                backup_url = "https://hq.sinajs.cn/list=" + ",".join(v[2] for v in INDICES.values())
                backup_raw = fetch(backup_url, "https://finance.sina.com.cn/")
                backup = parse_sina(backup_raw)
                if not valid_current_quotes(backup, quote_now, quote_phase) or not backup_quotes_agree(quotes, backup):
                    raise ValueError("备用来源时点、点位或成交额未核对一致")
                saved_close = {k: next((r for r in indices[k] if r['date'] == quote_date), None) for k in INDICES}
                keep_saved_close = quote_date < today and all(saved_close.values())
                if keep_saved_close and not backup_quotes_agree(quotes, saved_close):
                    raise ValueError("最近交易日报价与已保存历史不一致，保留原数据待核验")
                if quote_phase == "close" and not keep_saved_close:
                    for key in INDICES:
                        by_date = {r["date"]: r for r in indices[key]}
                        by_date[quote_date] = quotes[key]
                        indices[key] = sorted(by_date.values(), key=lambda r: r["date"])
                    day_updates = {**old.get("day_updates", {}), quote_date: {"fetched_at": now.isoformat(), "source": "腾讯财经 / 新浪财经交叉核验"}}
                    write_json(DATA / "indices.json", {**old, "indices": indices, "day_updates": day_updates})
                raw_dir = DATA / "raw" / now.strftime("%Y%m%d-%H%M%S")
                raw_dir.mkdir(parents=True, exist_ok=True)
                (raw_dir / "tencent.txt").write_bytes(quote_raw)
                (raw_dir / "sina.txt").write_bytes(backup_raw)
                warnings = errors + [f"主历史源暂不可用；保留已有历史，{quote_date}报价已通过腾讯/新浪交叉核验"]
                errors = []
                backup_used = True
            except Exception as exc:
                current_ok = False
                errors.append(f"备用源：{type(exc).__name__} {str(exc)[:180]}")
        if current_ok and quote_phase == "close" and not backup_used and not close_prices_agree(fetched, quotes, quote_date):
            current_ok = False
            errors.append("两处来源的收盘点位未核对一致，暂不发布新的当日收盘数据")
        if len(fetched) == len(INDICES):
            for key, bars in fetched.items():
                # Never convert an afternoon observation into a missing noon snapshot.
                allowed = [b for b in bars if b["date"] < today or
                           (b["date"] == today and current_ok and phase == "close")]
                merged = {r["date"]: r for r in indices.get(key, [])}
                merged.update({r["date"]: r for r in allowed})
                indices[key] = sorted(merged.values(), key=lambda r: r["date"])
            day_updates = dict(old.get("day_updates", {}))
            for date in {b["date"] for b in allowed}:
                if date in day_updates:
                    day_updates[date] = {"fetched_at": now.isoformat(), "source": "东方财富指数日线"}
            write_json(DATA / "indices.json", {**old, "indices": indices, "fetched_at": now.isoformat(),
                                               "sources": provenance, "day_updates": day_updates})
        if current_ok and quote_phase == "midday":
            path = DATA / "snapshots" / f"{today}-midday.json"
            # A scheduled second attempt may recover a failure, but not rewrite an observed noon.
            if not path.exists():
                write_json(path, {"date": today, "phase": phase, "quotes": quotes,
                                  "asof": min(q["asof"] for q in quotes.values()), "fetched_at": now.isoformat()})
        if phase and today in cal.get("trade_days", []) and not current_ok:
            errors.append("未取得当前交易日且时段匹配的报价；保留旧数据")
        state = "success" if not errors and (len(fetched) == len(INDICES) or backup_used) else "partial" if fetched else "failed"
        status = {"state": state, "attempted_at": now.isoformat(), "finished_at": now_cn().isoformat(),
                  "last_success": now.isoformat() if state == "success" else previous.get("last_success"),
                  "phase": phase, "errors": errors, "warnings": warnings, "backup_used": backup_used,
                  "current_quotes_verified": current_ok,
                  "verified_quote_date": quote_date if current_ok else None,
                  "message": "行情更新完成；综合评估单独核验" if state == "success" else "数据未全部更新，已保留可用历史"}
        write_json(DATA / "update-status.json", status)
        result = build_dashboard(now)
        return {**status, "coverage": result["coverage"], "latest_date": result["latest_date"]}
    except Exception as exc:
        status = {**previous, "state": "failed", "attempted_at": now.isoformat(), "errors": [f"{type(exc).__name__}: {exc}"]}
        write_json(DATA / "update-status.json", status)
        raise
    finally:
        handle.seek(0)
        if os.name == "nt":
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="只根据本地行情和核验记录重建页面数据")
    args = parser.parse_args()
    try:
        output = build_dashboard() if args.offline else update()
        if args.offline:
            output = {k: output[k] for k in ["coverage", "latest_date", "generated_at"]}
        print(json.dumps(output, ensure_ascii=False))
    except Exception as exc:
        print(f"更新失败，原有页面数据保留：{exc}", file=sys.stderr)
        sys.exit(1)
