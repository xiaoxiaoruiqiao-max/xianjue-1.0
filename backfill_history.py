"""Reconstruct end-of-day reviews from dated evidence, never future returns.

Historical news factors use the disclosed history-news-v1 rubric. They are
research proxies, not replicas of a human judgement published on that day.
"""
import argparse
from collections import Counter
from datetime import datetime
import re

from scoring import DIMENSIONS, quantitative, validate_review
from update import DATA, read_json, write_json, now_cn, build_dashboard

VERSION = "history-news-v1"
FOLDER = DATA / "backfill"


def archive(name, pattern=None):
    rows = read_json(FOLDER / f"{name}.json", {}).get("rows", [])
    result = {}
    for row in rows:
        if pattern and not re.search(pattern, row["text"] or row["title"]):
            continue
        if row["published_at"][11:19] > "18:00:00":
            continue
        date = row["published_at"][:10]
        if date not in result or row["published_at"] > result[date]["published_at"]:
            result[date] = row
    return result


def sentences(text):
    return [s.strip() for s in re.split(r"[。；\n]", text) if s.strip()]


def breadth(text):
    # Keep rounded news counts as rounded evidence, never exact metric counts.
    patterns = [r"(?:全市场|两市|沪深两市)[^。；]{0,20}?(超过|超|逾|近|约)?(\d{4})[余多]?(?:只|家)?(?:个股|股票|股)?(上涨|下跌|下挫|收涨|收跌|飘红)",
                r"(超过|超|逾|近|约)(\d{4})(?:只|家)?(?:个股|股票|股)(上涨|下跌|下挫|收涨|收跌|飘红)"]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            qualifier, count, direction = match.groups()
            count = int(count)
            up = direction in ("上涨", "收涨", "飘红")
            # '近' is not a lower bound. Use the next lower hundred for bins.
            conservative = count - 100 if qualifier == "近" else count
            thresholds = [(4500, 15 if up else 1), (4000, 13 if up else 3),
                          (3000, 10 if up else 5), (2500, 8 if up else 7), (1500, 6 if up else 9)]
            score = next((points for level, points in thresholds if conservative >= level), None)
            if score is not None:
                return {"score": score, "direction": "up" if up else "down", "count": count,
                        "qualifier": qualifier or "", "evidence": match[0]}
    return None


def number(pattern, text):
    match = re.search(pattern, text)
    return float(match[1]) if match else None


def sentiment(limits, leaders):
    if not limits or not leaders:
        return None
    seal = number(r"封板率(?:为)?(\d+(?:\.\d+)?)%", limits["text"])
    promotion = number(r"连板股晋级率(?:为|达|达到)?(\d+(?:\.\d+)?)%", leaders["text"])
    if seal is None or promotion is None:
        return None
    seal_score = 8 if seal >= 80 else 6 if seal >= 70 else 4 if seal >= 60 else 2
    promotion_score = 7 if promotion >= 60 else 5 if promotion >= 40 else 3 if promotion >= 25 else 1
    result = {"score": seal_score + promotion_score, "seal_rate": seal, "continuation": promotion,
            "limit_up": number(r"(?:共|有)(\d+)股涨停", limits["text"]),
            "broken": number(r"(\d+)股封板未遂", limits["text"])}
    if result['limit_up'] is not None and result['broken'] is not None:
        total = result['limit_up'] + result['broken']
        if total and abs(100 * result['limit_up'] / total - seal) > 1:
            return None
    return result


