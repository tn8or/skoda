"""
Basic test to diagnose import issues.
"""

import os
import sys


def test_import_debug():
    """Debug test to understand import issues."""
    # Print current working directory
    print(f"Current working directory: {os.getcwd()}")

    # Print Python path
    print(f"Python path: {sys.path}")

    # Check if chargefinder.py exists
    parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    chargefinder_path = os.path.join(parent_dir, "chargefinder.py")
    print(f"Looking for chargefinder.py at: {chargefinder_path}")
    print(f"File exists: {os.path.exists(chargefinder_path)}")

    # Try to import
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)

    try:
        import chargefinder

        print("Successfully imported chargefinder")
        print(f"chargefinder module file: {chargefinder.__file__}")

        # List available attributes
        attrs = [attr for attr in dir(chargefinder) if not attr.startswith("_")]
        print(f"Available attributes: {attrs}")

    except ImportError as e:
        print(f"Import error: {e}")
        # List files in parent directory
        if os.path.exists(parent_dir):
            files = os.listdir(parent_dir)
            print(f"Files in parent directory: {files}")

    # This test should always pass
    assert True


def test_simple_assertion():
    """Simple test that should always pass."""
    assert 1 + 1 == 2
