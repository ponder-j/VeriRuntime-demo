"""Infrastructure execution contracts; no verifier selection or verdict policy."""
from .backend import ExecutionBackend, ExecutionHandle, ExecutionMetrics, ExecutionOutcome, ExecutionSpec
from .local import LocalExecutionBackend

__all__ = ['ExecutionBackend', 'ExecutionHandle', 'ExecutionMetrics', 'ExecutionOutcome',
           'ExecutionSpec', 'LocalExecutionBackend']
