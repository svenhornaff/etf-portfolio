"""Enables `uv run python -m etf_portfolio` as an alternative to the
`etf-portfolio` console script installed via [project.scripts].
"""

import sys

from .report import main

if __name__ == "__main__":
    sys.exit(main())
