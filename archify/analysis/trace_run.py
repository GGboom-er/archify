"""Separate isolated entry point for explicitly requested target execution."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis.trace import main

if __name__ == '__main__':
    sys.exit(main())
