class AgentError(Exception):
    """Base application error."""


class PlanningError(AgentError):
    """Raised when a task cannot be planned."""


class SafetyError(AgentError):
    """Raised when a task is blocked by policy."""


class AdapterExecutionError(AgentError):
    """Raised when an integration adapter fails."""
