#!/usr/bin/env python3
"""Start the PPMT v2 server for TAREA 19 testing."""
import os
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "ppmt.terminal.v2_server:app",
        host="0.0.0.0",
        port=8420,
        log_level="info",
    )
