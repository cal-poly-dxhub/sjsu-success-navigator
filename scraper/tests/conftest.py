"""Put scraper/ first on sys.path so `import scraper` loads the module, not the package."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
