import uvicorn
import os
import sys

# Ensure UTF-8 output on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Ensure project root is in python path
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

if __name__ == "__main__":
    print("=" * 60)
    print("🚀 Google Review SaaS Platform Starting...")
    print("📱 Customer Landing Example: http://localhost:8000/r/artisan-roast")
    print("🏢 Reseller Admin Dashboard: http://localhost:8000/admin")
    print("🔑 Default Admin Login: admin / admin123")
    print("=" * 60)
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
