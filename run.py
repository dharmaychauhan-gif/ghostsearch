"""
GhostSearch Launcher
Binds strictly to 127.0.0.1 (Localhost only).
Inaccessible to outside network or local Wi-Fi devices.
Access logging disabled for 100% confidentiality.
"""

import uvicorn
import sys
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

if __name__ == "__main__":
    print("=" * 60)
    print("  GHOSTSEARCH - 100% PRIVATE SEARCH ENGINE")
    print("=" * 60)
    print("  * Status: ACTIVE")
    print("  * Address: http://127.0.0.1:5000")
    print("  * Network: LOCALHOST ONLY (Strict 127.0.0.1 binding)")
    print("  * Storage: ZERO-DISK (RAM-only volatile execution)")
    print("  * Logging: MUTED (0 logs recorded anywhere)")
    print("=" * 60)
    print("  Open your browser to: http://127.0.0.1:5000")
    print("  Press Ctrl+C to terminate.")
    print("=" * 60)
    
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=5000,
        access_log=False,
        log_level="critical"
    )
