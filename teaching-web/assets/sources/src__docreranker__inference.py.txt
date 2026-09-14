"""Batched multi-image Qwen2.5-VL reranking with auditable fallback behavior.

Heavy dependencies are imported only when inference is actually requested.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Iterable, Sequence

from docreranker.prompts import SYSTEM_PROMPT, ranking_prompt
from docreranker.training.data import multimodal_messages
from docreranker.training.rewards import parse_completion

EMPTY_CANDIDATE_RESPONSE = "<think>No candidate pages.</think><answer>[]</answer>"


def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{line_number}: expected an object")
            query_id = record.get("query_id")
            if not isinstance(query_id, str) or not query_id:
                raise ValueError(f"{path}:{line_number}: query_id must be a nonempty string")
            if query_id in seen:
                raise ValueError(f"{path}:{line_number}: duplicate query_id {query_id!r}")
            seen.add(query_id)
            records.append(record)
    return records


def candidate_page_ids(record: dict[str, Any]) -> list[str]:
    """Validate the ranking universe without loading any page image."""
    query_id = record.get("query_id")
    if not isinstance(query_id, str) or not query_id:
        raise ValueError("query_id must be a nonempty string")
    if not isinstance(record.get("query"), str) or not record["query"].strip():
        raise ValueError(f"{query_id}: query must be a nonempty string")
    candidates = record.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError(f"{query_id}: candidates must be an array")
    page_ids = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError(f"{query_id}: each candidate must be an object")
        page_id = candidate.get("page_id")
        if not isinstance(page_id, str) or not page_id:
            raise ValueError(f"{query_id}: page_id must be a nonempty string")
        page_ids.append(page_id)
    if len(set(page_ids)) != len(page_ids):
        raise ValueError(f"{query_id}: duplicate candidate page_id")
    return page_ids


def parse_ranking(response: str, n_candidates: int) -> list[int]:
    """Use the exact format/permutation rule that earns training reward.

    No repair, extraction of partial numbers, or silent insertion is performed.
    Malformed output must be counted as a fallback by the caller.
    """
    if type(n_candidates) is not int or n_candidates < 0:
        raise ValueError("n_candidates must be a nonnegative integer")
    if not isinstance(response, str):
        raise ValueError("model response must be a string")
    # Empty candidates bypass generation entirely. Training requires a positive
    # candidate count, so only this deterministic synthetic record has n=0.
    if n_candidates == 0:
        if response == EMPTY_CANDIDATE_RESPONSE:
            return []
        raise ValueError("empty candidates require the deterministic no-candidate response")
    ranking = parse_completion(response, n_candidates)
    if ranking is None:
        raise ValueError("expected a nonempty <think> rationale followed by one complete "
                         f"<answer> permutation of 1..{n_candidates}, without other text")
    return ranking


def input_fingerprint(record: dict[str, Any]) -> str:
    """Bind predictions to the question, candidate order, and image paths (not labels)."""
    payload = {
        "query_id": record["query_id"], "query": record["query"],
        "candidates": [{"page_id": page["page_id"], "image": page.get("image")}
                       for page in record["candidates"]],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def prediction_from_response(record: dict[str, Any], response: str, elapsed: float = 0.0) -> dict[str, Any]:
    baseline = candidate_page_ids(record)
    reason = None
    try:
        indices = parse_ranking(response, len(baseline))
        ranking = [baseline[index - 1] for index in indices]
    except ValueError as exc:
        ranking = baseline.copy()
        reason = str(exc)
    return {
        "query_id": record["query_id"], "ranked_page_ids": ranking,
        "baseline_page_ids": baseline, "raw_response": response,
        "fallback": reason is not None, "fallback_reason": reason,
        "elapsed": elapsed, "input_fingerprint": input_fingerprint(record),
    }


def build_messages(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a label-free prompt; page images stay separate for true multi-image input."""
    page_ids = candidate_page_ids(record)
    paths = []
    for candidate in record["candidates"]:
        image = candidate.get("image")
        if not isinstance(image, str) or not Path(image).is_absolute():
            raise ValueError(f"{record['query_id']}: image must be an absolute local path")
        paths.append(image)
    messages = multimodal_messages([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": ranking_prompt(record["query"], len(page_ids))},
    ], len(page_ids))
    images = iter(paths)
    for message in messages:
        for part in message["content"]:
            if part["type"] == "image":
                part["image"] = next(images)
    return messages


