"""
Added at repo root so `python -m tests.xxx` and `pytest` both find
the `src` package.  The canonical way to run tests is:

    python -m tests.test_protocol
    python -m tests.test_stress
    python -m tests.test_malformed
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
