class ComputeEdgeError(Exception):
    """Base exception for all ComputeEdge errors."""


class AnalysisError(ComputeEdgeError):
    """Failed to analyze repository."""


class PricingError(ComputeEdgeError):
    """Failed to match plans or calculate costs."""


class DeploymentError(ComputeEdgeError):
    """Failed during deployment."""

    def __init__(self, message: str, build_log: str | None = None,
                 suggestion: str | None = None, retry_hint: str | None = None,
                 diagnostics=None):
        super().__init__(message)
        self.build_log = build_log
        self.suggestion = suggestion
        self.retry_hint = retry_hint
        self.diagnostics = diagnostics


class ConfigError(ComputeEdgeError):
    """Missing or invalid configuration."""


class GitCloneError(ComputeEdgeError):
    """Failed to clone a git repository."""


class SSHError(ComputeEdgeError):
    """SSH connection or command execution failed."""


class ProviderAPIError(ComputeEdgeError):
    """Provider API returned an error."""


class WebhookError(ComputeEdgeError):
    """Failed to set up or remove webhook."""
