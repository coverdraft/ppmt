"""__main__.py — Allow running V12 paper trader as `python -m scripts.v12.paper_trader`."""
from .runner import main
import sys
sys.exit(main())
