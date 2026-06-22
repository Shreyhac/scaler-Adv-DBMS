"""Put the src/ directory on sys.path so benchmarks can `import minidb`."""
import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "src")))
