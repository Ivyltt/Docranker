"""Merge a saved SFT/RL LoRA into its base weights using PEFT safe_merge."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .common import check_training_dependencies


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", required=True, help="local PEFT adapter checkpoint")
    parser.add_argument("--output", required=True, help="new directory for merged model")
    parser.add_argument("--base-model", help="override adapter base path if it has moved")
    parser.add_argument("--dtype", choices=["float32", "bfloat16", "float16"], default="bfloat16")
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--dry-run", action="store_true", help="validate adapter metadata only")
    args = parser.parse_args(argv)
    try:
        adapter = Path(args.adapter)
        metadata = json.loads((adapter / "adapter_config.json").read_text(encoding="utf-8"))
        base = args.base_model or metadata.get("base_model_name_or_path")
        if not base:
            raise ValueError("adapter does not identify a base model; supply --base-model")
        if not any(adapter.glob("adapter_model.*")):
            raise ValueError("adapter checkpoint has no adapter weights")
        output = Path(args.output)
        if output.exists() and any(output.iterdir()):
            raise ValueError("merge output must be a new or empty directory")
        if args.dry_run:
            print(json.dumps({"adapter": str(adapter.resolve()), "base_model": base,
                              "output": str(output.resolve()), "dry_run": True}, indent=2))
            return
        check_training_dependencies()
        import torch
        from peft import PeftModel
        from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

        model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            base, dtype=getattr(torch, args.dtype), device_map=args.device,
        )
        model = PeftModel.from_pretrained(model, str(adapter), is_trainable=False)
        merged = model.merge_and_unload(safe_merge=True)
        merged.config.use_cache = True
        merged.save_pretrained(output, safe_serialization=True, max_shard_size="4GB")
        processor_source = str(adapter) if (adapter / "preprocessor_config.json").exists() else base
        AutoProcessor.from_pretrained(processor_source).save_pretrained(output)
        previous = adapter / "docreranker_training.json"
        manifest = json.loads(previous.read_text(encoding="utf-8")) if previous.exists() else {}
        manifest.update({"merged": True, "adapter": str(adapter.resolve()), "merge_base_model": base})
        (output / "docreranker_training.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    except (ValueError, RuntimeError, OSError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
