"""Deterministic market reference, and evidence-gated eight-factor reviews."""
import math
from datetime import datetime, timedelta, timezone

DIMENSIONS = [
    ("trend", "指数趋势", 20), ("volume", "量价承接", 15),
    ("breadth", "赚钱效应", 15), ("sentiment", "短线情绪", 15),
    ("theme", "主线持续", 15), ("policy", "政策基本面", 10),
    ("external", "外部环境", 5), ("events", "事件风险", 5),
]
MODEL_VERSION = "price-volume-v1.0"


def mean(values):
    return sum(values) / len(values) if values else None


def trend_component(bars, date):
    history = [b for b in bars if b["date"] <= date]
    if len(history) < 21 or history[-1]["date"] != date:
        return None
    close = history[-1]["close"]
    closes = [b["close"] for b in history]
    ma5, ma20 = mean(closes[-5:]), mean(closes[-20:])
    previous_ma20 = mean(closes[-21:-1])
    ret5 = (close / closes[-6] - 1) * 100
    ret20 = (close / closes[-21] - 1) * 100
    points = (25 if close >= ma5 else 0) + (30 if close >= ma20 else 0)
    points += 25 if ma20 >= previous_ma20 else 0
    points += 20 if ret5 >= 0 else 0
    return {"points": points, "ma5": round(ma5, 2), "ma20": round(ma20, 2),
            "ret5": round(ret5, 2), "ret20": round(ret20, 2),
            "close": close, "change_pct": history[-1]["change_pct"]}


def quantitative(indices, date, phase="close"):
    keys = ["sh", "sz", "cyb"]
    parts = {k: trend_component(indices.get(k, []), date) for k in keys}
    if not all(parts.values()):
        return None
    trend = mean([parts[k]["points"] for k in keys])
    sh = {b["date"]: b for b in indices.get("sh", []) if b["date"] <= date}
    sz = {b["date"]: b for b in indices.get("sz", []) if b["date"] <= date}
    common = sorted(set(sh) & set(sz))
    if len(common) < 21:
        return None
    amounts = [sh[d]["turnover"] + sz[d]["turnover"] for d in common]
    total = amounts[-1]
    prev5, prev20 = mean(amounts[-6:-1]), mean(amounts[-21:-1])
    ratio5, ratio20 = total / prev5, total / prev20
    avg_change = mean([parts[k]["change_pct"] for k in keys])
    # Intraday volume must never be compared to a complete-day average.
    volume = None
    if phase == "close":
        if avg_change >= 0:
            volume = 100 if ratio5 >= 1.1 else 70 if ratio5 >= .9 else 40
        else:
            volume = 10 if ratio5 >= 1.1 else 25 if ratio5 >= .9 else 45
    score = round(.7 * trend + .3 * volume) if volume is not None else round(trend)
    return {"score": score, "model": MODEL_VERSION,
            "basis": "三指数趋势70% + 沪深量价30%" if phase == "close" else "午间趋势参考（量能不参与）",
            "components": parts, "trend_points": trend, "volume_points": volume,
            "turnover": total, "turnover_ratio5": round(ratio5, 3) if volume is not None else None,
            "turnover_ratio20": round(ratio20, 3) if volume is not None else None,
            "turnover_change": round((total / amounts[-2] - 1) * 100, 2) if volume is not None else None,
            "note": "当前规则按截至当日的行情回算；并非当时发布信号，不等同八维综合分。"}


def metric_conflicts(metrics):
    if all(k in metrics for k in ("limit_up", "broken", "seal_rate")):
        total = metrics["limit_up"] + metrics["broken"]
        if total and abs(100 * metrics["limit_up"] / total - metrics["seal_rate"]) > 1:
            return [f"来源统计冲突：{metrics['limit_up']}只涨停、{metrics['broken']}只炸板按家数计算封板率为"
                    f"{100 * metrics['limit_up'] / total:.2f}%，原文记为{metrics['seal_rate']:g}%。"
                    "超过整百分数取整差异；口径尚待来源澄清，不任选一个数字评分。"]
    return []


