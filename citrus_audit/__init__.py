"""Independent Citrus data audit support library."""
__version__ = '3.1.0'
from .envelope import TableEnvelope
from .api import AuditSnapshot, audit_table, review_bytes, export_workbook
from .review import review_sheet
from .readers import read_table, read_tables, read_region, read_regions, normalize_regions
from .methods import audit_frame, audit_selected_checks, audit_duplicates, audit_group_outliers
from .data_audit_rules import RULES, describe_rule, rule_manifest
from .integrity import IntegrityTable, IntegrityConfig, table_from_rows, table_from_frame, audit_numeric_reuse
from .summary_consistency import check_integer_summary, compare_reported_summaries
from .compare import compare_snapshots
from .inspection import discover_regions
from .plan import compare_bound, parse_plan
from .reporting import compact_report
