"""Tool context — DI wrapper around the cross-tenant read tools.

Builders receive a ``ToolCtx`` instance instead of importing tool
modules directly. Tests inject a ``ToolCtx(handlers={...})`` with
mocked callables; production wiring uses :func:`production_ctx`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict


@dataclass
class ToolCtx:
    """Maps a tool-name to a handler callable.

    Each handler accepts kwargs and returns the same shape as the
    underlying tool's ``handler(**kwargs)``.
    """
    handlers: Dict[str, Callable[..., Any]] = field(default_factory=dict)

    def call(self, tool_name: str, **kwargs: Any) -> Any:
        if tool_name not in self.handlers:
            raise KeyError(f"tool {tool_name!r} not available in this context")
        return self.handlers[tool_name](**kwargs)

    # Convenience accessors used by builders. Each forwards to ``call``.
    def list_aws_resources(self, **kw: Any) -> Any:
        return self.call("list_aws_resources", **kw)

    def read_customer_tenant_state(self, **kw: Any) -> Any:
        return self.call("read_customer_tenant_state", **kw)

    def read_customer_pipeline(self, **kw: Any) -> Any:
        return self.call("read_customer_pipeline", **kw)

    def read_customer_ontology(self, **kw: Any) -> Any:
        return self.call("read_customer_ontology", **kw)

    def read_aria_conversations(self, **kw: Any) -> Any:
        return self.call("read_aria_conversations", **kw)


def production_ctx() -> ToolCtx:
    """Wire the real tool handlers. Imported lazily so unit tests
    that build a custom :class:`ToolCtx` don't pull in AWS clients."""
    from nexus.overwatch_v2.tools.read_tools import (
        list_aws_resources, read_aria_conversations, read_customer_ontology,
        read_customer_pipeline, read_customer_tenant_state,
    )
    return ToolCtx(handlers={
        "list_aws_resources": list_aws_resources.handler,
        "read_customer_tenant_state": read_customer_tenant_state.handler,
        "read_customer_pipeline": read_customer_pipeline.handler,
        "read_customer_ontology": read_customer_ontology.handler,
        "read_aria_conversations": read_aria_conversations.handler,
    })
