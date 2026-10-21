"""Custom backend: plug in your own data agent or Genie wrapper.

Set CUSTOM_BACKEND="package.module:function" on the hub. The function is an async callable
    async def ask(question: str, conversation_id: str = "") -> dict
that returns the dictionary described in backends/base.py. The module must be importable inside the hub image: put it under hub/ (for example
hub/my_backend.py, referenced as CUSTOM_BACKEND=my_backend:ask) or add it to requirements.txt as a package.

A minimal example is in integrations/genie/custom_backend_example.py."""
from __future__ import annotations

import importlib

from backends.base import failure
from settings import CUSTOM_BACKEND

NAME = "custom"
_fn = None


def _load():
    global _fn
    if _fn is None:
        module_name, _, func_name = CUSTOM_BACKEND.partition(":")
        if not module_name or not func_name:
            raise RuntimeError("CUSTOM_BACKEND must look like 'package.module:function'")
        _fn = getattr(importlib.import_module(module_name), func_name)
    return _fn


async def ask(question: str, conversation_id: str = "") -> dict:
    try:
        fn = _load()
    except Exception as exc:
        return failure(question, f"The custom backend could not be loaded ({type(exc).__name__}: {str(exc)[:200]}).", NAME)
    try:
        result = await fn(question, conversation_id)
    except Exception as exc:
        return failure(question, f"The custom backend failed ({type(exc).__name__}: {str(exc)[:200]}).", NAME)
    if not isinstance(result, dict):
        return failure(question, "The custom backend must return a dict (see backends/base.py).", NAME)
    result.setdefault("question", question)
    result.setdefault("backend", NAME)
    return result
