from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image
import requests

from docreranker.annotation import annotate_example, annotate_examples, load_examples, main, shuffled_candidates
from docreranker.costs import ModelPrice
from docreranker.openrouter import InvalidTeacherOutput, OpenRouterClient, OpenRouterError


class MockResponse:
    status_code = 200
    headers = {}

    def json(self):
        return {"id": "gen-mock", "choices": [{"finish_reason": "stop", "message": {
            "content": "Page evidence supports the stated answer; other pages cover a different topic."}}],
            "usage": {"cost": 0.0001, "prompt_tokens": 100, "completion_tokens": 20}}


class MockSession:
    def __init__(self, fail_at=None):
        self.calls = []
        self.fail_at = fail_at

    def post(self, url, **kwargs):
        self.calls.append(kwargs["json"])
        if len(self.calls) == self.fail_at:
            raise requests.Timeout()
        return MockResponse()


class TruncatedResponse(MockResponse):
    def json(self):
        response = super().json()
        response["choices"][0]["finish_reason"] = "length"
        return response


class InvalidFirstQuerySession(MockSession):
    def post(self, url, **kwargs):
        response = super().post(url, **kwargs)
        content = kwargs["json"]["messages"][-1]["content"]
        return TruncatedResponse() if "UNUSABLE_QUERY" in json.dumps(content) else response


class AnnotationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name, color in [("positive", "white"), ("negative", "gray")]:
            Image.new("RGB", (120, 160), color).save(self.root / f"{name}.png")
        self.row = {"query_id": "q1", "query": "What is the annual revenue?", "subset": "table",
            "positive_page_ids": ["positive"], "candidates": [
                {"page_id": "negative", "image": str(self.root / "negative.png"), "score": 999, "relevant": False},
                {"page_id": "positive", "image": str(self.root / "positive.png"), "score": -10, "relevant": True}]}
        self.price = ModelPrice("google/gemini-2.5-flash-lite", 0.1, 0.4)

    def tearDown(self):
        self.tmp.cleanup()

    def client(self, session):
        return OpenRouterClient(self.root / "cache", self.price, 1, api_key="mock",
                                session=session, max_attempts=1, sleep=lambda _: None)

    def test_labels_outrank_retrieval_score_and_output_has_full_permutation(self):
        session = MockSession()
        with self.client(session) as client:
            record = annotate_example(self.row, client)
        answer = json.loads(record["messages"][-1]["content"].split("<answer>")[1].split("</answer>")[0])
        self.assertEqual(sorted(answer), [1, 2])
        self.assertIn(answer[0], record["positive_ids"])
        self.assertEqual(record["page_ids"][answer[0] - 1], "positive")
        self.assertEqual(len(session.calls), 3)
        self.assertTrue(all(Path(path).is_absolute() for path in record["images"]))
        self.assertNotIn("999", record["messages"][1]["content"])

    def test_partial_stage_resume_does_not_repeat_completed_image_call(self):
        first = MockSession(fail_at=2)
        with self.client(first) as client:
            with self.assertRaises(OpenRouterError):
                annotate_example(self.row, client)
        second = MockSession()
        with self.client(second) as client:
            record = annotate_example(self.row, client)
            same = annotate_example(self.row, client)
        self.assertEqual(len(second.calls), 2)  # second image + refinement only
        self.assertEqual(record, same)

    def test_seeded_shuffle_is_deterministic_and_positions_vary(self):
        result = shuffled_candidates(self.row, seed=42)
        self.assertEqual(result, shuffled_candidates(self.row, seed=42))
        positions = set()
        for index in range(20):
            row = {**self.row, "query_id": f"query-{index}"}
            positions.add(tuple(shuffled_candidates(row, seed=42)[2]))
        self.assertEqual(positions, {(1,), (2,)})

    def test_no_positive_is_skipped_and_relative_images_resolve_from_input(self):
        row = json.loads(json.dumps(self.row))
        row["candidates"][0]["image"] = "negative.png"
        row["candidates"][1]["image"] = "positive.png"
        negative = {**row, "query_id": "no-pos", "positive_page_ids": [],
                    "candidates": [row["candidates"][0]]}
        source = self.root / "input.jsonl"
        source.write_text(json.dumps(row) + "\n" + json.dumps(negative) + "\n")
        examples, skipped = load_examples(source)
        self.assertEqual(skipped, 1)
        self.assertEqual(len(examples), 1)
        self.assertTrue(Path(examples[0]["candidates"][0]["image"]).is_absolute())

    def test_quality_exclusions_are_replaced_before_the_limit(self):
        source = self.root / "input.jsonl"
        excluded = {**self.row, "query_id": "rejected", "candidates": []}
        source.write_text(json.dumps(excluded) + "\n" + json.dumps(self.row) + "\n")
        examples, skipped = load_examples(source, limit=1, excluded_ids={"rejected"})
        self.assertEqual([row["query_id"] for row in examples], ["q1"])
        self.assertEqual(skipped, 0)

    def test_dry_run_never_constructs_paid_client(self):
        source = self.root / "input.jsonl"
        source.write_text(json.dumps(self.row) + "\n")
        with patch("docreranker.annotation.OpenRouterClient", side_effect=AssertionError("paid client")):
            with patch.dict("os.environ", {}, clear=True), redirect_stdout(io.StringIO()):
                result = main(["--input", str(source), "--output", str(self.root / "sft.jsonl")])
        self.assertEqual(result["mode"], "dry-run")
        self.assertFalse((self.root / "sft.jsonl").exists())

    def test_inconsistent_ground_truth_is_rejected(self):
        self.row["candidates"][1]["relevant"] = False
        source = self.root / "input.jsonl"
        source.write_text(json.dumps(self.row) + "\n")
        with self.assertRaisesRegex(ValueError, "Conflicting relevance"):
            load_examples(source)

    def test_evaluation_split_cannot_become_training_annotations(self):
        self.row["split"] = "evaluation"
        source = self.root / "input.jsonl"
        source.write_text(json.dumps(self.row) + "\n")
        with self.assertRaisesRegex(ValueError, "only accepts split=train"):
            load_examples(source)

    def test_false_positive_annotation_cannot_override_gold_ids(self):
        self.row["candidates"][0]["relevant"] = True
        source = self.root / "input.jsonl"
        source.write_text(json.dumps(self.row) + "\n")
        with self.assertRaisesRegex(ValueError, "Conflicting relevance"):
            load_examples(source)

    def test_duplicate_image_is_rejected_before_teacher_spend(self):
        self.row["candidates"][0]["image"] = self.row["candidates"][1]["image"]
        source = self.root / "input.jsonl"
        source.write_text(json.dumps(self.row) + "\n")
        with self.assertRaisesRegex(ValueError, "Duplicate candidate image"):
            load_examples(source)

    def test_direct_annotation_api_also_rejects_heldout_split(self):
        self.row["split"] = "validation"
        with self.assertRaisesRegex(ValueError, "only accepts split=train"):
            annotate_example(self.row, client=None)

    def test_parallel_query_results_keep_order_and_stage_cache(self):
        examples = [{**self.row, "query_id": f"q{i}", "query": f"What is revenue for item {i}?"}
                    for i in range(8)]
        sessions = []

        class SlowSession(MockSession):
            def post(self, *args, **kwargs):
                time.sleep(0.005)
                return super().post(*args, **kwargs)

        def factory():
            session = SlowSession()
            sessions.append(session)
            return session

        with OpenRouterClient(self.root / "parallel-cache", self.price, 1, api_key="fake",
                              session_factory=factory) as client:
            results = list(annotate_examples(examples, client, workers=4))
            resumed = list(annotate_examples(examples, client, workers=4))
        self.assertEqual([r["query_id"] for r in results], [r["query_id"] for r in examples])
        self.assertEqual(resumed, results)
        self.assertEqual(sum(len(session.calls) for session in sessions), 24)
        self.assertEqual(len(sessions), 4)

    def test_parallel_failure_stops_dispatch_at_bounded_initial_wave(self):
        started = []
        barrier = threading.Barrier(4)
        examples = [{"query_id": i} for i in range(100)]

        def fake_annotate(example, client, **kwargs):
            started.append(example["query_id"])
            barrier.wait(timeout=5)
            if example["query_id"] == 0:
                raise ValueError("bad first query")
            client._cancelled.wait(timeout=5)
            return example

        with self.client(MockSession()) as client:
            with patch("docreranker.annotation.annotate_example", side_effect=fake_annotate):
                with self.assertRaisesRegex(ValueError, "bad first query"):
                    list(annotate_examples(examples, client, workers=4))
        self.assertEqual(set(started), {0, 1, 2, 3})

    def test_dry_run_does_not_read_env_file_and_defaults_to_flash_lite(self):
        source = self.root / "input.jsonl"
        source.write_text(json.dumps(self.row) + "\n")
        with redirect_stdout(io.StringIO()):
            result = main(["--input", str(source), "--output", str(self.root / "sft.jsonl"),
                           "--env-file", str(self.root / "does-not-exist.env"), "--workers", "4"])
        self.assertEqual(result["price"]["model"], "google/gemini-2.5-flash-lite")

    def test_consumer_close_cancels_and_joins_remaining_workers(self):
        barrier = threading.Barrier(2)
        finished = threading.Event()

        def fake_annotate(example, client, **kwargs):
            barrier.wait(timeout=5)
            if example["query_id"] == 0:
                return example
            client._cancelled.wait(timeout=5)
            finished.set()
            return example

        with self.client(MockSession()) as client:
            with patch("docreranker.annotation.annotate_example", side_effect=fake_annotate):
                records = annotate_examples([{"query_id": 0}, {"query_id": 1}], client, workers=2)
                self.assertEqual(next(records)["query_id"], 0)
                records.close()
                self.assertTrue(finished.is_set())
                self.assertTrue(client._cancelled.is_set())

    def test_skip_invalid_fills_target_and_rejection_survives_restart(self):
        examples = [{**self.row, "query_id": "bad", "query": "UNUSABLE_QUERY"},
                    *[{**self.row, "query_id": f"ok{i}", "query": f"Question {i}?"} for i in range(4)]]
        skipped = []
        session = InvalidFirstQuerySession()
        with self.client(session) as client:
            records = list(annotate_examples(examples, client, workers=1, limit=2,
                skip_invalid_examples=True, on_skip=lambda row: skipped.append(row["query_id"])))
            self.assertFalse(client._cancelled.is_set())
        self.assertEqual([r["query_id"] for r in records], ["ok0", "ok1"])
        self.assertEqual(skipped, ["bad"])
        self.assertEqual(len(session.calls), 7)
        never_called = MockSession(fail_at=1)
        with self.client(never_called) as client:
            self.assertEqual(list(annotate_examples(examples, client, limit=2,
                                                   skip_invalid_examples=True)), records)
        self.assertEqual(never_called.calls, [])

    def test_parallel_skip_fills_exact_target_without_dispatching_extra_successes(self):
        examples = [{**self.row, "query_id": "bad", "query": "UNUSABLE_QUERY"},
                    *[{**self.row, "query_id": f"ok{i}", "query": f"Question {i}?"} for i in range(10)]]
        session = InvalidFirstQuerySession()
        with self.client(session) as client:
            records = list(annotate_examples(examples, client, workers=4, limit=4, skip_invalid_examples=True))
        self.assertEqual([r["query_id"] for r in records], [f"ok{i}" for i in range(4)])
        self.assertEqual(len(session.calls), 1 + 4 * 3)

    def test_skip_mode_fails_explicitly_when_success_target_cannot_be_reached(self):
        examples = [{**self.row, "query_id": "bad", "query": "UNUSABLE_QUERY"}, self.row]
        with self.client(InvalidFirstQuerySession()) as client:
            with self.assertRaisesRegex(ValueError, "Requested 2 successful annotations but only 1"):
                list(annotate_examples(examples, client, workers=2, limit=2, skip_invalid_examples=True))

    def test_default_still_stops_at_invalid_teacher_output(self):
        examples = [{**self.row, "query_id": "bad", "query": "UNUSABLE_QUERY"}, self.row]
        session = InvalidFirstQuerySession()
        with self.client(session) as client:
            with self.assertRaises(InvalidTeacherOutput):
                list(annotate_examples(examples, client))
        self.assertEqual(len(session.calls), 1)

    def test_network_failure_is_not_swallowed_by_skip_mode(self):
        with self.client(MockSession(fail_at=1)) as client:
            with self.assertRaises(OpenRouterError) as caught:
                list(annotate_examples([self.row], client, skip_invalid_examples=True))
            self.assertNotIsInstance(caught.exception, InvalidTeacherOutput)

    def test_skip_cli_preflights_target_not_entire_pool_and_writes_exact_count(self):
        source, output = self.root / "pool.jsonl", self.root / "filled.jsonl"
        examples = [{**self.row, "query_id": f"ok{i}", "query": f"Question {i}?"} for i in range(10)]
        source.write_text("".join(json.dumps(row) + "\n" for row in examples))
        session = MockSession()

        def factory(cache_dir, price, budget, **kwargs):
            return OpenRouterClient(cache_dir, price, budget, api_key="fake", session=session, **kwargs)

        with patch("docreranker.annotation.OpenRouterClient", side_effect=factory), \
                patch("docreranker.annotation.live_price", return_value=self.price), redirect_stdout(io.StringIO()):
            report = main(["--input", str(source), "--output", str(output), "--limit", "2",
                "--cache-dir", str(self.root / "cli-cache"), "--max-cost-usd", "0.0024",
                "--workers", "4", "--skip-invalid-examples", "--execute"])
        self.assertEqual(report["eligible_input_pool"], 10)
        self.assertEqual(report["samples"], 2)
        self.assertEqual(len(output.read_text().splitlines()), 2)
        self.assertEqual(len(session.calls), 6)


if __name__ == "__main__":
    unittest.main()
