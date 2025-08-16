import os
import sys

# Ensure we can import the local helpers module (skodachargefrontend/helpers.py)
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.dirname(CURRENT_DIR)
if FRONTEND_DIR not in sys.path:
    sys.path.insert(0, FRONTEND_DIR)
