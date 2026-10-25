import copy
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import scoring
import update
import publish_review


def history():
    start = datetime(2026, 8, 1)
    bars = [{"date": (start + timedelta(days=i)).date().isoformat(), "open": 100+i,
             "close": 101+i, "high": 102+i, "low": 99+i, "volume": 100,
             "turnover": 1000+i*10, "change_pct": 1} for i in range(40)]
    return {k: copy.deepcopy(bars) for k in update.INDICES}


def review():
    return {"date": "2026-09-09", "phase": "close", "kind": "retrospective", "asof": "2026-09-09T16:30:00+08:00",
            "assessed_at": "2026-09-09T16:35:00+08:00", "sources": [{"id": "a", "title": "测试证据", "url": "https://example.com", "published_at": "2026-09-09T15:10:00+08:00"}],
            "dimensions": [{"key": k, "score": cap, "reason": "测试依据", "source_ids": ["a"]} for k, _, cap in scoring.DIMENSIONS],
            "metrics": {"seal_rate": 80},
            "conditions": [{"key": k, "status": "pass", "detail": "已核验", "source_ids": ["a"]} for k in ("seal", "breadth", "theme")]}


class ScoreTests(unittest.TestCase):
    def test_future_prices_do_not_change_past_reference(self):
        data = history()
        date = data["sh"][-1]["date"]
        before = scoring.quantitative(data, date)
        for rows in data.values():
            rows.append({**rows[-1], "date": "2027-01-01", "close": 1, "turnover": 1e20})
        self.assertEqual(scoring.quantitative(data, date), before)

    def test_missing_dimension_is_not_zero_or_full_score(self):
        r = review()
        r["dimensions"].pop()
        result = scoring.assess(r, r["date"], "close")
        self.assertIsNone(result["score"])
        self.assertIsNone(result["dimensions"][-1]["score"])
        self.assertEqual(result["coverage"], 95)
        self.assertNotEqual(result["actions"]["add"], "可分批评估")

    def test_zero_score_is_valid(self):
        r = review()
        for d in r["dimensions"]:
            d["score"] = 0
        result = scoring.assess(r, r["date"], "close")
        self.assertEqual(result["score"], 0)
        self.assertTrue(result["complete"])
        self.assertEqual(result["state"], "defense")

    def test_high_score_cannot_bypass_unknown_conditions_or_risk_gate(self):
        r = review()
        self.assertEqual(scoring.assess(r, r["date"], "close")["actions"]["add"], "可分批评估")
        r["conditions"][1]["status"] = "unknown"
        self.assertEqual(scoring.assess(r, r["date"], "close")["actions"]["add"], "等待确认")
        r["metrics"]["decline_ratio"] = .9
        self.assertEqual(scoring.assess(r, r["date"], "close")["state"], "defense")

    def test_noon_volume_not_compared_to_full_day(self):
        data = history()
        result = scoring.quantitative(data, "2026-09-09", "midday")
        self.assertIsNone(result["volume_points"])
        self.assertIsNone(result["turnover_ratio5"])
        self.assertIsNone(result["turnover_change"])

    def test_future_sources_and_noon_close_mix_are_rejected(self):
        r = review()
        r["sources"][0]["published_at"] = "2026-09-10T09:30:00+08:00"
        with self.assertRaises(ValueError): scoring.validate_review(r, r["date"], "close")
        r = review()
        r["phase"] = "midday"
        with self.assertRaises(ValueError): scoring.validate_review(r, r["date"], "midday")

    def test_invalid_scores_and_duplicate_sources_rejected(self):
        for value in (-1, 21, float("nan"), True):
            r = review()
            r["dimensions"][0]["score"] = value
            with self.assertRaises(ValueError): scoring.validate_review(r, r["date"], "close")
        r = review()
        r["sources"].append(r["sources"][0])
        with self.assertRaises(ValueError): scoring.validate_review(r, r["date"], "close")

    def test_saved_reviews_pass_evidence_validation(self):
        for path in (update.DATA / "reviews").glob("*.json"):
            r = update.read_json(path)
            self.assertIsInstance(scoring.validate_review(r, r["date"], r["phase"]), bool)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        test_root = Path(__file__).resolve().parent / ".tmp"
        test_root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=test_root)
        self.folder = Path(self.temp.name)
        self.mock_data = patch.object(update, "DATA", self.folder)
        self.mock_data.start()

    def tearDown(self):
        self.mock_data.stop()
        self.temp.cleanup()

    def test_stale_and_afternoon_quotes_cannot_be_noon(self):
        now = datetime.fromisoformat("2026-09-09T12:30:00+08:00")
        quotes = {k: {"asof": "2026-09-09T11:30:00+08:00"} for k in update.INDICES}
        self.assertTrue(update.valid_current_quotes(quotes, now, "midday"))
        quotes["sh"]["asof"] = "2026-09-08T11:30:00+08:00"
        self.assertFalse(update.valid_current_quotes(quotes, now, "midday"))
        for q in quotes.values(): q["asof"] = "2026-09-09T15:00:00+08:00"
        self.assertFalse(update.valid_current_quotes(quotes, now, "midday"))

    def test_close_window_and_midday_window(self):
        for clock, expected in [("12:30", "midday"), ("13:05", None), ("15:00", None), ("16:30", "close")]:
            self.assertEqual(update.phase_for(datetime.fromisoformat("2026-09-09T" + clock + ":00+08:00")), expected)

    def test_inconsistent_final_price_is_not_published(self):
        data = history()
        quotes = {k: rows[-1] for k, rows in data.items()}
        self.assertTrue(update.close_prices_agree(data, quotes, "2026-09-09"))
        quotes = copy.deepcopy(quotes)
        quotes["cyb"]["close"] += 1
        self.assertFalse(update.close_prices_agree(data, quotes, "2026-09-09"))

    def test_missing_trading_day_disables_reference(self):
        data = history()
        expected = [r["date"] for r in data["sh"]]
        for rows in data.values(): rows.pop(-3)
        row = update.make_record(data, "2026-09-09", "close", "2026-09-09T15:00:00+08:00", None, trade_days=expected)
        self.assertIsNone(row["quantitative"])

    def test_backup_requires_price_and_amount_agreement(self):
        a = {k: {"close": 100, "turnover": 10000} for k in update.INDICES}
        b = copy.deepcopy(a)
        self.assertTrue(update.backup_quotes_agree(a, b))
        b["sh"]["turnover"] = 9000
        self.assertFalse(update.backup_quotes_agree(a, b))

    def test_noon_backup_does_not_create_close_or_rewrite_snapshot(self):
        all_rows = history()
        data = {k: rows[:-1] for k, rows in all_rows.items()}
        update.write_json(self.folder / "indices.json", {"indices": data})
        original = (self.folder / "indices.json").read_bytes()
        quotes = {k: {**rows[-1], "asof": "2026-09-09T11:30:00+08:00", "close": 125, "turnover": 600} for k, rows in all_rows.items()}
        backup = {k: {"asof": row["asof"], "close": row["close"], "turnover": row["turnover"]} for k, row in quotes.items()}
        with patch.object(update, "fetch_index", side_effect=OSError("primary down")), patch.object(update, "fetch", return_value=b"test"), patch.object(update, "parse_quotes", return_value=quotes), patch.object(update, "parse_sina", return_value=backup):
            status = update.update(datetime.fromisoformat("2026-09-09T12:30:00+08:00"))
            self.assertTrue(status["backup_used"])
            self.assertEqual(status["state"], "success")
            self.assertEqual((self.folder / "indices.json").read_bytes(), original)
            snapshot = self.folder / "snapshots" / "2026-09-09-midday.json"
            saved = snapshot.read_bytes()
            update.update(datetime.fromisoformat("2026-09-09T12:31:00+08:00"))
            self.assertEqual(snapshot.read_bytes(), saved)
        records = update.read_json(self.folder / "dashboard.json")["records"]
        self.assertEqual([r["phase"] for r in records if r["date"] == "2026-09-09"], ["midday"])

    def test_review_revision_keeps_previous_version(self):
        update.write_json(self.folder / "indices.json", {"indices": history()})
        update.build_dashboard(datetime.fromisoformat("2026-09-09T16:30:00+08:00"))
        first = review()
        candidate = self.folder / "candidate.json"
        with patch.object(publish_review, "DATA", self.folder):
            update.write_json(candidate, first)
            self.assertEqual(publish_review.publish(candidate)["status"], "published-local")
            revised = copy.deepcopy(first)
            revised["assessed_at"] = "2026-09-09T16:40:00+08:00"
            revised["dimensions"][0]["score"] = 10
            update.write_json(candidate, revised)
            self.assertEqual(publish_review.publish(candidate)["status"], "published-local")
        archive = list((self.folder / "review-archive").glob("*.json"))
        self.assertEqual(len(archive), 1)
        self.assertEqual(update.read_json(archive[0]), first)

    def test_review_without_matching_snapshot_rejected(self):
        r = review()
        candidate = self.folder / "candidate.json"
        update.write_json(candidate, r)
        with patch.object(publish_review, "DATA", self.folder):
            with self.assertRaises(ValueError): publish_review.publish(candidate)

    def test_failed_fetch_preserves_price_cache(self):
        update.write_json(self.folder / "indices.json", {"indices": history(), "fetched_at": "2026-09-09T16:30:00+08:00"})
        before = (self.folder / "indices.json").read_bytes()
        with patch.object(update, "fetch_index", side_effect=OSError("network unavailable")), patch.object(update, "fetch", side_effect=OSError("network unavailable")):
            status = update.update(datetime.fromisoformat("2026-09-10T16:30:00+08:00"))
        self.assertEqual(status["state"], "failed")
        self.assertEqual((self.folder / "indices.json").read_bytes(), before)
        self.assertEqual(update.read_json(self.folder / "dashboard.json")["latest_date"], "2026-09-09")

    def test_snapshot_history_excludes_future_and_preserves_noon(self):
        data = history()
        update.write_json(self.folder / "indices.json", {"indices": data, "fetched_at": "2026-09-09T16:30:00+08:00"})
        quotes = {k: {**rows[-1], "close": 123} for k, rows in data.items()}
        update.write_json(self.folder / "snapshots" / "2026-09-09-midday.json", {"date": "2026-09-09", "asof": "2026-09-09T11:30:00+08:00", "fetched_at": "2026-09-09T12:30:00+08:00", "quotes": quotes})
        result = update.build_dashboard(datetime.fromisoformat("2026-09-09T16:30:00+08:00"))
        slots = [r for r in result["records"] if r["date"] == "2026-09-09"]
        self.assertEqual([r["phase"] for r in slots], ["midday", "close"])
        self.assertEqual(slots[0]["quotes"]["sh"]["close"], 123)
        self.assertEqual(slots[1]["quotes"]["sh"]["close"], 140)
        earlier = update.build_dashboard(datetime.fromisoformat("2026-09-08T16:30:00+08:00"))
        self.assertNotIn("2026-09-09", [r["date"] for r in earlier["records"]])


if __name__ == "__main__":
    unittest.main()