THEMES = {
    "AI算力": r"算力|光通信|光模块|CPO|PCB|液冷|服务器|光纤|数据中心|算电|AI硬件|铜缆|谷歌产业链",
    "半导体": r"半导体|芯片|光刻|存储|先进封装|CPU|碳化硅|MicroLED|Micro LED",
    "机器人": r"机器人|具身智能|物理AI",
    "AI应用": r"AI应用|人工智能应用|大模型|智能体|AI医疗|AI教育|AI漫剧|AI视频",
    "商业航天": r"商业航天|卫星|航天",
    "新能源": r"锂电|锂矿|电池|储能|光伏|风电|碳酸锂|固态电池",
    "农业": r"农业|种业|种植|粮食|化肥|养殖|猪肉",
    "资源周期": r"煤炭|油气|石油|有色|稀土|黄金|化工|钢铁|贵金属|玻纤|电子布|工业气体|顺周期",
    "消费": r"消费|零售|食品|饮料|旅游|白酒|免税",
    "金融地产": r"银行|证券|券商|保险|金融|地产",
    "医药": r"医药|创新药|医疗|中药|CRO|CXO",
    "汽车": r"汽车|无人驾驶|智能驾驶|汽配|车路云",
    "军工": r"军工|军贸|国防",
    "传媒": r"传媒|短剧|游戏|影视|出版",
    "电力": r"电力|特高压|电网|核电",
    "自主可控": r"自主可控|国产替代|信创|华为|鸿蒙|操作系统",
    "区域改革": r"福建|海峡|海南|自贸|深圳|上海|国企改革|并购重组",
    "其他新产业": r"量子|核聚变|低空|脑机|数据要素|数字货币|6G",
}
POSITIVE = r"强势|走强|活跃|领涨|爆发|反弹|回流|上涨|大涨|涨停|修复|加强|扩散|拉升|异军突起|多点开花|亮眼|卷土重来|涨幅居前|占据主导|补涨|一枝独秀|强者恒强|持续发酵|继续主导|资金坚守|热门板块"
NEGATIVE = r"下跌|下挫|领跌|杀跌|退潮|走弱|调整|重挫|大跌|跌停|分化|分歧|冲高回落|高开低走"


def theme_observation(row):
    if not row:
        return None
    body = row["text"].split("板块方面，")[-1]
    if body == row["text"]:
        body = row["text"].split("个股方面，")[-1]
    active = []
    weak = []
    for clause in re.split(r"[；。，]", body):
        # Only assess the opening sector statement; individual-stock gains in
        # a falling sector must not turn it into a positive sector observation.
        headline = clause.split("，")[0]
        if len(headline) > 70:
            headline = headline[:70]
        if not re.search(r"板块|概念|方向|产业|题材|行业|赛道|应用端|AI硬件|算力硬件|科技股|顺周期|周期股|电子布|工业气体|光通信|光纤|液冷|CPO|PCB|MLCC", headline):
            continue
        if re.search(r"或将|有望|预计|明日|下周|可能", headline):
            continue
        names = [name for name, pattern in THEMES.items() if re.search(pattern, headline)]
        if re.search(NEGATIVE, headline):
            weak.extend(names)
        elif re.search(POSITIVE, headline):
            active.extend(names)
    return {"active": sorted(set(active)), "weak": sorted(set(weak)), "row": row}


