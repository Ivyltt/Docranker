"""Qwen2.5-VL multi-image LoRA supervised fine-tuning."""

from __future__ import annotations

import json

from .common import (assert_frozen_vision, load_model_and_processor, make_lora_config,
                     parser_for, prepare_run, save_training)
from .data import make_dataset


def main(argv: list[str] | None = None) -> None:
    parser = parser_for("sft")
    args = parser.parse_args(argv)
    try:
        config, rows, indices, manifest = prepare_run(args, "sft")
        if args.dry_run:
            print(json.dumps(manifest, indent=2, ensure_ascii=False))
            return
        model, processor = load_model_and_processor(config)
        from trl import SFTConfig, SFTTrainer

        training_args = SFTConfig(output_dir=args.output, **config["training"])
        trainer = SFTTrainer(
            model=model, processing_class=processor, args=training_args,
            train_dataset=make_dataset(rows, indices, "sft"),
            peft_config=make_lora_config(model, config),
        )
        assert_frozen_vision(trainer.model)
        trainer.train(resume_from_checkpoint=args.resume)
        save_training(trainer, processor, args.output, manifest)
    except (ValueError, RuntimeError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
