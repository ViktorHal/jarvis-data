"""Test paths. The engine lives in jarvis/, the robot's own modules in fetch/, the research scripts in research/;
the test files import them by bare name (`import decision`), so put all three on the path once, here, and the whole
suite runs from anywhere with `python -m pytest tests -q`."""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
for d in ("jarvis", "fetch", "research", "."):
    p = os.path.join(ROOT, d)
    if p not in sys.path:
        sys.path.insert(0, p)
