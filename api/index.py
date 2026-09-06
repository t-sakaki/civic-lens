import sys
from pathlib import Path

# Add project root to sys.path so app and its dependencies can be imported
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from app import app