def policy_observation(row):
    if not row:
        return None
    bullets = re.split(r"\n\s*\d+[、.]", row["text"])[1:]
    authority = r"人民银行|央行|国务院|中共中央|中央经济|中央政治|财政部|证监会|工信部|发改委|国家统计局|商务部|国家发展改革委|习近平|李强|吴清|潘功胜|中办|国办|国家能源局|科技部|金融监管总局|国家数据局|全国人大|网络安全审查办公室|深圳市发布|省.*印发|上海.*印发|市政府常务会议"
    foreign = r"美国|美联储|日本央行|欧洲央行|英国央行|印度央行|韩国央行|俄罗斯央行"
    facts = [b for b in bullets if re.search(authority, b) and not re.search(foreign, b[:24])
             and not re.search(r"公告.*(?:立案|罚款|行政处罚)", b)]
    if not facts:
        return None
    text = "\n".join(facts)
    # Plans/expectations do not receive the implemented-easing bonus.
    easing = [b for b in facts if re.search(r"降准|降息|下调[^。]{0,20}(?:利率|准备金率)|恢复公开市场国债买卖", b)
              and not re.search(r"预期|预计|可能|择机|适时|研究|空间|未|不降|美联储|美国|日本|欧洲", b)]
    support = [b for b in facts if re.search(r"支持|促进|提振|扩大|宽松|稳市|稳增长|增长|保障|提升|加快|推动|降低", b)]
    constraint = [b for b in facts if re.search(r"加征|暂停进口|下滑|下降|收缩|不及|严格限制|严重侵害|产供链危机", b)
                  and not re.search(r"取消[^。]{0,12}加征|暂停[^。]{0,12}加征|降幅收窄", b)]
    score = 8 if easing else 6 if support else 5
    if constraint:
        score = max(2, score - 2)
    pmi = re.search(r"(?:制造业采购经理指数|制造业PMI|制造业采购经理人指数)[^。]{0,16}?(\d{2}\.\d)%?", text)
    if pmi:
        score = min(score, 4) if float(pmi[1]) < 50 else max(score, 6)
    labels = []
    if easing: labels.append("已宣布的货币宽松措施")
    if support: labels.append("稳增长、产业或市场支持表述")
    if constraint: labels.append("同时出现限制或基本面走弱信息")
    if not labels: labels.append("国内政策或宏观事实，方向未明确")
    return {"score": score, "reason": "；".join(labels) + "。仅评价当日已公开政策叙事，不等于盈利兑现。",
            "evidence": facts, "row": row}


def external_observation(row):
    if not row:
        return None
    text = row["text"]
    match = re.search(r"(?:纳斯达克(?:综合)?指数|纳指)(?:数)?(?:收盘|收|大)?(涨|跌)(\d+(?:\.\d+)?)%", text)
    if not match:
        return None
    change = float(match[2]) * (1 if match[1] == "涨" else -1)
    score = 5 if change >= 1 else 4 if change >= 0 else 3 if change > -1 else 2 if change > -2 else 1
    return {"score": score, "change": change, "row": row}


def event_observation(morning, focus):
    # An empty risk-keyword scan is NOT evidence of low event risk.
    risks = []
    for row in [morning, focus]:
        if not row:
            continue
        for sentence in sentences(row["text"]):
            if re.search(r"公告|披露|公司|股东|减持|业绩|停牌|退市|净利|概念|关税|战争|空袭|冲突|停摆|伊朗|以色列", sentence) and re.search(
                    r"(?:拟|计划|已|累计)[^。]{0,24}减持|立案|留置|欺诈|终止上市|退市|亏损|亏[^。]{0,8}亿元|同比转亏|净利[^。]{0,24}下降|利润[^。]{0,24}下降|业绩[^。]{0,12}不及预期|停牌核查|停牌预警|终止收购|终止重组|终止筹划|风险提示|暂无[^。]{0,15}业务|尚未实现|不涉及|未涉及|尚未开展|不生产|不存在[^。]{0,24}计划|无[^。]{0,12}订单|占比[^。]{0,8}低|解禁|关税|战争|空袭|冲突|停摆|重大不确定性|违法|军事打击|进口限制|禁止出口|提起公诉", sentence):
                if not re.search(r"撤销退市风险|解除退市风险|不存在退市|否认|澄清", sentence):
                    risks.append((sentence, row))
    if not risks:
        return None
    severe = any(re.search(r"立案调查|停牌核查|退市风险|业绩[^。]{0,12}不及预期", s) for s, _ in risks)
    score = 2 if severe or len(risks) >= 3 else 3
    return {"score": score, "risks": risks,
            "reason": f"当日新闻窗口发现{len(risks)}条公司、贸易或重大事件线索；" +
                      ("含立案、停牌核查、退市或业绩不及预期事项。" if severe else "需避开对应公司事件暴露。") +
                      "这是新闻风险观察，未完成全市场公告与解禁穷尽核查。"}


