# Copyright 2026 Compounding Energy Ltd
# SPDX-License-Identifier: Apache-2.0
"""``python -m ce_studio`` — same as the ``ce-studio`` command."""

import sys

from .cli import main

sys.exit(main())
