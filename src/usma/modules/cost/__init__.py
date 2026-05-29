"""Cost module.

Mid-term roadmap scaffolding (v0): pulls month-over-month consumption from
``Microsoft.Consumption`` / Cost Management for the workspace's resource group,
broken down by SKU and pool, and (when fabric_mapping has produced a CU
projection) joins the two for a side-by-side TCO delta.
"""