def make_reviews():
    indices = read_json(DATA / "indices.json")["indices"]
    dashboard = read_json(DATA / "dashboard.json")
    dates = sorted({r["date"] for r in dashboard["records"] if r["phase"] == "close"})
    close = archive("close", r"^【收评[：:]")
    limits = archive("limits", r"^【\d+月\d+日涨停分析")
    leaders = archive("leaders", r"^【\d+月\d+日连板股分析")
    morning = archive("morning", r"^【财联社\d+月\d+日早间新闻精选")
    focus = {}
    additional = {}
    evidence_by_id = {row["id"]: row for name in ("close", "limits", "leaders", "morning", "focus", "global")
                      for row in read_json(FOLDER / f"{name}.json", {}).get("rows", [])}
    evidence_by_id.update({row["id"]: row for row in read_json(FOLDER / "supplemental-sources.json", [])})
    for path in sorted((FOLDER / "articles").glob("*.json")):
        row = read_json(path)
        row["cache"] = path.relative_to(DATA).as_posix()
        evidence_by_id[row["id"]] = row
        if "焦点复盘" in row["title"] and row["published_at"][11:19] <= "18:00:00":
            focus[row["published_at"][:10]] = row
        elif ("【早报】" in row["title"] or "环球市场" in row["title"]) and row["published_at"][11:19] <= "18:00:00":
            additional.setdefault(row["published_at"][:10], []).append(row)
    # The long-form morning digest can recover a missing short-form bulletin.
    for date, rows in additional.items():
        if date not in morning:
            morning[date] = next((row for row in rows if "【早报】" in row["title"]), rows[0])
    overrides = read_json(FOLDER / "overrides.json", {})
    prepared, breadth_history, theme_history, audit = [], {}, {}, []
    stamp = now_cn().isoformat()
    for index, date in enumerate(dates):
        if date >= now_cn().date().isoformat():
            continue  # Current-day observations stay under the scheduled updater.
        q = quantitative(indices, date)
        r = {"date": date, "phase": "close", "kind": "retrospective", "model": VERSION,
             "asof": date + "T18:00:00+08:00", "assessed_at": stamp,
             "sources": [], "dimensions": [], "conditions": [], "reasons": [], "metrics": {},
             "missing": [], "research_note": "按当日18:00前公开资料进行规则回评；不代表当时发布过信号。新闻因子与原人工评估有方法差异，未做收益回测。"}
        source_ids = set()
        def source(row):
            ident = "cls-" + str(row["id"]) if isinstance(row["id"], int) else row["id"]
            if ident not in source_ids:
                source_ids.add(ident)
                title = row["title"] or (re.search(r"【(.+?)】", row["text"])[1] if re.search(r"【(.+?)】", row["text"]) else "财联社历史新闻")
                r["sources"].append({"id": ident, "title": title, "url": row["url"],
                                     "published_at": row["published_at"], "retrieved_at": row["retrieved_at"],
                                     "cache": row.get("cache", f"backfill/articles/{row['id']}.json")})
            return ident
        def dimension(key, score, reason, rows):
            r["dimensions"].append({"key": key, "score": score, "reason": reason,
                                     "source_ids": list(dict.fromkeys(source(row) for row in rows))})
        price = {"id": "prices", "title": "东方财富三指数历史日线（本机保存原始返回）",
                 "url": "https://quote.eastmoney.com/center/hszs.html", "date": date,
                 "note": "按截至所选日的行情回算，原始文件与索引保存在 data/raw 和 data/indices.json"}
        if q:
            r["sources"].append(price)
            r["dimensions"].extend([
                {"key": "trend", "score": round(q["trend_points"] * .2),
                 "reason": "三个指数等权：收盘相对MA5/MA20、MA20方向及5日收益四项检查，再折算至20分。",
                 "source_ids": ["prices"]},
                {"key": "volume", "score": round(q["volume_points"] * .15),
                 "reason": f"沪深成交额较前5日均额为{q['turnover_ratio5']:.3f}倍；结合三指数当日平均涨跌方向评分，非单看放量。",
                 "source_ids": ["prices"]}])
        b = None
        for row in [close.get(date), leaders.get(date), focus.get(date)]:
            if row and (found := breadth(row["text"])):
                b = {**found, "row": row}
                break
        if b:
            breadth_history[date] = b
            dimension("breadth", b["score"], f"{b['evidence']}；按新闻家数档位评价广度。约数保持约数，不推算精确涨跌占比。", [b["row"]])
            r["metrics"]["breadth_note"] = b["evidence"]
        s = sentiment(limits.get(date), leaders.get(date))
        if s:
            dimension("sentiment", s["score"], f"封板率{s['seal_rate']:g}%，连板晋级率{s['continuation']:g}%；两项分档合计，不含ST和退市股。", [limits[date], leaders[date]])
            r["metrics"].update({key: int(value) if key in ("limit_up", "broken") else value for key, value in s.items() if key != "score" and value is not None})
            r["metrics"]["scope"] = "涨停/炸板/封板率/晋级率：财联社，不含ST和退市股。广度为报道约数；不与非ST样本混算占比。"
        override = overrides.get(date, {})
        t = theme_observation(leaders.get(date))
        if override.get("theme_observation"):
            t = {**override["theme_observation"], "row": leaders[date]}
        if t and (t["active"] or t["weak"]):
            theme_history[date] = t
            previous = theme_history.get(dates[index-1], {}) if index else {}
            continued = sorted(set(t["active"]) & set(previous.get("active", [])))
            score = 12 if continued else 8 if t["active"] else 4
            if t["weak"] and t["active"]:
                score -= 1
            detail = "活跃方向：" + "、".join(t["active"]) if t["active"] else "报道所述主要方向偏弱"
            if continued: detail += "；与上一交易日相同：" + "、".join(continued)
            if t["weak"]: detail += "；分歧或走弱：" + "、".join(t["weak"])
            dimension("theme", score, detail + "。以连续新闻描述观察主线；不把连板本身认作回踩承接。", [t["row"]] + ([previous["row"]] if continued else []))
        p = policy_observation(morning.get(date))
        if p:
            dimension("policy", p["score"], p["reason"], [p["row"]])
        e = external_observation(morning.get(date))
        if e is None:
            e = next((found for row in additional.get(date, []) if (found := external_observation(row))), None)
        if e:
            dimension("external", e["score"], f"当日早间报道的最近美股收盘：纳指{e['change']:+.2f}%。仅作海外科技风险偏好代理，不涵盖全部海外风险。", [e["row"]])
        ev = event_observation(morning.get(date), focus.get(date))
        if ev:
            dimension("events", ev["score"], ev["reason"], [row for _, row in ev["risks"]])
        previous_b = breadth_history.get(dates[index-1]) if index else None
        older_b = breadth_history.get(dates[index-2]) if index >= 2 else None
        sustained = bool(b and previous_b and older_b and b["direction"] == "up" and previous_b["direction"] == "up"
                         and b["score"] >= previous_b["score"] >= older_b["score"])
        r["conditions"] = [
            {"key": "seal", "status": "pass" if s and s["seal_rate"] >= 70 else "fail" if s else "unknown",
             "detail": f"当日封板率{s['seal_rate']:g}%" if s else "缺少封板率/晋级率资料",
             "source_ids": [source(limits[date])] if s else []},
            {"key": "breadth", "status": "pass" if sustained else "fail" if b and b["direction"] == "down" else "unknown",
             "detail": "连续两日上涨家数居多，广度档位较再前一日未下降；约数仅按档位比较。" if sustained else "未证实连续两日广度改善；单日反弹不通过。",
             "source_ids": list(dict.fromkeys(source(item['row']) for item in (b, previous_b, older_b) if item))},
            {"key": "theme", "status": "unknown", "detail": "新闻中的板块上涨或连板不足以证明主线回踩承接，需另有明确证据。"}]
        # Explicit, source-linked manual readings resolve only the reviewed gaps.
        for addition in override.get("dimensions", []):
            rows = [evidence_by_id[ident] for ident in addition["article_ids"]]
            r["dimensions"] = [d for d in r["dimensions"] if d["key"] != addition["key"]]
            dimension(addition["key"], addition["score"], addition["reason"], rows)
        if override.get("theme_condition"):
            condition = override["theme_condition"]
            rows = [evidence_by_id[ident] for ident in condition["article_ids"]]
            r["conditions"][2] = {"key": "theme", **{k: v for k, v in condition.items() if k != "article_ids"},
                                    "source_ids": [source(row) for row in rows]}
        keys = {d["key"] for d in r["dimensions"]}
        for key, label, _ in DIMENSIONS:
            if key not in keys:
                reason = {"breadth": "未取得可用的当日全市场涨跌家数或明确广度描述",
                          "sentiment": "封板率或连板晋级率未能核定",
                          "theme": "当日板块描述不足以按连续性规则核定",
                          "policy": "新闻窗口缺少可明确归类的国内政策/基本面证据，未用中性分填充",
                          "external": "未核定当时已完成的海外交易日涨跌，未把休市当作零涨跌",
                          "events": "未完成可支撑风险档位的事件核查；未发现关键词不等于没有风险"}.get(key, "行情窗口不完整")
                r["missing"].append({"key": key, "label": label, "reason": reason,
                                      "status": "evidence_insufficient" if morning.get(date) or focus.get(date) else "source_unavailable"})
        r["reasons"] = ["按截至当日18:00的公开资料重建八维评分；仅用于历史环境回看。",
                        f"已核验{len(keys)}/8项；" + ("资料齐全后按分档显示开仓条件。" if len(keys) == 8 else "缺失项不计零分、不推算总分。"),
                        "分数达标仍需封板、广度和回踩承接三项同时通过；减仓要结合持仓失效位。"]
        validate_review(r, date, "close", strict=True)
        prepared.append(r)
        audit.append({"date": date, "policy_evidence": p["evidence"] if p else [],
                      "event_evidence": [x for x, _ in ev["risks"]] if ev else [],
                      "theme": {k: v for k, v in (t or {}).items() if k != "row"}})
    return prepared, audit


