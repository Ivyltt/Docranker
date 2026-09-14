"""Shared configuration, reproducibility records and lazy training imports."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
from typing import Any

from .data import load_rows, resolution_balanced_indices
from .sampling import quantile_without_replacement_indices

PINNED_VERSIONS = {
    "transformers": "4.57.3", "trl": "0.26.2", "peft": "0.18.0",
    "accelerate": "1.12.0", "datasets": "4.4.1",
    "torch": "2.9.1", "torchvision": "0.24.1",
}
DEFAULT_MODEL = "Qwen/Qwen2.5-VL-7B-Instruct"


def grpo_batch_contract(training: dict[str, Any], world_size: int) -> dict[str, int]:
    """Describe the supported, optimizer-aligned native GRPO sampling cycle.

    Generation can span several optimizer batches. Its old log probabilities
    remain fixed through every reuse; a larger cycle is not an equivalent
    optimization trajectory even when the optimizer batch stays unchanged.
    """
    values = {"world_size": world_size,
              "per_device_train_batch_size": training["per_device_train_batch_size"],
              "gradient_accumulation_steps": training["gradient_accumulation_steps"],
              "num_generations": training.get("num_generations", 2),
              "num_iterations": training.get("num_iterations", 1)}
    for key, value in values.items():
        if type(value) is not int or value < 1:
            raise ValueError(f"{key} must be a positive integer")
    if "generation_batch_size" in training and "steps_per_generation" in training:
        raise ValueError("configure generation_batch_size or steps_per_generation, not both")
    microbatch = values["per_device_train_batch_size"] * world_size
    accumulation = values["gradient_accumulation_steps"]
    generation = training.get("generation_batch_size")
    if generation is None:
        steps = training.get("steps_per_generation", accumulation)
        if type(steps) is not int or steps < 1:
            raise ValueError("steps_per_generation must be a positive integer")
        generation = microbatch * steps
    if type(generation) is not int or generation < 1:
        raise ValueError("generation_batch_size must be a positive integer")
    if generation % microbatch or generation % values["num_generations"]:
        raise ValueError("GRPO generation batch must divide evenly into distributed batches and generation groups")
    optimizer = microbatch * accumulation
    if generation % optimizer:
        raise ValueError("GRPO generation batch must be a positive multiple of the optimizer global batch")
    updates = generation * values["num_iterations"] // optimizer
    return {"world_size": world_size, "optimizer_global_batch": optimizer,
            "generation_batch_size": generation,
            "unique_sampled_positions_per_generation": generation // values["num_generations"],
            "num_generations": values["num_generations"], "num_iterations": values["num_iterations"],
            "steps_per_generation": generation // microbatch,
            "optimizer_steps_per_generation_cycle": updates,
            "max_old_logprob_lag_optimizer_updates": updates - 1}


def validate_grpo_checkpoint_alignment(training: dict[str, Any], world_size: int,
                                       resume: str | None = None) -> None:
    """The ephemeral completion buffer cannot be resumed mid enlarged cycle."""
    contract = grpo_batch_contract(training, world_size)
    if contract["generation_batch_size"] == contract["optimizer_global_batch"]:
        return  # Preserve existing diagnostic checkpoint behavior.
    cycle = contract["optimizer_steps_per_generation_cycle"]
    if training.get("save_strategy", "steps") == "steps":
        interval = training.get("save_steps", 500)
        if type(interval) is not int or interval < 1 or interval % cycle:
            raise ValueError(f"GRPO save_steps must be a positive multiple of the {cycle}-update generation "
                             "cycle; choose an explicit aligned --save-steps (e.g. 52 for cycle 4)")
    if resume:
        state = json.loads(Path(resume, "trainer_state.json").read_text(encoding="utf-8"))
        step = state.get("global_step")
        if type(step) is not int or step < 0 or step % cycle:
            raise ValueError(f"GRPO resume checkpoint must end a complete {cycle}-update generation cycle")


def parser_for(stage: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=f"DocReranker {stage.upper()} training")
    parser.add_argument("--config", required=True, help="JSON training configuration")
    parser.add_argument("--data", required=True, help="SFT or RL training JSONL")
    parser.add_argument("--output", required=True, help="checkpoint directory")
    parser.add_argument("--model", help="override the model / merged SFT checkpoint in config")
    parser.add_argument("--exclude-queries", help="evaluation queries JSONL; reject overlapping query_id")
    parser.add_argument("--resume", help="resume a Trainer checkpoint, including optimizer state")
    parser.add_argument("--dry-run", action="store_true", help="validate data/config only; no model loading or GPU")
    if stage == "grpo":
        parser.add_argument("--allow-base-model", action="store_true", help="explicit RL-only ablation without SFT")
    return parser


def read_config(path: str | Path, stage: str) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as handle:
        config = json.load(handle)
    if not isinstance(config, dict):
        raise ValueError("training config must be a JSON object")
    allowed = {"model_name_or_path", "processor", "lora", "training", "sampling", "dtype", "attn_implementation"}
    if stage == "grpo":
        allowed.update({"skip_unused_image_logging", "lazy_grpo_images"})
    if set(config) - allowed:
        raise ValueError(f"unknown configuration keys: {sorted(set(config) - allowed)}")
    if type(config.get("skip_unused_image_logging", False)) is not bool:
        raise ValueError("skip_unused_image_logging must be a boolean")
    if type(config.get("lazy_grpo_images", False)) is not bool:
        raise ValueError("lazy_grpo_images must be a boolean")
    for key in ("processor", "lora", "training", "sampling"):
        if not isinstance(config.get(key), dict):
            raise ValueError(f"configuration requires a {key} object")
    if not isinstance(config.get("model_name_or_path"), str) or not config["model_name_or_path"]:
        raise ValueError("model_name_or_path is required")
    if config.get("dtype", "bfloat16") not in {"bfloat16", "float16", "float32"}:
        raise ValueError("dtype must be bfloat16, float16 or float32")
    if config.get("attn_implementation", "sdpa") not in {"sdpa", "eager", "flash_attention_2"}:
        raise ValueError("unsupported attn_implementation")
    lora = config["lora"]
    if set(lora) - {"r", "alpha", "dropout", "target_modules"}:
        raise ValueError("unknown LoRA configuration keys")
    if type(lora.get("r")) is not int or lora["r"] < 1 or lora.get("alpha", 0) <= 0:
        raise ValueError("LoRA requires positive r and alpha")
    if not 0 <= lora.get("dropout", 0.0) < 1:
        raise ValueError("LoRA dropout must be in [0, 1)")
    if not isinstance(lora.get("target_modules"), list) or not lora["target_modules"]:
        raise ValueError("LoRA target_modules must be a nonempty list")
    processor = config["processor"]
    if set(processor) - {"min_pixels", "max_pixels"}:
        raise ValueError("processor only supports min_pixels and max_pixels")
    if not 0 < processor.get("min_pixels", 0) <= processor.get("max_pixels", 0):
        raise ValueError("processor requires 0 < min_pixels <= max_pixels")
    sampling = config["sampling"]
    if set(sampling) - {"enabled", "bins", "epoch_size", "method"}:
        raise ValueError("unknown sampling configuration keys")
    if sampling.get("method", "equal_width_log2") not in {"equal_width_log2", "quantile_without_replacement"}:
        raise ValueError("sampling.method must be equal_width_log2 or quantile_without_replacement")
    training = config["training"]
    if "output_dir" in training:
        raise ValueError("pass output_dir through --output, not training config")
    if training.get("remove_unused_columns", False) is not False:
        raise ValueError("remove_unused_columns must be false to preserve images and reward labels")
    if training.get("learning_rate", 0) <= 0 or training.get("num_train_epochs", 0) <= 0:
        raise ValueError("learning_rate and num_train_epochs must be positive")
    for key in ("per_device_train_batch_size", "gradient_accumulation_steps"):
        if type(training.get(key)) is not int or training[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    if training.get("bf16", False) != (config.get("dtype", "bfloat16") == "bfloat16"):
        raise ValueError("training.bf16 must agree with dtype")
    if training.get("fp16", False) != (config.get("dtype", "bfloat16") == "float16"):
        raise ValueError("training.fp16 must agree with dtype")
    if training.get("push_to_hub", False):
        raise ValueError("training scripts save locally; publishing is a separate action")
    if stage == "sft":
        if training.get("max_length") is not None or training.get("packing", False):
            raise ValueError("multimodal SFT requires max_length=null and packing=false to preserve image tokens")
        if training.get("completion_only_loss", True) is not True or training.get("assistant_only_loss", False):
            raise ValueError("SFT uses prompt/completion with completion_only_loss=true and assistant_only_loss=false")
    else:
        n = training.get("num_generations", 2)
        if type(n) is not int or n < 2:
            raise ValueError("GRPO num_generations must be at least 2")
        if training.get("max_prompt_length") is not None:
            raise ValueError("do not truncate multimodal prompts; lower processor.max_pixels instead")
        if training.get("use_vllm", False) or training.get("use_transformers_paged", False):
            raise ValueError("this pinned implementation uses native Transformers generation")
        grpo_batch_contract(training, int(os.environ.get("WORLD_SIZE", "1")))
        if training.get("reward_weights", [1.0, 1.0]) != [1.0, 1.0]:
            raise ValueError("the documented reward is result_reward + format_reward, with equal weights")
    return config


def prepare_run(args: argparse.Namespace, stage: str):
    config = read_config(args.config, stage)
    if args.model:
        config["model_name_or_path"] = args.model
    rows = load_rows(args.data, stage, args.exclude_queries)
    sampling = config["sampling"]
    seed = config["training"].get("seed", 42)
    if sampling.get("enabled", True):
        sampler = (quantile_without_replacement_indices
                   if sampling.get("method", "equal_width_log2") == "quantile_without_replacement"
                   else resolution_balanced_indices)
        indices, report = sampler(
            rows, bins=sampling.get("bins", 10), seed=seed, epoch_size=sampling.get("epoch_size"),
        )
    else:
        indices, report = list(range(len(rows))), {"enabled": False, "sampled_examples": len(rows)}
    if stage == "grpo":
        contract = grpo_batch_contract(config["training"], int(os.environ.get("WORLD_SIZE", "1")))
        distinct_per_batch = contract["unique_sampled_positions_per_generation"]
        if len(indices) < distinct_per_batch:
            raise ValueError(f"GRPO needs at least {distinct_per_batch} sampled rows to form one generation batch")
    if not args.dry_run and not args.resume and Path(args.output).exists() and any(Path(args.output).iterdir()):
        raise ValueError("output directory is not empty; choose a new path or pass --resume")
    manifest = {
        "stage": stage,
        "config": config,
        "data": str(Path(args.data).resolve()),
        "data_sha256": hashlib.sha256(Path(args.data).read_bytes()).hexdigest(),
        "source_examples": len(rows),
        "sampling": report,
        "source_model": config["model_name_or_path"],
        "excluded_queries": str(Path(args.exclude_queries).resolve()) if args.exclude_queries else None,
        "versions": PINNED_VERSIONS,
        "dry_run": args.dry_run,
    }
    if stage == "grpo":
        manifest["allow_base_model"] = args.allow_base_model
        used_positions = len(indices) // distinct_per_batch * distinct_per_batch
        manifest["generation_protocol"] = {
            **contract, "sampled_positions": len(indices), "used_positions_per_epoch": used_positions,
            "dropped_tail_positions_per_epoch": len(indices) - used_positions,
            "completions_per_epoch": used_positions * contract["num_generations"],
            "training_exposures_per_epoch": used_positions * contract["num_generations"] * contract["num_iterations"],
            "optimizer_updates_per_epoch": (used_positions * contract["num_generations"]
                                            * contract["num_iterations"] // contract["optimizer_global_batch"]),
            "limitations": "Larger generation batches change local shuffle groups and retain old policy log probabilities "
                            "for more optimizer updates. Matching optimizer batch and sample counts does not imply an "
                            "identical optimization trajectory. Counts describe sampled positions, which may repeat queries.",
        }
    return config, rows, indices, manifest


def check_training_dependencies() -> None:
    mismatches = []
    for package, expected in PINNED_VERSIONS.items():
        try:
            installed = importlib.metadata.version(package).split("+", 1)[0]
        except importlib.metadata.PackageNotFoundError:
            installed = "missing"
        if installed != expected:
            mismatches.append(f"{package}: expected {expected}, found {installed}")
    if mismatches:
        raise RuntimeError("Install the pinned training extra (pip install -e '.[train]'). " + "; ".join(mismatches))


def load_model_and_processor(config: dict[str, Any]):
    check_training_dependencies()
    import torch
    from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration, set_seed

    if not torch.cuda.is_available():
        raise RuntimeError("GPU training requires an allocated CUDA device; use --dry-run for CPU validation")
    if config.get("dtype", "bfloat16") == "bfloat16" and not torch.cuda.is_bf16_supported():
        raise RuntimeError("this GPU does not support bfloat16; set dtype=float16, bf16=false and fp16=true")
    set_seed(config["training"].get("seed", 42))
    processor = AutoProcessor.from_pretrained(config["model_name_or_path"], **config["processor"])
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        config["model_name_or_path"],
        dtype=getattr(torch, config.get("dtype", "bfloat16")),
        attn_implementation=config.get("attn_implementation", "sdpa"),
    )
    model.config.use_cache = False
    # No device_map='auto': Trainer/Accelerate owns placement, including DDP.
    for parameter in model.visual.parameters():
        parameter.requires_grad_(False)
    return model, processor


def make_lora_config(model, config: dict[str, Any]):
    from peft import LoraConfig

    suffixes = config["lora"]["target_modules"]
    targets = [
        name for name, module in model.named_modules()
        if "language_model" in name.split(".") and name.rsplit(".", 1)[-1] in suffixes
        and hasattr(module, "weight") and getattr(module.weight, "ndim", 0) == 2
    ]
    if not targets:
        raise ValueError("no language-model LoRA target modules matched; check model architecture")
    missing = set(suffixes) - {name.rsplit(".", 1)[-1] for name in targets}
    if missing:
        raise ValueError(f"LoRA target modules did not match: {sorted(missing)}")
    return LoraConfig(
        r=config["lora"]["r"], lora_alpha=config["lora"]["alpha"],
        lora_dropout=config["lora"].get("dropout", 0.0),
        target_modules=targets, bias="none", task_type="CAUSAL_LM",
    )


def save_training(trainer, processor, output: str, manifest: dict[str, Any]) -> None:
    trainer.save_model(output)
    trainer.save_state()
    if trainer.is_world_process_zero():
        processor.save_pretrained(output)
        manifest["dry_run"] = False
        manifest["global_step"] = trainer.state.global_step
        manifest["trainable_parameters"] = sum(p.numel() for p in trainer.model.parameters() if p.requires_grad)
        Path(output, "docreranker_training.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
        )


def assert_frozen_vision(model) -> None:
    unexpected = [name for name, p in model.named_parameters() if ".visual." in name and p.requires_grad]
    if unexpected:
        raise RuntimeError(f"vision parameters unexpectedly trainable: {unexpected[:3]}")
    if not any(p.requires_grad for p in model.parameters()):
        raise RuntimeError("model has no trainable parameters")
