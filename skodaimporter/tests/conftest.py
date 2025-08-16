"""
Test configuration for skodaimporter tests.
Ensures the repository root is on sys.path so `import skodaimporter.*` works
when running `pytest` from the `skodaimporter/` folder.
"""

import sys
from pathlib import Path

# Add repo root (parent of the 'skodaimporter' package) to sys.path
REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
