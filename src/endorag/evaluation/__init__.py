"""Evaluation strategies, runner, and manifest loading."""

from endorag.evaluation.manifest import ExperimentManifest, load_experiment_manifests
from endorag.evaluation.models import EvaluationStrategy, Prediction


def __getattr__(name: str):
    if name == "EvaluationRunner":
        from endorag.evaluation.runner import EvaluationRunner

        return EvaluationRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "EvaluationRunner",
    "EvaluationStrategy",
    "ExperimentManifest",
    "Prediction",
    "load_experiment_manifests",
]
