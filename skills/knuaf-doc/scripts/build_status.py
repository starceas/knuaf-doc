#!/usr/bin/env python3
"""Read the project revision, not legacy status prose."""

import sys

from gg_commands import main

if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        getattr(_stream, "reconfigure", lambda **_: None)(encoding="utf-8")
    raise SystemExit(main("status"))
