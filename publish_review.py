"""Validate a proposed review, preserve the prior version, then publish locally."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil

from scoring import validate_review
from update import DATA, now_cn, read_json, write_json, build_dashboard


def publish(path):
    proposed = read_json(Path(path))
    date, phase = proposed["date"], proposed["phase"]
    datetime.strptime(date, "%Y-%m-%d")
    if phase not in ("close", "midday"):
        raise ValueError("评估时段无效")
    now = now_cn()
    if date < now.date().isoformat() and proposed.get("kind") != "retrospective":
        raise ValueError("过去交易日的新增或修订评估必须标注为复盘补录")
    if date > now.date().isoformat() or datetime.fromisoformat(proposed["assessed_at"]) > now:
        raise ValueError("不能发布未来评估")
    complete = validate_review(proposed, date, phase, strict=True)
    dashboard = read_json(DATA / "dashboard.json", {})
    if not any(r["date"] == date and r["phase"] == phase for r in dashboard.get("records", [])):
        raise ValueError("该日该时段尚无真实行情快照，不能生成评估")
    target = DATA / "reviews" / f"{date}-{phase}.json"
    if target.exists():
        old_bytes = target.read_bytes()
        old = json.loads(old_bytes)
        if old == proposed:
            return {"status": "unchanged", "date": date, "phase": phase}
        if datetime.fromisoformat(proposed["assessed_at"]) <= datetime.fromisoformat(old["assessed_at"]):
            raise ValueError("新评估时间不得早于已有版本")
        digest = hashlib.sha256(old_bytes).hexdigest()[:12]
        archived = DATA / "review-archive" / f"{date}-{phase}-{digest}.json"
        archived.parent.mkdir(exist_ok=True)
        if not archived.exists():
            shutil.copy2(target, archived)
    write_json(target, proposed)
    build_dashboard()
    return {"status": "published-local", "date": date, "phase": phase, "complete": complete}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="已写入本地的候选评估JSON")
    args = parser.parse_args()
    print(json.dumps(publish(args.path), ensure_ascii=False))
