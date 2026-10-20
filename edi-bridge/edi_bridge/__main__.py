"""Allows `python -m edi_bridge ...`."""

import sys

from .cli import main

sys.exit(main())
