"""Real, tiny-model GPU integration test; downloads processor files, not 7B weights.

The random model has no ranking skill. The GRPO test includes a generated-token
probe reward to obtain a nonzero policy gradient; production ranking rewards
are evaluated alongside it. This is plumbing validation, never task accuracy.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="new integration-test artifact directory")
    parser.add_argument("--processor", default="Qwen/Qwen2.5-VL-7B-Instruct")
    args = parser.parse_args(argv)
    output = Path(args.output).resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("output directory must be empty")

    from .common import (assert_frozen_vision, check_training_dependencies, load_model_and_processor,
                         make_lora_config, prepare_run, save_training)
    check_training_dependencies()
    import torch
    from PIL import Image
    from transformers import (AutoProcessor, Qwen2_5_VLConfig, Qwen2_5_VLForConditionalGeneration,
                              set_seed)
    from trl import GRPOConfig, GRPOTrainer

    from . import merge, sft
    from .data import make_dataset
    from .rewards import format_rerank_reward, rerank_reward

    if not torch.cuda.is_available():
        parser.error("run inside a CUDA GPU allocation")
    set_seed(42)
    output.mkdir(parents=True, exist_ok=True)
    processor = AutoProcessor.from_pretrained(args.processor, min_pixels=3136, max_pixels=3136)
    tokenizer = processor.tokenizer
    config = Qwen2_5_VLConfig(
        text_config={
            "vocab_size": len(tokenizer), "hidden_size": 64, "intermediate_size": 128,
            "num_hidden_layers": 2, "num_attention_heads": 4, "num_key_value_heads": 2,
            "max_position_embeddings": 2048,
            "rope_scaling": {"type": "mrope", "mrope_section": [2, 3, 3]},
            "pad_token_id": tokenizer.pad_token_id, "bos_token_id": tokenizer.bos_token_id,
            "eos_token_id": tokenizer.eos_token_id,
        },
        vision_config={
            "depth": 2, "hidden_size": 32, "intermediate_size": 64, "out_hidden_size": 64,
            "num_heads": 4, "patch_size": 14, "temporal_patch_size": 2,
            "spatial_merge_size": 2, "window_size": 112, "fullatt_block_indexes": [1],
        },
        image_token_id=tokenizer.convert_tokens_to_ids("<|image_pad|>"),
        video_token_id=tokenizer.convert_tokens_to_ids("<|video_pad|>"),
        vision_start_token_id=tokenizer.convert_tokens_to_ids("<|vision_start|>"),
        vision_end_token_id=tokenizer.convert_tokens_to_ids("<|vision_end|>"),
    )
    tiny_base = output / "tiny-base"
    base = Qwen2_5_VLForConditionalGeneration(config)
    parameters = sum(p.numel() for p in base.parameters())
    base.save_pretrained(tiny_base)
    processor.save_pretrained(tiny_base)
    del base

    paths = []
    for index, color in enumerate(("red", "blue")):
        path = output / f"image-{index + 1}.png"
        Image.new("RGB", (56, 56), color).save(path)
        paths.append(str(path))
    sft_rows, grpo_rows = [], []
    for index in range(4):
        record = {"query_id": f"smoke-{index}", "query": "Which image is red?", "images": paths,
                  "positive_ids": [1], "subset": "synthetic-smoke"}
        grpo_rows.append(record)
        sft_rows.append({**record, "messages": [
            {"role": "user", "content": "Which image is red? Image 1: <image> Image 2: <image>"},
            {"role": "assistant", "content": "<think>Image 1 is red; image 2 is blue.</think><answer>[1,2]</answer>"},
        ]})
    for name, rows in (("sft", sft_rows), ("grpo", grpo_rows)):
        (output / f"{name}.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    common = {
        "model_name_or_path": str(tiny_base), "dtype": "float32", "attn_implementation": "sdpa",
        "processor": {"min_pixels": 3136, "max_pixels": 3136},
        "lora": {"r": 8, "alpha": 32, "dropout": 0.0, "target_modules": ["q_proj", "v_proj"]},
        "sampling": {"enabled": True, "bins": 10},
        "training": {
            "num_train_epochs": 1, "max_steps": 2, "learning_rate": 0.001,
            "per_device_train_batch_size": 2, "gradient_accumulation_steps": 1,
            "bf16": False, "fp16": False, "gradient_checkpointing": True,
            "gradient_checkpointing_kwargs": {"use_reentrant": False},
            "logging_steps": 1, "save_strategy": "no", "report_to": "none",
            "remove_unused_columns": False, "seed": 42, "dataloader_num_workers": 0,
        },
    }
    sft_config = {**common, "training": {**common["training"], "max_length": None,
                                        "packing": False, "completion_only_loss": True}}
    sft_config_path = output / "sft-config.json"
    sft_config_path.write_text(json.dumps(sft_config), encoding="utf-8")
    adapter = output / "sft-adapter"
    sft.main(["--config", str(sft_config_path), "--data", str(output / "sft.jsonl"), "--output", str(adapter)])
    sft_manifest = json.loads((adapter / "docreranker_training.json").read_text())
    assert sft_manifest["global_step"] == 2
    merged = output / "sft-merged"
    merge.main(["--adapter", str(adapter), "--output", str(merged), "--dtype", "float32"])
    assert (merged / "config.json").is_file() and not (merged / "adapter_config.json").exists()

    grpo_config = {
        **common, "model_name_or_path": str(merged),
        "lora": {**common["lora"], "r": 64, "alpha": 128},
        "training": {**common["training"], "num_generations": 2, "num_iterations": 2,
                     "max_completion_length": 8, "temperature": 1.0, "beta": 0.04,
                     "loss_type": "grpo", "reward_weights": [1.0, 1.0], "use_vllm": False},
    }
    grpo_config_path = output / "grpo-config.json"
    grpo_config_path.write_text(json.dumps(grpo_config), encoding="utf-8")
    grpo_output = output / "grpo-adapter"
    namespace = argparse.Namespace(config=str(grpo_config_path), data=str(output / "grpo.jsonl"),
                                  output=str(grpo_output), model=None, exclude_queries=None,
                                  dry_run=False, resume=None, allow_base_model=False)
    run_config, rows, indices, manifest = prepare_run(namespace, "grpo")
    model, processor = load_model_and_processor(run_config)
    processor.tokenizer.padding_side = "left"

    def generated_token_probe_reward(completion_ids, **kwargs):
        # The sampled token really comes from model.generate. Its scalar value
        # creates a nonconstant reward even though a random model cannot rank.
        return [float(ids[0] % 997) / 997 if ids else 0.0 for ids in completion_ids]

    training = {**run_config["training"], "reward_weights": [1.0, 1.0, 1.0]}
    trainer = GRPOTrainer(
        model=model, processing_class=processor,
        args=GRPOConfig(output_dir=str(grpo_output), **training),
        train_dataset=make_dataset(rows, indices, "grpo"),
        reward_funcs=[rerank_reward, format_rerank_reward, generated_token_probe_reward],
        peft_config=make_lora_config(model, run_config),
    )
    assert_frozen_vision(trainer.model)
    before = {name: parameter.detach().cpu().clone() for name, parameter in trainer.model.named_parameters()
              if parameter.requires_grad}
    trainer.train()
    changed = sum(not torch.equal(before[name], parameter.detach().cpu())
                  for name, parameter in trainer.model.named_parameters() if name in before)
    assert trainer.state.global_step == 2
    assert changed > 0, "GRPO must update an adapter using generated-token probe reward"
    manifest["smoke_probe_reward"] = True
    save_training(trainer, processor, str(grpo_output), manifest)
    result = {"status": "passed", "parameters": parameters, "device": torch.cuda.get_device_name(),
              "sft_steps": 2, "merged_sft_checkpoint": str(merged), "grpo_steps": 2,
              "grpo_changed_tensors": changed, "images_per_example": 2,
              "note": "Random tiny model; GRPO uses an additional generated-token probe reward. No task-quality claim."}
    (output / "smoke-result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
