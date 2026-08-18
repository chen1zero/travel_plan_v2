"""HTTP API for the travel planning harness."""


def create_app(*args, **kwargs):
    """Import the application factory lazily to avoid assembly cycles."""
    from agent_app.api.app import create_app as factory

    return factory(*args, **kwargs)


__all__ = ["create_app"]