def validate_review(review, date, phase, strict=False):
    if review.get("date") != date or review.get("phase") != phase:
        raise ValueError("评估日期/时段不匹配")
    if phase not in ("midday", "close") or review.get("kind") not in ("observed", "retrospective"):
        raise ValueError("评估时段或记录类型无效")
    if not review.get("sources") or not review.get("asof") or not review.get("assessed_at"):
        raise ValueError("评估缺少来源或时点")
    asof = datetime.fromisoformat(review["asof"])
    assessed = datetime.fromisoformat(review["assessed_at"])
    if not asof.tzinfo or not assessed.tzinfo or assessed < asof:
        raise ValueError("评估时间需有时区且不早于资料时间")
    local_asof = asof.astimezone(timezone(timedelta(hours=8)))
    if local_asof.date().isoformat() != date:
        raise ValueError("评估时点必须属于所选北京时间交易日")
    clock = local_asof.strftime("%H:%M")
    if phase == "midday" and not "11:30" <= clock < "13:00":
        raise ValueError("午间评估必须使用北京时间11:30至13:00前的资料截止时点")
    if phase == "close" and clock < "15:00":
        raise ValueError("收盘评估不能使用收盘前的资料截止时点")
    ids = {s["id"] for s in review["sources"]}
    if len(ids) != len(review["sources"]):
        raise ValueError("来源编号重复")
    for source in review["sources"]:
        if not source.get("url", "").startswith(("https://", "http://")) or not source.get("title"):
            raise ValueError("来源缺少有效链接或标题")
        published = source.get("published_at")
        if published:
            stamp = datetime.fromisoformat(published)
            if not stamp.tzinfo or stamp > asof:
                raise ValueError("来源时间缺少时区或晚于本次评估资料截止时间")
        if strict and not published and not source.get("date"):
            raise ValueError("新发布来源必须注明发布日期")
        if source.get("date") and source["date"] > date:
            raise ValueError("来源日期晚于评估日期")
    seen = set()
    for row in review.get("dimensions", []):
        key = row["key"]
        if key in seen:
            raise ValueError("重复维度")
        seen.add(key)
        cap = next((v for k, _, v in DIMENSIONS if k == key), None)
        score = row.get("score")
        if cap is None or isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= cap:
            raise ValueError("维度分数不合法")
        if not row.get("reason") or not row.get("source_ids"):
            raise ValueError("维度缺少解释或来源")
        if not set(row["source_ids"]) <= ids:
            raise ValueError("维度引用不存在的来源")
    conditions = review.get("conditions", [])
    if len({c["key"] for c in conditions}) != len(conditions):
        raise ValueError("加仓条件重复")
    if any(c.get("status") not in ("pass", "fail", "unknown") for c in conditions):
        raise ValueError("加仓条件状态无效")
    if any(not set(c.get("source_ids", [])) <= ids for c in conditions):
        raise ValueError("加仓条件引用不存在的来源")
    if any(c["key"] not in ("seal", "breadth", "theme") for c in conditions):
        raise ValueError("加仓条件名称无效")
    if strict and any(c["status"] != "unknown" and (not c.get("source_ids") or not c.get("detail")) for c in conditions):
        raise ValueError("已核验加仓条件必须有逐项解释和来源")
    metrics = review.get("metrics", {})
    for key, cap in [("seal_rate", 100), ("continuation", 100), ("decline_ratio", 1)]:
        if key in metrics and (isinstance(metrics[key], bool) or not isinstance(metrics[key], (int, float)) or not math.isfinite(metrics[key]) or not 0 <= metrics[key] <= cap):
            raise ValueError("比例数据超出范围或无效")
    for key in ("limit_up", "limit_down", "broken", "up", "down"):
        if key in metrics and (type(metrics[key]) is not int or metrics[key] < 0):
            raise ValueError("家数数据必须是非负整数")
    if any(c["key"] == "seal" and c["status"] == "pass" for c in conditions) and metrics.get("seal_rate", 0) < 70:
        raise ValueError("封板率条件通过但缺少达标的量化证据")
    if strict and metric_conflicts(metrics):
        raise ValueError(metric_conflicts(metrics)[0])
    return len(seen) == len(DIMENSIONS)


