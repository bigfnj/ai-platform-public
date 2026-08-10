"""Dev shim — prefer the installed `cvc` command (see [project.scripts]). This adds src/
to the path and delegates so `python cli.py` still works without installing."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from cvc_words.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
