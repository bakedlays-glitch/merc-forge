import subprocess, sys, os
SIDECAR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(SIDECAR, ".venv", "Scripts", "python.exe")
LOG = sys.argv[1]
f = open(LOG, "ab")
p = subprocess.Popen([PY, "main.py", "--port", "8000", "--host", "127.0.0.1"],
                     cwd=SIDECAR, creationflags=0x08000000, stdin=subprocess.DEVNULL,
                     stdout=f, stderr=subprocess.STDOUT, close_fds=True)
print(p.pid)
