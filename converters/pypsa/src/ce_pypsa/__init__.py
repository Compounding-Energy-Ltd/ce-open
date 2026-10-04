# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""PyPSA <-> CE bundle converters.

>>> from ce_pypsa import import_network, export_bundle
>>> bundle = import_network("my_pypsa_csv_folder", "my_bundle")      # doctest: +SKIP
>>> tables, report = export_bundle("my_bundle", "back_to_pypsa")      # doctest: +SKIP

Every conversion returns a report of what was approximated or dropped; see
README.md for the full mapping.
"""

__version__ = "0.1.0"

from .bundle import Bundle
from .exporter import bundle_to_tables, export_bundle, to_network
from .importer import import_network
from .pypsa_csv import PypsaTables, UnsupportedNetworkError, read_csv_folder, write_csv_folder
from .report import ConversionReport, LossyConversionWarning

__all__ = [
    "Bundle", "ConversionReport", "LossyConversionWarning", "PypsaTables", "UnsupportedNetworkError",
    "bundle_to_tables", "export_bundle", "import_network", "read_csv_folder", "to_network", "write_csv_folder",
    "__version__",
]
