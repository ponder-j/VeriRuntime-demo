"""Backend-neutral command, resource request and execution lifecycle records."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import math

from veriruntime.model import ExecutionStatus, TerminationReason


@dataclass(frozen=True)
class ExecutionSpec:
    argv: tuple[str, ...]
    cwd: str
    environment: dict[str, str]
    wall_time_limit: float | None = None
    cpu_request: float | None = None
    memory_request_mb: int | None = None
    metadata: dict = field(default_factory=dict)
    stdout_path: str | None = None
    stderr_path: str | None = None
    stdin_data: bytes | None = field(default=None, repr=False)

    def __post_init__(self):
        if not self.argv or any(not isinstance(arg, str) for arg in self.argv):
            raise ValueError('Execution requires an argv without shell interpretation')
        for value in (self.wall_time_limit, self.cpu_request):
            if value is not None and (isinstance(value, bool) or not math.isfinite(value) or value <= 0):
                raise ValueError('Execution time and CPU requests must be positive and finite')
        if self.memory_request_mb is not None and (
                type(self.memory_request_mb) is not int or self.memory_request_mb <= 0):
            raise ValueError('Execution memory request must be a positive integer')

    def record(self):
        # Record the input byte count rather than duplicating an upstream prompt.
        from veriruntime.model import to_data
        record = {key: value for key, value in to_data(self).items() if key != 'stdin_data'}
        # Execution needs the environment, but evidence must not duplicate
        # unrelated host tokens/credentials. Match the existing command receipt.
        record['environment'] = {key: self.environment[key] for key in (
            'LC_ALL', 'PATH', 'HOME', 'TMPDIR', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME',
            'DYLD_LIBRARY_PATH', 'JAVA') if key in self.environment}
        record['stdin_bytes'] = len(self.stdin_data) if self.stdin_data is not None else 0
        return record


@dataclass(frozen=True)
class ExecutionHandle:
    """Opaque infrastructure identity. Consumers must not inspect a local PID."""
    id: str
    backend: str


@dataclass(frozen=True)
class ExecutionMetrics:
    rss_bytes: int | None = None
    peak_sampled_rss_bytes: int | None = None
    cpu_seconds: float | None = None
    scope: str = 'unavailable'


@dataclass(frozen=True)
class ExecutionOutcome:
    handle_id: str
    backend: str
    exit_code: int | None
    stdout: str
    stderr: str
    start_time: str
    end_time: str
    wall_time_sec: float
    status: ExecutionStatus
    termination_reason: TerminationReason
    metrics: ExecutionMetrics = field(default_factory=ExecutionMetrics)
    diagnostics: dict = field(default_factory=dict)


class ExecutionBackend(ABC):
    """Execute the selected command; never choose tools, goals or cache entries.

    A backend must make the requested workspace available, retain its outputs
    there before cleanup, and return the same outcome on repeated waits. Cancel
    and cleanup are idempotent. Cleanup must release all owned execution resources.
    """
    name: str

    @abstractmethod
    async def start(self, spec: ExecutionSpec) -> ExecutionHandle: ...

    @abstractmethod
    async def wait(self, handle: ExecutionHandle) -> ExecutionOutcome: ...

    @abstractmethod
    async def cancel(self, handle: ExecutionHandle, reason=TerminationReason.USER_CANCEL): ...

    @abstractmethod
    async def collect_metrics(self, handle: ExecutionHandle) -> ExecutionMetrics: ...

    @abstractmethod
    async def cleanup(self, handle: ExecutionHandle): ...