def publish_reviews(proposed):
    """Publish new historical dates under the price updater's shared lock."""
    import msvcrt
    handle = (DATA / "update.lock").open("a+b")
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        raise RuntimeError("已有更新正在运行；历史评估尚未发布，请稍后重试") from None
    try:
        now = now_cn()
        actual = {(r["date"], r["phase"]) for r in read_json(DATA / "dashboard.json")["records"]}
        seen = set()
        for r in proposed:
            ident = (r["date"], r["phase"])
            if ident in seen or ident not in actual or r["phase"] != "close":
                raise ValueError("历史评估日期重复，或没有对应的真实收盘行情")
            seen.add(ident)
            if r["date"] >= now.date().isoformat() or r["kind"] != "retrospective":
                raise ValueError("批量补录仅限过去交易日的复盘评估")
            if datetime.fromisoformat(r["assessed_at"]) > now:
                raise ValueError("不能发布未来评估")
            validate_review(r, r["date"], r["phase"], strict=True)
        written, preserved = [], []
        for r in proposed:
            target = DATA / "reviews" / f"{r['date']}-close.json"
            if target.exists():
                preserved.append(r["date"])
            else:
                write_json(target, r)
                written.append(r["date"])
        build_dashboard()
        return written, preserved
    finally:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


def run(publish=False):
    proposed, audit = make_reviews()
    written, preserved = [], []
    for r in proposed:
        write_json(FOLDER / "candidates" / f"{r['date']}-close.json", r)
        target = DATA / "reviews" / f"{r['date']}-close.json"
        if target.exists():
            preserved.append(r["date"])
    if publish:
        written, preserved = publish_reviews(proposed)
    write_json(FOLDER / "factor-audit.json", audit)
    stats = {"model": VERSION, "generated_at": now_cn().isoformat(), "candidate_days": len(proposed),
             "complete": sum(len(r["dimensions"]) == 8 for r in proposed),
             "missing_dimensions": dict(Counter(m["label"] for r in proposed for m in r["missing"])),
             "published": len(written), "preserved_existing": preserved}
    write_json(FOLDER / "status.json", stats)
    print(stats)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--publish", action="store_true")
    run(parser.parse_args().publish)
