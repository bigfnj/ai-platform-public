"""Dev shim — prefer the installed `edu-dashboard` command (see [project.scripts]). This
adds src/ to the path and delegates so `python serve.py` (and Open Dashboard.bat) still work."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from dashboard.serve import main  # noqa: E402

if __name__ == "__main__":
    main()
