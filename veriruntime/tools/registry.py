from pathlib import Path

from .base import ToolAdapter


class ToolRegistry:
    def __init__(self, adapters=()):
        self._adapters: dict[str, ToolAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ToolAdapter) -> None:
        if adapter.name in self._adapters:
            raise ValueError(f"Duplicate backend: {adapter.name}")
        self._adapters[adapter.name] = adapter

    def discover(self):
        return tuple(adapter.detect() for adapter in self._adapters.values())

    def profiles(self):
        return tuple(a.profile() for a in self._adapters.values())

    def compatible_tools(self, task):
        return tuple(a.profile() for a in self._adapters.values() if a.supports(task))

    def get(self, tool: str) -> ToolAdapter:
        return self._adapters[tool]

    def get_profile(self, tool: str):
        return self.get(tool).profile()


def default_registry(root: str | Path = ".", backend=None) -> ToolRegistry:
    import os
    from .cbmc import CBMCAdapter
    from .esbmc import ESBMCAdapter
    from .cpachecker import CPAcheckerAdapter
    adapters = (CBMCAdapter(root), ESBMCAdapter(root), CPAcheckerAdapter(root))
    backend = backend or os.environ.get('VRUN_BACKEND', 'native')
    if backend == 'docker':
        from .docker import DockerAdapter
        adapters = tuple(DockerAdapter(a) for a in adapters)
    elif backend != 'native':
        raise ValueError(f'Unsupported verifier backend: {backend}')
    return ToolRegistry(adapters)
