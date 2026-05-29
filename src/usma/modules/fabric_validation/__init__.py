"""Fabric-side validation module.

Mid-term roadmap scaffolding (v0): connects to a target Fabric Warehouse / Lakehouse
after migration and verifies that the inventory captured by ``analyze-all`` is
present — object counts, row counts, collation, and a sample of T-SQL surface
findings actually resolved.

The Fabric SQL endpoint speaks T-SQL over the same `pyodbc` driver as Synapse,
so the SQL helpers in this module are intentionally narrow and side-effect free
(SELECT-only).
"""
