"""Multi-image GRPO from a merged SFT checkpoint, with a fresh RL LoRA."""

from __future__ import annotations

import json
from pathlib import Path

from .common import (assert_frozen_vision, load_model_and_processor, make_lora_config,
                     parser_for, prepare_run, save_training, validate_grpo_checkpoint_alignment)
from .data import make_dataset
from .lazy_images import configure_dataset_images, lazy_image_manifest, select_lazy_grpo_trainer
from .rewards import format_rerank_reward, rerank_reward
from .trl_compat import compatibility_manifest, select_grpo_trainer


def validate_sft_checkpoint(model_name: str, allow_base_model: bool) -> None:
    if allow_base_model:
        return
    path = Path(model_name)
    if not path.is_dir() or not (path / "config.json").exists():
        raise ValueError("GRPO requires a local merged SFT checkpoint; run training.merge first or explicitly use --allow-base-model for the RL-only ablation")
    if (path / "adapter_config.json").exists():
        raise ValueError("merge the SFT adapter before GRPO; RL uses a new rank-64 adapter on SFT weights")
    if not any(path.glob("*.safetensors")) and not any(path.glob("pytorch_model*.bin")):
        raise ValueError("merged SFT checkpoint contains no model weights")
    manifest_path = path / "docreranker_training.json"
    if not manifest_path.is_file():
        raise ValueError("GRPO requires the completed SFT merge provenance; a local base model is not an SFT checkpoint")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (not isinstance(manifest, dict) or manifest.get("stage") != "sft" or manifest.get("merged") is not True
            or manifest.get("dry_run") is not False or type(manifest.get("global_step")) is not int
            or manifest["global_step"] < 1):
        raise ValueError("GRPO requires a completed, non-dry-run SFT adapter merge with positive training steps")


def main(argv: list[str] | None = None) -> None:
    parser = parser_for("grpo")
    args = parser.parse_args(argv)
    try:
        config, rows, indices, manifest = prepare_run(args, "grpo")
        skip_image_logs = config.get("skip_unused_image_logging", False)
        lazy_images = config.get("lazy_grpo_images", False)
        manifest["image_logging_compatibility"] = compatibility_manifest(skip_image_logs)
        manifest["image_decoding_compatibility"] = lazy_image_manifest(lazy_images)
        manifest["trainer_class"] = None  # A dry run does not select/import the optional runtime class.
        if args.dry_run:
            # A dry run is usable before SFT finishes; report, do not fabricate,
            # the fact that no trained model is available yet.
            manifest["checkpoint_exists"] = Path(config["model_name_or_path"], "config.json").is_file()
            print(json.dumps(manifest, indent=2, ensure_ascii=False))
            return
        validate_sft_checkpoint(config["model_name_or_path"], args.allow_base_model)
        from trl import GRPOConfig, GRPOTrainer

        trainer_class, manifest["image_logging_compatibility"] = select_grpo_trainer(
            GRPOTrainer, enabled=skip_image_logs,
        )
        trainer_class, manifest["image_decoding_compatibility"] = select_lazy_grpo_trainer(
            trainer_class, enabled=lazy_images,
        )
        manifest["trainer_class"] = f"{trainer_class.__module__}.{trainer_class.__qualname__}"
        training_args = GRPOConfig(output_dir=args.output, **config["training"])
        validate_grpo_checkpoint_alignment(config["training"], training_args.world_size, args.resume)
        protocol = manifest["generation_protocol"]
        if (training_args.generation_batch_size != protocol["generation_batch_size"]
                or training_args.steps_per_generation != protocol["steps_per_generation"]
                or training_args.world_size != protocol["world_size"]):
            raise ValueError("resolved TRL generation batch/topology differs from the validated sampling contract")
        model, processor = load_model_and_processor(config)
        processor.tokenizer.padding_side = "left"
        trainer = trainer_class(
            model=model, processing_class=processor, args=training_args,
            train_dataset=configure_dataset_images(make_dataset(rows, indices, "grpo"), enabled=lazy_images),
            reward_funcs=[rerank_reward, format_rerank_reward],
            peft_config=make_lora_config(model, config),
        )
        assert_frozen_vision(trainer.model)
        trainer.train(resume_from_checkpoint=args.resume)
        save_training(trainer, processor, args.output, manifest)
    except (ValueError, RuntimeError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
