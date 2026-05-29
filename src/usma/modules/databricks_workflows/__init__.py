"""Databricks workflows (jobs + clusters) analysis module.

This module mirrors the shape of :mod:`~..pipelines` but targets
Databricks Jobs / Workflows + interactive clusters via
``databricks.sdk.WorkspaceClient``. It is **not** a generalization of
``pipelines`` — per ADR D5 we keep ``pipelines`` and
``databricks_workflows`` as sibling modules so each can evolve at its
own pace and the migration story stays platform-specific.
"""
