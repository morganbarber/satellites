"""
Execution entry point for running ccsds as a Python module (`python -m ccsds`).
"""

import sys
from ccsds.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
