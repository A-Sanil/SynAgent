"""
Build ReactionDB — works on Windows (.exe) and macOS (.app).
Run this script ON EACH PLATFORM to produce the binary for that platform.

Windows:  python usb_app\\build_exe.py   → usb_app/dist/ReactionDB.exe  (~274 MB)
macOS:    python usb_app/build_exe.py    → usb_app/dist/ReactionDB       (~280 MB)

Then copy the output to the USB:
  Windows: copy dist\\ReactionDB.exe  D:\\SynAgent\\ReactionDB.exe
  macOS:   cp dist/ReactionDB         /Volumes/SynAgent/ReactionDB
"""
import subprocess, sys, os, platform

HERE   = os.path.dirname(os.path.abspath(__file__))
script = os.path.join(HERE, "synagent_db.py")
dist   = os.path.join(HERE, "dist")
build  = os.path.join(HERE, "build")
IS_MAC = platform.system() == "Darwin"

name = "ReactionDB"

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--onefile",
    "--noconsole",
    "--name", name,
    "--distpath", dist,
    "--workpath", build,
    "--specpath", HERE,
    # Make sure _tailwind_data is bundled
    "--hidden-import", "_tailwind_data",
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
    "--collect-all", "PySide6",
    script,
]

# macOS: produce a proper .app bundle
if IS_MAC:
    cmd = [c for c in cmd if c != "--onefile"]
    cmd += ["--windowed"]   # creates .app bundle on macOS

platform_name = "macOS" if IS_MAC else "Windows"
print(f"Building ReactionDB for {platform_name} (~3-5 minutes)...")
result = subprocess.run(cmd)

if result.returncode == 0:
    if IS_MAC:
        out = os.path.join(dist, name)
        print(f"\nBuilt: {out}")
        print(f"\nCopy to USB:  cp -r \"{out}\" /Volumes/SynAgent/ReactionDB")
    else:
        out = os.path.join(dist, name + ".exe")
        size_mb = os.path.getsize(out) / 1024 / 1024
        print(f"\nBuilt: {out}  ({size_mb:.0f} MB)")
        print(f"\nCopy to USB:  copy \"{out}\" D:\\SynAgent\\ReactionDB.exe")
else:
    print("\nBuild FAILED — check errors above.")
    sys.exit(1)
