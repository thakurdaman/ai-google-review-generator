import sys
import os

# Ensure root directory is in sys.path so 'app' package is found
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from app.main import app

# Export both app and handler for Vercel ASGI compatibility
handler = app
