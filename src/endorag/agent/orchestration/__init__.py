__all__ = ["build_graph", "run_endorag_workflow"]


def build_graph(*args, **kwargs):
    from endorag.agent.orchestration.graph import build_graph as _build

    return _build(*args, **kwargs)


async def run_endorag_workflow(*args, **kwargs):
    from endorag.agent.orchestration.runner import run_endorag_workflow as _run

    return await _run(*args, **kwargs)
