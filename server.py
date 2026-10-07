"""Backward-compatible entry point for running from a checkout: `python server.py`.

The code now lives in the icloud_mail_mcp package; installing it gives the
`icloud-mail-mcp` command. See the README.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from icloud_mail_mcp.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
