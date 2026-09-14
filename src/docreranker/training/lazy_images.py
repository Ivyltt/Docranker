"""Decode GRPO images only when TRL actually generates a new completion group.

The generation DataLoader repeats whole input batches while TRL reuses buffered
completion tensors. Keep those repeated rows as Image(decode=False) records,
and use the original datasets decoder immediately before generation/scoring.
This is neither an image-feature cache nor a change to numerical training.
"""
from __future__ import annotations

from typing import Any


def lazy_image_manifest(enabled: bool) -> dict[str, Any]:
    if type(enabled) is not bool:
        raise ValueError("lazy_grpo_images must be a boolean")
    return {
        "enabled": enabled, "dataset_image_decode": not enabled,
        "decode_timing": "generation_group" if enabled else "dataset_access",
        "decoder": "datasets.Image().decode_example",
        "runtime_class_selected": False,
        "scope": "Defer PIL decoding only; no change to sampling, images, generation, rewards or updates.",
    }


def configure_dataset_images(dataset: Any, *, enabled: bool = False) -> Any:
    """Change only Arrow's decoding metadata, before the dataset is accessed."""
    lazy_image_manifest(enabled)  # Strict Boolean validation; no optional imports.
    if not enabled:
        return dataset
    from datasets import Image, Sequence

    # make_dataset() has only constructed/selected the Arrow table so far.
    # This cast does not fetch rows or decode any images.
    return dataset.cast_column("images", Sequence(Image(decode=False)))


def decode_grpo_images(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy input rows and restore ordinary datasets PIL images in original order."""
    from datasets import Image as DatasetImage
    from PIL import Image as PILImage

    decoder = DatasetImage()
    prepared = []
    for row in inputs:
        copied = dict(row)
        if "images" in row and row["images"] is not None:
            if not isinstance(row["images"], list):
                raise ValueError("GRPO images must be a list")
            decoded = []
            for image in row["images"]:
                if isinstance(image, PILImage.Image):
                    decoded.append(image)
                elif isinstance(image, dict) and "path" in image and "bytes" in image:
                    # Exactly the same decoder as Image(decode=True), including
                    # load(), bytes-backed inputs, image mode and EXIF transpose.
                    decoded.append(decoder.decode_example(image))
                else:
                    raise ValueError("lazy GRPO image must be a datasets path/bytes record or a PIL image")
            copied["images"] = decoded
        prepared.append(copied)
    return prepared


def select_lazy_grpo_trainer(base_trainer: type, *, enabled: bool = False) -> tuple[type, dict[str, Any]]:
    """Layer a conventional decoding wrapper over the selected numerical trainer."""
    manifest = lazy_image_manifest(enabled)
    if not enabled:
        return base_trainer, manifest

    class LazyImageGRPOTrainer(base_trainer):
        def _generate_and_score_completions(self, inputs):
            return super()._generate_and_score_completions(decode_grpo_images(inputs))

    LazyImageGRPOTrainer.__qualname__ = "LazyImageGRPOTrainer"
    manifest.update(
        runtime_class_selected=True,
        base_class=f"{base_trainer.__module__}.{base_trainer.__qualname__}",
        trainer_class=f"{LazyImageGRPOTrainer.__module__}.{LazyImageGRPOTrainer.__qualname__}",
    )
    return LazyImageGRPOTrainer, manifest
