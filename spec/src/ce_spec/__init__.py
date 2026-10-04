# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""ce_spec — the CE data-format specification in code: schemas, provenance grammar and validators.

Quick use::

    from ce_spec import validate_bundle, validate_results
    report = validate_bundle("path/to/bundle")
    print(report.summary())
    report.raise_if_errors()

See SPEC.md for the normative text and TABLES.md for the column reference.
"""

from .bundle import LoadedBundle, load_bundle, validate_bundle
from .issues import CODES, ERROR, WARNING, Issue, Report, ValidationError
from .prov import Prov, format_prov, parse_prov_text, read_prov
from .results import validate_results
from .schema import SPEC_VERSION, arrow_schema, doc_schema, doc_schemas, table_schema, table_schemas
from .tables import read_raw, write_table

__version__ = SPEC_VERSION

__all__ = [
    "CODES", "ERROR", "WARNING", "Issue", "LoadedBundle", "Prov", "Report", "SPEC_VERSION", "ValidationError",
    "arrow_schema", "doc_schema", "doc_schemas", "format_prov", "load_bundle", "parse_prov_text", "read_prov",
    "read_raw", "table_schema", "table_schemas", "validate_bundle", "validate_results", "write_table",
]
