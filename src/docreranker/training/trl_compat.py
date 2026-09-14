"""Opt-in, source-verified TRL compatibility; never mutate the installed trainer.

TRL 0.26.2 gathers original PIL images for completion logs even when those logs
are disabled. The upstream method has no smaller override point for this call.
This factory changes its one logging guard in a subclass, retaining the entire
numerical method and the original zero-argument super() binding.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import inspect
import types


TRL_VERSION = "0.26.2"
METHOD_NAME = "_generate_and_score_completions"
UPSTREAM_SOURCE_SHA256 = "3b15479f744f09373bcbfc4e0e7e49eaecbcd95b767b551fdc98c790e0574209"
ORIGINAL_FRAGMENT = '        if images is not None:\n            self._logs["images"].extend(gather_object(images))\n'
PATCHED_FRAGMENT = (
    '        if self.log_completions and images is not None:\n'
    '            self._logs["images"].extend(gather_object(images))\n'
)
PATCH_ID = "trl-0.26.2-skip-unused-image-logging-v1"


def compatibility_manifest(enabled: bool) -> dict:
    """Describe the requested change without importing optional training packages."""
    if type(enabled) is not bool:
        raise ValueError("skip_unused_image_logging must be a boolean")
    return {
        "enabled": enabled, "patch_id": PATCH_ID if enabled else None,
        "runtime_source_validated": False,
        "scope": "Skip only image-log gather when log_completions=false; reward/loss collectives are unchanged.",
    }


def select_grpo_trainer(base_trainer: type, *, enabled: bool = False) -> tuple[type, dict]:
    """Return the original class by default, or a tightly checked derived class.

    A version, source, or closure mismatch is an error; there is no silent
    fallback to an unverified patch. No module globals or classes are patched.
    """
    manifest = compatibility_manifest(enabled)
    if not enabled:
        return base_trainer, manifest
    version = importlib.metadata.version("trl")
    if version != TRL_VERSION:
        raise ValueError(f"{PATCH_ID} requires exact TRL {TRL_VERSION}; found {version}")
    original = getattr(base_trainer, METHOD_NAME)
    try:
        source = inspect.getsource(original)
    except (OSError, TypeError) as error:
        raise ValueError("cannot verify the installed TRL method source") from error
    source_sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
    if source_sha != UPSTREAM_SOURCE_SHA256:
        raise ValueError(f"TRL method source drift: expected {UPSTREAM_SOURCE_SHA256}, found {source_sha}")
    if source.count(ORIGINAL_FRAGMENT) != 1:
        raise ValueError("TRL image logging fragment must occur exactly once")
    if (original.__code__.co_freevars != ("__class__",) or not original.__closure__
            or original.__closure__[0].cell_contents is not base_trainer):
        raise ValueError("unexpected TRL method super()/closure binding")

    patched_source = source.replace(ORIGINAL_FRAGMENT, PATCHED_FRAGMENT, 1)
    # A temporary class gives the compiled method a __class__ free variable.
    # Reuse the ORIGINAL closure below: super() must still skip GRPOTrainer,
    # otherwise it would call GRPOTrainer._prepare_inputs recursively.
    namespace = dict(original.__globals__)
    filename = f"<{PATCH_ID}>"
    exec(compile("class _VerifiedSource:\n" + patched_source, filename, "exec", dont_inherit=True), namespace)
    template = getattr(namespace["_VerifiedSource"], METHOD_NAME)
    if template.__code__.co_freevars != original.__code__.co_freevars:
        raise ValueError("compiled TRL method closure changed unexpectedly")
    replacement = types.FunctionType(template.__code__, original.__globals__, original.__name__,
                                     original.__defaults__, original.__closure__)
    replacement.__kwdefaults__ = original.__kwdefaults__
    replacement.__annotations__ = dict(original.__annotations__)
    replacement.__doc__ = original.__doc__
    replacement.__qualname__ = f"GRPOTrainerWithoutUnusedImageLogging.{METHOD_NAME}"
    trainer = type("GRPOTrainerWithoutUnusedImageLogging", (base_trainer,), {
        "__module__": __name__, METHOD_NAME: replacement,
    })
    manifest.update(
        runtime_source_validated=True, trl_version=version,
        original_class=f"{base_trainer.__module__}.{base_trainer.__qualname__}",
        trainer_class=f"{trainer.__module__}.{trainer.__qualname__}",
        upstream_method_sha256=source_sha,
        patched_method_sha256=hashlib.sha256(patched_source.encode("utf-8")).hexdigest(),
        original_class_and_module_unchanged=True,
    )
    return trainer, manifest
