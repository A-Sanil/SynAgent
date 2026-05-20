"""
Build ReactionDB.exe — run this once on your laptop.
Output: usb_app/dist/ReactionDB.exe  (~30-50 MB single file)
Then copy ReactionDB.exe to D:\SynAgent\

Usage:
    cd C:\...\SynAgent
    python usb_app/build_exe.py
"""
import subprocess, sys, os

script = os.path.join(os.path.dirname(__file__), "synagent_db.py")
dist   = os.path.join(os.path.dirname(__file__), "dist")

cmd = [
    sys.executable, "-m", "PyInstaller",
    "--onefile",            # single .exe
    "--noconsole",          # no black CMD window (use tkinter window instead)
    "--name", "ReactionDB",
    "--distpath", dist,
    "--workpath", os.path.join(os.path.dirname(__file__), "build"),
    "--specpath", os.path.dirname(__file__),
    # Hidden imports FastAPI/uvicorn need
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
    "--hidden-import", "fastapi",
    "--hidden-import", "anyio",
    "--hidden-import", "anyio._backends._asyncio",
    "--hidden-import", "starlette",
    "--hidden-import", "starlette.routing",
    script,
]

print("Building ReactionDB.exe ...")
print("This takes 1-3 minutes.\n")
result = subprocess.run(cmd)

if result.returncode == 0:
    exe = os.path.join(dist, "ReactionDB.exe")
    size_mb = os.path.getsize(exe) / 1024 / 1024
    print(f"\n✅  Built: {exe}  ({size_mb:.0f} MB)")
    print(f"\nNext: Copy ReactionDB.exe to D:\\SynAgent\\")
    print(f"Then double-click it from the USB on any Windows laptop.")
else:
    print("\n❌  Build failed — check errors above.")
    sys.exit(1)
