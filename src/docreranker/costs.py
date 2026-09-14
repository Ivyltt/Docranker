"""Reproducible, no-key annotation estimates; optional live public price refresh."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
from pathlib import Path
import urllib.request

MODELS_URL = "https://openrouter.ai/api/v1/models"
PRICE_DATE = "2026-09-13"


@dataclass(frozen=True)
class ModelPrice:
    model: str
    input_per_million: float
    output_per_million: float
    retrieved_at: str = PRICE_DATE
    source: str = MODELS_URL

    def __post_init__(self):
        for value in (self.input_per_million, self.output_per_million):
            if not math.isfinite(value) or value < 0:
                raise ValueError("Prices must be finite and nonnegative")

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("Token counts cannot be negative")
        return (input_tokens * self.input_per_million +
                output_tokens * self.output_per_million) / 1_000_000


SNAPSHOT = {
    p.model: p for p in [
        ModelPrice("anthropic/claude-sonnet-4", 3.0, 15.0),
        ModelPrice("google/gemini-2.5-flash", 0.30, 2.50),
        ModelPrice("google/gemini-2.5-flash-lite", 0.10, 0.40),
    ]
}


def live_price(model: str) -> ModelPrice:
    """GET only: public metadata, no API key and no inference charges."""
    with urllib.request.urlopen(MODELS_URL, timeout=30) as response:
        models = json.load(response)["data"]
    for item in models:
        if item["id"] == model:
            if "image" not in item["architecture"]["input_modalities"]:
                raise ValueError(f"Model does not accept images: {model}")
            pricing = item["pricing"]
            if float(pricing.get("request", 0) or 0) != 0:
                raise ValueError("Per-request pricing is unsupported by this estimator")
            return ModelPrice(model, float(Decimal(pricing["prompt"]) * 1_000_000),
                              float(Decimal(pricing["completion"]) * 1_000_000),
                              datetime.now(timezone.utc).isoformat())
    raise ValueError(f"Model not found in public catalog: {model}")


def estimate_cost(price: ModelPrice, samples: int = 7200, candidates: int = 5,
                  image_tokens: int = 1500, analysis_input: int = 350,
                  analysis_output: int = 100, refine_input: int = 2000,
                  refine_output: int = 150, retry_margin: float = 0.20) -> dict:
    values = (samples, candidates, image_tokens, analysis_input, analysis_output,
              refine_input, refine_output)
    if any(x < 0 for x in values) or candidates < 1:
        raise ValueError("Counts must be nonnegative; candidates must be positive")
    if not math.isfinite(retry_margin) or retry_margin < 0:
        raise ValueError("Retry margin must be finite and nonnegative")
    analysis_calls = samples * candidates
    input_tokens = analysis_calls * (image_tokens + analysis_input) + samples * refine_input
    output_tokens = analysis_calls * analysis_output + samples * refine_output
    base = price.cost(input_tokens, output_tokens)
    return {"model": price.model, "price": asdict(price), "samples": samples,
            "candidates": candidates, "analysis_calls": analysis_calls,
            "refinement_calls": samples, "total_calls": analysis_calls + samples,
            "assumptions": {"image_tokens_per_page": image_tokens,
                            "analysis_text_input_tokens": analysis_input,
                            "analysis_output_tokens": analysis_output,
                            "refinement_input_tokens": refine_input,
                            "refinement_output_tokens": refine_output},
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "base_usd": round(base, 6), "retry_margin": retry_margin,
            "with_margin_usd": round(base * (1 + retry_margin), 6),
            "scope": "Inference credits only; excludes GPU, taxes and credit purchase fees",
            "estimate_only": True}


def scenarios(price: ModelPrice, analysis_max_tokens=250, refine_max_tokens=500, **kwargs) -> dict:
    return {"expected": estimate_cost(price, analysis_output=min(100, analysis_max_tokens),
                                      refine_output=min(150, refine_max_tokens), **kwargs),
            "conservative_output_caps": estimate_cost(
                price, analysis_output=analysis_max_tokens, refine_output=refine_max_tokens, **kwargs)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="all", help="Model slug or all")
    parser.add_argument("--samples", type=int, default=7200)
    parser.add_argument("--candidates", type=int, default=5)
    parser.add_argument("--image-tokens", type=int, default=1500)
    parser.add_argument("--analysis-input", type=int, default=350)
    parser.add_argument("--refine-input", type=int, default=2000)
    parser.add_argument("--retry-margin", type=float, default=0.20)
    parser.add_argument("--live", action="store_true", help="Refresh public catalog prices (no key)")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    names = list(SNAPSHOT) if args.model == "all" else [args.model]
    output = []
    for name in names:
        price = live_price(name) if args.live else SNAPSHOT.get(name)
        if price is None:
            parser.error("Unknown snapshot model: use --live for another model")
        output.append(scenarios(price, samples=args.samples, candidates=args.candidates,
                                image_tokens=args.image_tokens, analysis_input=args.analysis_input,
                                refine_input=args.refine_input, retry_margin=args.retry_margin))
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
