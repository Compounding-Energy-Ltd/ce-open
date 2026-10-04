# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""ce-genx: import a GenX case folder as a CE data-format bundle.

Typical use::

    from ce_genx import import_case

    bundle = import_case("path/to/genx_case", "path/to/bundle")
    print(bundle.report.format())

or, from the command line::

    ce-genx-import path/to/genx_case path/to/bundle

See the README for the mapping and for every lossy-mapping note code.
"""

from __future__ import annotations

__version__ = "0.1.0"

from .bundle import check_bundle, read_tables, write_bundle  # noqa: E402
from .convert import CEBundle, ImportOptions, convert  # noqa: E402
from .genx import GenXCase, read_case  # noqa: E402
from .report import ImportReport  # noqa: E402

__all__ = [
    "__version__",
    "import_case",
    "read_case",
    "convert",
    "write_bundle",
    "check_bundle",
    "read_tables",
    "GenXCase",
    "CEBundle",
    "ImportOptions",
    "ImportReport",
]


def import_case(
    case_dir,
    out_dir=None,
    *,
    stage: int | None = None,
    tdr: bool = False,
    fmt: str = "parquet",
    overwrite: bool = False,
    options: ImportOptions | None = None,
    **option_fields,
) -> CEBundle:
    """Read a GenX case, convert it and (if ``out_dir`` is given) write the bundle.

    Keyword arguments other than ``stage``, ``tdr``, ``fmt``, ``overwrite``
    and ``options`` are :class:`ImportOptions` fields, e.g.
    ``investment_year=2035`` or ``network="dc"``.
    """
    if options is not None and option_fields:
        raise TypeError("pass either options= or individual option fields, not both")
    opts = options or ImportOptions(**option_fields)
    case = read_case(case_dir, stage=stage, tdr=tdr)
    bundle = convert(case, opts)
    if out_dir is not None:
        write_bundle(bundle, out_dir, fmt=fmt, overwrite=overwrite)
    return bundle
