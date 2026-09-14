"""Checks for ranking exploitation, data alignment, and real bin reweighting."""

import itertools
import json

import pytest

from docreranker.training.data import load_rows, multimodal_messages, resolution_balanced_indices
from docreranker.training.rewards import (format_reward, format_rerank_reward, parse_completion,
                                         rerank_reward, result_reward)


def answer(order):
    return f"<think>The relevant page contains the requested evidence.</think><answer>{json.dumps(order)}</answer>"


def test_pdf_cubic_reward_examples():
    assert result_reward(answer([2, 4, 1, 3, 5]), [2, 4], 5) == 1.0
    assert result_reward(answer([4, 2, 1, 3, 5]), [2, 4], 5) == 1.0
    assert result_reward(answer([2, 1, 4, 3, 5]), [2, 4], 5) == pytest.approx((1 + 1 / 27) / (1 + 1 / 8))
    assert result_reward(answer([1, 2, 3, 4, 5]), [2, 4], 5) == 0.125
    assert result_reward(answer([1, 3, 5, 2, 4]), [2, 4], 5) == pytest.approx((1 / 64 + 1 / 125) / 1.125)


@pytest.mark.parametrize("order", [[], [1], [1, 2], [1, 1, 1], [1, 2, 2], [0, 1, 2],
                                  [1, 2, 4], [1, 2, 3, 4], [True, 2, 3], [1.0, 2, 3], ["1", 2, 3]])
def test_invalid_candidate_lists_cannot_collect_any_reward(order):
    assert result_reward(answer(order), [1], 3) == 0.0
    assert format_reward(answer(order), 3) == 0.0


@pytest.mark.parametrize("text", [
    "[1,2,3]", "<answer>[1,2,3]</answer>",
    "<think></think><answer>[1,2,3]</answer>",
    "<think>ok</think><answer>[1,2,3,]</answer>",
    "<think>ok</think><answer>[1,2,3]</answer> trailing junk",
    "<think><answer>[1,2,3]</answer></think><answer>[1,2,3]</answer>",
    "<think>ok</think><answer>[1,2,3]</answer><answer>[3,2,1]</answer>",
    "<think>ok</think><answer>[__import__('os').system('false')]</answer>",
])
def test_malformed_protocol_is_not_accepted(text):
    assert parse_completion(text, 3) is None
    assert format_reward(text, 3) == 0


def test_json_whitespace_preserves_format_and_ranking_without_accepting_extra_content():
    text = " \n<think>Visible evidence.</think>\n<answer> \n [ 2,\n 1, 3 ] \t </answer>\n"
    assert parse_completion(text, 3) == [2, 1, 3]
    assert result_reward(text, [2], 3) == 1.0
    assert format_reward(text, 3) == 1.0
    for malformed in ("<answer> [2,1,3] </answer>", text + "extra", text.replace("[ 2,", "junk [ 2,")):
        assert parse_completion(malformed, 3) is None
        assert result_reward(malformed, [2], 3) == 0
        assert format_reward(malformed, 3) == 0


def test_all_permutations_obey_bounds_and_top1_preference():
    for permutation in itertools.permutations(range(1, 6)):
        reward = result_reward(answer(permutation), [1, 3], 5)
        assert 0 < reward <= 1
        assert format_reward(answer(permutation), 5) == 1
    assert result_reward(answer([1, 2, 3]), [1], 3) > result_reward(answer([2, 1, 3]), [1], 3)


def test_trl_conversational_batch_and_metadata_validation():
    completions = [[{"role": "assistant", "content": answer([1, 2])}], answer([2, 1])]
    assert rerank_reward(completions, [[1], [1]], [2, 2], trainer_state=None) == [1, 0.125]
    assert format_rerank_reward(completions, [2, 2]) == [1, 1]
    for positives in ([], [0], [3], [True], [1, 1]):
        with pytest.raises(ValueError):
            result_reward(answer([1, 2]), positives, 2)
    with pytest.raises(ValueError):
        rerank_reward(completions, [[1]], [2, 2])


def test_multimodal_candidate_mapping_preserves_interleaving():
    messages = [{"role": "system", "content": "Rank."},
                {"role": "user", "content": "Image 1: <image> Image 2: <image> Query?"}]
    parts = multimodal_messages(messages, 2)[1]["content"]
    assert [part["type"] for part in parts] == ["text", "image", "text", "image", "text"]
    assert "Image 1:" in parts[0]["text"] and "Image 2:" in parts[2]["text"]
    appended = multimodal_messages([{"role": "user", "content": "Query?"}], 2)[0]["content"]
    assert sum(part["type"] == "image" for part in appended) == 2
    with pytest.raises(ValueError):
        multimodal_messages(messages, 3)


def test_resolution_bins_rebalance_skewed_data():
    rows = [{"mean_pixels": 1024}] * 9 + [{"mean_pixels": 1048576}]
    selected, report = resolution_balanced_indices(rows, bins=10, seed=7)
    assert selected == resolution_balanced_indices(rows, bins=10, seed=7)[0]
    assert report["source_bin_counts"] == {"0": 9, "9": 1}
    assert selected.count(9) == 5
    assert len(selected) == 10
    same, same_report = resolution_balanced_indices(rows[:9], bins=10)
    assert sorted(same) == list(range(9))
    assert same_report["nonempty_bins"] == 1


def test_training_data_validates_images_and_excludes_eval(tmp_path):
    from PIL import Image

    images = []
    for i in range(2):
        path = tmp_path / f"page-{i}.png"
        Image.new("RGB", (56, 56), color=(i * 100, 0, 0)).save(path)
        images.append(str(path))
    row = {"query_id": "train-1", "images": images, "positive_ids": [1], "query": "Which is darker?"}
    path = tmp_path / "rl.jsonl"
    path.write_text(json.dumps(row) + "\n")
    loaded = load_rows(path, "grpo")
    assert loaded[0]["candidate_count"] == 2
    assert loaded[0]["mean_pixels"] == 3136
    exclude = tmp_path / "eval.jsonl"
    exclude.write_text(json.dumps({"query_id": "train-1"}) + "\n")
    with pytest.raises(ValueError, match="leaked"):
        load_rows(path, "grpo", exclude)
    row["messages"] = [{"role": "user", "content": "Rank these images."},
                       {"role": "assistant", "content": answer([1, 2])}]
    path.write_text(json.dumps(row) + "\n")
    assert len(load_rows(path, "sft")) == 1
    row["split"] = "evaluation"
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="split"):
        load_rows(path, "sft")
    row.pop("split")
    row["messages"][-1]["content"] = answer([2, 1])
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="positive candidate"):
        load_rows(path, "sft")
    row["messages"][-1]["content"] = answer([1, 1])
    path.write_text(json.dumps(row) + "\n")
    with pytest.raises(ValueError, match="permutation"):
        load_rows(path, "sft")
