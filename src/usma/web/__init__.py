"""Local control-plane web backend for the Unified Solution Migration Analyzer.

This package exposes the existing analyzer functions as a thin FastAPI
app. It is opt-in (mounted only when ``sma serve --with-api`` is used)
and stays local-only by default. See ``web/PLAN_CONTROL_PLANE.md`` for
the full design.
"""

from .app import create_app

__all__ = ["create_app"]

__all__ = ["create_app"]
