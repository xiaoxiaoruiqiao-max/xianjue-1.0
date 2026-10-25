import copy
from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import backfill_history as history
from scoring import assess, validate_review
from update import DATA, read_json, write_json
from test_market import review


class HistoricalRulesTests(unittest.TestCase):
    def test_rounded_breadth_is_not_an_exact_metric(self):
        result = history.breadth("全市场近4500只个股上涨")
        self.assertEqual(result["score"], 13)
        self.assertEqual(result["qualifier"], "近")
        self.assertNotIn("decline_ratio", result)
        self.assertIsNone(history.breadth("全市场个股涨跌互现"))

    def test_news_archive_excludes_noon_and_after_cutoff(self):
        rows = [
            {"id": 1, "published_at": "2026-09-09T11:40:00+08:00", "text": "【午间涨停分析】", "title": ""},
            {"id": 2, "published_at": "2026-09-09T15:10:00+08:00", "text": "【9月9日涨停分析】", "title": ""},
            {"id": 3, "published_at": "2026-09-09T18:01:00+08:00", "text": "【9月9日涨停分析】", "title": ""}]
        with patch.object(history, "read_json", return_value={"rows": rows}):
            self.assertEqual(history.archive("limits", r"^【\d+月\d+日涨停分析")["2026-09-09"]["id"], 2)

    def test_stock_rise_and_sector_forecast_are_not_sector_strength(self):
        r = {"text": "板块方面，银行板块下跌，工商银行涨停。半导体板块有望明日走强。"}
        observation = history.theme_observation(r)
        self.assertEqual(observation["active"], [])
        self.assertEqual(observation["weak"], ["金融地产"])

    def test_empty_event_scan_does_not_give_safe_score(self):
        self.assertIsNone(history.event_observation({"text": "今日指数小幅上涨。"}, None))

    def test_policy_expectation_does_not_receive_implemented_easing_bonus(self):
        r = {"text": "早报\n1、央行预计适时降准，支持实体经济。"}
        self.assertEqual(history.policy_observation(r)["score"], 6)

    def test_external_market_closure_is_not_zero_return(self):
        self.assertIsNone(history.external_observation({"text": "美国股市因节日休市。"}))

    def test_missing_reason_propagates_and_no_total_is_invented(self):
        r = review()
        r["dimensions"] = [d for d in r["dimensions"] if d["key"] != "events"]
        r["missing"] = [{"key": "events", "reason": "未取得当日事件核查依据", "status": "source_unavailable"}]
        result = assess(r, r["date"], "close")
        self.assertIsNone(result["score"])
        self.assertEqual(result["missing"][0]["reason"], "未取得当日事件核查依据")
        self.assertEqual(result["actions"]["reduce"], "资料不足")

    def test_high_score_does_not_pass_unknown_pullback(self):
        r = review()
        r["conditions"][2]["status"] = "unknown"
        result = assess(r, r["date"], "close")
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["actions"]["add"], "等待确认")

    def test_missing_condition_source_is_rejected(self):
        r = review()
        r["conditions"][2]["source_ids"] = ["not-in-sources"]
        with self.assertRaises(ValueError):
            validate_review(r, r["date"], "close")


class HistoricalPublishTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).parent / ".tmp"
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.folder = Path(self.temp.name)
        self.now = datetime.fromisoformat("2026-10-23T17:00:00+08:00")
        self.r = review()
        write_json(self.folder / "dashboard.json", {"records": [{"date": self.r["date"], "phase": "close"}]})
        self.target = self.folder / "reviews" / f"{self.r['date']}-close.json"

    def tearDown(self):
        self.temp.cleanup()

    def publish(self, records):
        with patch.object(history, "DATA", self.folder), patch.object(history, "now_cn", return_value=self.now), patch.object(history, "build_dashboard"):
            return history.publish_reviews(records)

    def test_publish_preserves_existing_review_bytes(self):
        write_json(self.target, {"existing": "original manual review"})
        original = self.target.read_bytes()
        written, preserved = self.publish([self.r])
        self.assertEqual(written, [])
        self.assertEqual(preserved, [self.r["date"]])
        self.assertEqual(self.target.read_bytes(), original)

    def test_new_publication_is_idempotent(self):
        self.assertEqual(self.publish([self.r])[0], [self.r["date"]])
        self.assertEqual(self.publish([self.r])[0], [])

    def test_entire_batch_is_validated_before_writing(self):
        other = copy.deepcopy(self.r)
        other["date"] = "2026-09-10"
        with self.assertRaises(ValueError):
            self.publish([self.r, other])
        self.assertFalse(self.target.exists())

    def test_future_source_or_current_day_cannot_be_backfilled(self):
        bad = copy.deepcopy(self.r)
        bad["sources"][0]["published_at"] = "2026-09-10T09:30:00+08:00"
        with self.assertRaises(ValueError): self.publish([bad])
        bad = copy.deepcopy(self.r)
        bad["date"] = self.now.date().isoformat()
        write_json(self.folder / "dashboard.json", {"records": [{"date": bad["date"], "phase": "close"}]})
        with self.assertRaises(ValueError): self.publish([bad])
        self.assertFalse(self.target.exists())


class HistoricalEvidenceTests(unittest.TestCase):
    def test_all_published_reviews_keep_their_source_caches(self):
        for path in (DATA / 'reviews').glob('*.json'):
            for source in read_json(path).get('sources', []):
                if source.get('cache'):
                    cached = DATA / source['cache'].replace('\\', '/')
                    self.assertTrue(cached.is_file(), str(cached))

    def test_saved_historical_evidence_matches_real_dates_and_cutoffs(self):
        indices = read_json(DATA / "indices.json")["indices"]
        actual = set.intersection(*({r["date"] for r in rows} for rows in indices.values()))
        dashboard = read_json(DATA / "dashboard.json")
        visible = {r["date"] for r in dashboard["records"] if r["phase"] == "close"}
        candidates = list((DATA / "backfill" / "candidates").glob("*.json"))
        self.assertGreater(len(candidates), 200)
        for path in candidates:
            r = read_json(path)
            self.assertIn(r["date"], actual)
            if dashboard["history_start"] <= r["date"] <= dashboard["today"]:
                self.assertIn(r["date"], visible)
            self.assertEqual(r["phase"], "close")
            self.assertEqual(r["kind"], "retrospective")
            self.assertEqual(r["asof"], r["date"] + "T18:00:00+08:00")
            complete = validate_review(r, r["date"], "close")
            self.assertEqual(complete, not bool(r["missing"]))
            for source in r["sources"]:
                if source["id"] != "prices":
                    self.assertIn("published_at", source)
                    self.assertTrue((DATA / source["cache"].replace('\\', '/')).is_file(), source["cache"])
            if assess(r, r["date"], "close")["actions"]["add"] == "可分批评估":
                self.assertTrue(all(c["status"] == "pass" for c in r["conditions"]))
                self.assertTrue(r["conditions"][2].get("source_ids"))


if __name__ == "__main__":
    unittest.main()