def assess(review, date, phase, quant=None):
    complete = validate_review(review, date, phase) if review else False
    rows = {r["key"]: r for r in (review or {}).get("dimensions", [])}
    missing_notes = {row["key"]: row for row in (review or {}).get("missing", [])}
    conflicts = metric_conflicts((review or {}).get("metrics", {}))
    if conflicts:
        complete = False
        rows.pop("sentiment", None)
        missing_notes["sentiment"] = {"reason": conflicts[0], "status": "source_conflict"}
    dimensions = [{"key": k, "label": label, "max": cap, "score": rows.get(k, {}).get("score"),
                   "reason": rows.get(k, {}).get("reason", missing_notes.get(k, {}).get("reason", "缺少可核验的当日证据")),
                   "source_ids": rows.get(k, {}).get("source_ids", [])} for k, label, cap in DIMENSIONS]
    total = sum(r["score"] for r in dimensions) if complete else None
    metrics = (review or {}).get("metrics", {})
    gate = (metrics.get("decline_ratio", 0) >= .8 or
            (metrics.get("limit_down", 0) >= 40 and metrics.get("seal_rate", 100) < 60))
    if total is None:
        state, label, title = "missing", "待补评", "行情已更新，综合判断待补全"
    elif gate or total < 40:
        state, label, title = "defense", "防守", "防守为主，等待修复"
    elif total < 55:
        state, label, title = "observe", "观察", "先观察，等待条件改善"
    elif total < 70:
        state, label, title = "trial", "试仓", "允许有条件的小仓试探"
    else:
        state, label, title = "advance", "进攻", "环境改善，检查加仓条件"
    conditions = (review or {}).get("conditions", [])
    by_key = {c["key"]: c for c in conditions}
    checks = [{"key": k, "label": label, "status": by_key.get(k, {}).get("status", "unknown"),
               "detail": by_key.get(k, {}).get("detail", "待核验"),
               "source_ids": by_key.get(k, {}).get("source_ids", [])}
              for k, label in [("seal", "封板率回到70%以上"), ("breadth", "赚钱效应持续改善"),
                               ("theme", "主线回踩有承接")]]
    # Preserve old review files, but do not treat an unsourced historical pass as verified.
    for check in checks:
        if check["key"] == "seal" and conflicts:
            check["status"], check["detail"] = "unknown", conflicts[0]
        elif check["status"] == "pass" and not check["source_ids"]:
            check["status"] = "unknown"
            check["detail"] = "原记录未列出此条件的核验来源，待复核。原说明：" + check["detail"]
    risk_status = "triggered" if gate else "clear" if all(k in metrics for k in ("decline_ratio", "limit_down", "seal_rate")) else "unknown"
    add_allowed = complete and total >= 70 and not gate and all(c["status"] == "pass" for c in checks)
    reduce = "资料不足" if not complete else "优先检查减仓" if state == "defense" else "弱势持仓检查" if state == "observe" else "未触发市场减仓"
    return {"score": total, "complete": complete, "state": state, "label": label, "title": title,
            "coverage": sum(r["max"] for r in dimensions if r["score"] is not None), "dimensions": dimensions,
            "risk_gate": gate, "risk_status": risk_status, "data_conflicts": conflicts, "conditions": checks,
            "actions": {"open": "小仓试探" if state in ("trial", "advance") else "等待补评" if not complete else "暂缓",
                        "add": "可分批评估" if add_allowed else "暂停" if state == "defense" else "等待确认",
                        "reduce": reduce},
            "missing": [{"key": d["key"], "label": d["label"], "reason": d["reason"],
                         "status": missing_notes.get(d["key"], {}).get("status", "unassessed")}
                        for d in dimensions if d["score"] is None],
            "review_model": (review or {}).get("model", "manual-evidence-v1" if review else None),
            "research_note": (review or {}).get("research_note"),
            "reasons": [conflicts[0], "短线情绪维度暂停计分，综合分留空；原始评估继续保留供复核。",
                        "资料矛盾澄清前，暂缓据此作出开仓或加仓判断。"] if conflicts else
                       (review or {}).get("reasons", ["价格数据不包含完整的市场广度、主线和政策证据。", "先核验资料，不以量价参考分替代开仓许可。"]),
            "sources": (review or {}).get("sources", []), "asof": (review or {}).get("asof"),
            "assessed_at": (review or {}).get("assessed_at"), "quantitative": quant}
