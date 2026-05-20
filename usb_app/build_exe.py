"""
Build ReactionDB.exe — run once to produce the standalone app.
Output: usb_app/dist/ReactionDB.exe
Then copy to D:\SynAgent\ReactionDB.exe

Usage:
    cd C:\\...\\SynAgent
    python usb_app\\build_exe.py
"""
import subprocess, sys, os

HERE   = os.path.dirname(os.path.abspath(__file__))
script = os.path.join(HERE, "synagent_db.py")
dist   = os.path.join(HERE, "dist")
build  = os.path.join(HERE, "build")

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--onefile",
    "--noconsole",
    "--name", "ReactionDB",
    "--distpath", dist,
    "--workpath", build,
    "--specpath", HERE,
    # FastAPI / uvicorn
    "--hidden-import", "uvicorn.logging",
    "--hidden-import", "uvicorn.loops",
    "--hidden-import", "uvicorn.loops.auto",
    "--hidden-import", "uvicorn.protocols",
    "--hidden-import", "uvicorn.protocols.http",
    "--hidden-import", "uvicorn.protocols.http.auto",
    "--hidden-import", "uvicorn.protocols.websockets",
    "--hidden-import", "uvicorn.protocols.websockets.auto",
    "--hidden-import", "uvicorn.lifespan",
    "--hidden-import", "uvicorn.lifespan.on",
    "--hidden-import", "anyio._backends._asyncio",
    "--hidden-import", "starlette.routing",
    # PySide6 WebEngine
    "--hidden-import", "PySide6.QtWebEngineWidgets",
    "--hidden-import", "PySide6.QtWebEngineCore",
    "--hidden-import", "PySide6.QtWebChannel",
    "--hidden-import", "PySide6.QtNetwork",
    "--hidden-import", "PySide6.QtCore",
    "--hidden-import", "PySide6.QtGui",
    "--hidden-import", "PySide6.QtWidgets",
    # Collect all PySide6 data files (needed for WebEngine)
    "--collect-all", "PySide6",
    script,
]

print("Building ReactionDB.exe (PySide6 embedded browser - ~200 MB) ...")
print("This takes 3-5 minutes.\n")
result = subprocess.run(cmd)

if result.returncode == 0:
    exe = os.path.join(dist, "ReactionDB.exe")
    size_mb = os.path.getsize(exe) / 1024 / 1024
    print(f"\nBUILT: {exe}  ({size_mb:.0f} MB)")
    print(f"\nCopy to USB:  copy \"{exe}\" D:\\SynAgent\\ReactionDB.exe")
else:
    print("\nBuild FAILED — check errors above.")
    sys.exit(1)
