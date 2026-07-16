"""Build LlamaIndex and Docling transformations from declarative settings."""

from __future__ import annotations

import ast
import importlib
from typing import Any


def import_class(qualified_name: str) -> type:
    if "." not in qualified_name:
        raise ValueError(f"Transformation must use a qualified class name: {qualified_name}")
    module_name, class_name = qualified_name.rsplit(".", 1)
    module = importlib.import_module(module_name)
    return getattr(module, class_name)


def get_transformations(specifications: list[Any] | None) -> list[Any]:
    transformations: list[Any] = []
    for specification in specifications or []:
        tokens = [specification] if isinstance(specification, str) else list(specification)
        if not tokens:
            continue
        class_name = str(tokens[0]).strip()
        parameter_text = " ".join(str(token) for token in tokens[1:]).strip()
        if not parameter_text and " " in class_name:
            class_name, parameter_text = class_name.split(" ", 1)
        parameters = ast.literal_eval(parameter_text) if parameter_text else {}
        if not isinstance(parameters, dict):
            raise ValueError(f"Transformation parameters must be a mapping: {specification!r}")
        transformations.append(import_class(class_name)(**parameters))
    return transformations