class QwenReranker:
    def __init__(
        self, model: str = "Qwen/Qwen2.5-VL-7B-Instruct", *, adapter: str | None = None,
        processor: str | None = None, device: str = "auto", dtype: str = "auto",
        max_new_tokens: int = 768, min_pixels: int = 256 * 28 * 28,
        max_pixels: int = 1024 * 28 * 28, attn_implementation: str = "sdpa",
        local_files_only: bool = False,
    ):
        if dtype not in {"auto", "float32", "float16", "bfloat16"}:
            raise ValueError("dtype must be auto, float32, float16, or bfloat16")
        if max_new_tokens < 1 or min_pixels < 1 or max_pixels < min_pixels:
            raise ValueError("invalid token or image pixel limits")
        self.model_name, self.adapter = model, adapter
        self.processor_name = processor or model
        self.device, self.dtype = device, dtype
        self.max_new_tokens = max_new_tokens
        self.min_pixels, self.max_pixels = min_pixels, max_pixels
        self.attn_implementation = attn_implementation
        self.local_files_only = local_files_only
        self._model = self._processor = self._torch = None

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration
        except ImportError as exc:
            raise RuntimeError("Install the project inference extras: pip install -e '.[model]'") from exc
        dtype = self.dtype if self.dtype == "auto" else getattr(torch, self.dtype)
        # torch_dtype remains compatible with the pinned Transformers 4.x training stack.
        model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            self.model_name, torch_dtype=dtype,
            device_map="auto" if self.device == "auto" else {"": self.device},
            attn_implementation=self.attn_implementation, local_files_only=self.local_files_only,
        )
        if self.adapter:
            try:
                from peft import PeftModel
            except ImportError as exc:
                raise RuntimeError("Loading a LoRA adapter requires peft (the train extras)") from exc
            model = PeftModel.from_pretrained(model, self.adapter, is_trainable=False)
        processor = AutoProcessor.from_pretrained(
            self.processor_name, min_pixels=self.min_pixels, max_pixels=self.max_pixels,
            local_files_only=self.local_files_only,
        )
        processor.tokenizer.padding_side = "left"
        model.eval()
        self._model, self._processor = model, processor
        self._torch = torch

    def rerank(self, record: dict[str, Any]) -> dict[str, Any]:
        return self.rerank_batch([record])[0]

    def _prediction_metadata(self) -> dict[str, Any]:
        return {
            "model": self.model_name, "adapter": self.adapter,
            "inference_config": {
                "processor": self.processor_name, "dtype": self.dtype,
                "min_pixels": self.min_pixels, "max_pixels": self.max_pixels,
                "max_new_tokens": self.max_new_tokens, "do_sample": False,
                "attn_implementation": self.attn_implementation,
            },
        }

    def rerank_batch(self, records: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
        if not records:
            return []
        results: list[dict[str, Any] | None] = [None] * len(records)
        active_indices = []
        seen = set()
        for index, record in enumerate(records):
            page_ids = candidate_page_ids(record)
            if record["query_id"] in seen:
                raise ValueError(f"duplicate query_id {record['query_id']!r}")
            seen.add(record["query_id"])
            if not page_ids:
                result = prediction_from_response(record, EMPTY_CANDIDATE_RESPONSE)
                result.update(**self._prediction_metadata(), generation_skipped="empty_candidates", batch_size=0)
                results[index] = result
            else:
                active_indices.append(index)
        if active_indices:
            active_records = [records[index] for index in active_indices]
            # Validate prompts and image availability before allocating model memory.
            messages = [build_messages(record) for record in active_records]
            from PIL import Image
            images = []
            try:
                for record in active_records:
                    for candidate in record["candidates"]:
                        with Image.open(candidate["image"]) as source:
                            images.append(source.convert("RGB"))
                self._load()
                started = time.perf_counter()
                texts = [self._processor.apply_chat_template(
                    message, tokenize=False, add_generation_prompt=True,
                ) for message in messages]
                inputs = self._processor(text=texts, images=images, padding=True, return_tensors="pt")
                # Accelerate dispatch hooks handle later layers for device_map='auto'.
                inputs = inputs.to(self._model.device)
                with self._torch.inference_mode():
                    generated = self._model.generate(
                        **inputs, max_new_tokens=self.max_new_tokens, do_sample=False,
                        pad_token_id=self._processor.tokenizer.pad_token_id,
                    )
                prompt_length = inputs["input_ids"].shape[1]
                responses = self._processor.batch_decode(
                    generated[:, prompt_length:], skip_special_tokens=True, clean_up_tokenization_spaces=False,
                )
                elapsed = time.perf_counter() - started
                if len(responses) != len(active_records):
                    raise RuntimeError("model returned a different number of responses than queries")
                for index, record, response in zip(active_indices, active_records, responses):
                    result = prediction_from_response(record, response, elapsed)
                    result.update(**self._prediction_metadata(), batch_size=len(active_records))
                    results[index] = result
            finally:
                for img in images:
                    img.close()
        return [result for result in results if result is not None]


def write_jsonl_atomic(path: str | Path, records: Iterable[dict[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            name = handle.name
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        os.replace(name, path)
    finally:
        if name is not None and os.path.exists(name):
            os.unlink(name)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Candidate JSONL, in retrieval order")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", default="Qwen/Qwen2.5-VL-7B-Instruct")
    parser.add_argument("--adapter", help="Optional PEFT/LoRA directory applied to --model")
    parser.add_argument("--processor", help="Processor source, defaults to --model")
    parser.add_argument("--device", default="auto", help="auto, cpu, cuda:0, ...")
    parser.add_argument("--dtype", default="auto", choices=["auto", "float32", "float16", "bfloat16"])
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--max-new-tokens", type=int, default=768)
    parser.add_argument("--min-pixels", type=int, default=256 * 28 * 28)
    parser.add_argument("--max-pixels", type=int, default=1024 * 28 * 28)
    parser.add_argument("--attn-implementation", default="sdpa", choices=["sdpa", "eager", "flash_attention_2"])
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if args.output.resolve() == args.input.resolve():
        parser.error("--output must differ from --input")
    if args.output.exists() and not args.overwrite:
        parser.error("output already exists; use --overwrite to replace it")
    records = read_jsonl(args.input)
    for record in records:
        candidate_page_ids(record)
    reranker = QwenReranker(
        args.model, adapter=args.adapter, processor=args.processor, device=args.device,
        dtype=args.dtype, max_new_tokens=args.max_new_tokens,
        min_pixels=args.min_pixels, max_pixels=args.max_pixels,
        attn_implementation=args.attn_implementation, local_files_only=args.local_files_only,
    )

    def generate():
        for start in range(0, len(records), args.batch_size):
            yield from reranker.rerank_batch(records[start:start + args.batch_size])
            print(f"Reranked {min(start + args.batch_size, len(records))}/{len(records)} queries", flush=True)

    write_jsonl_atomic(args.output, generate())


if __name__ == "__main__":
    main()
