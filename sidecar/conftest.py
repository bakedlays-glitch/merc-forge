"""Root pytest configuration for the sidecar.

The HTTP API refuses to serve when MERCWIZARD_TOKEN is unset, so that a copy of
the sidecar executable started outside the Tauri shell cannot come up as an
unauthenticated API with write access to a user's game install. The test suite
drives the app in-process through TestClient with no shell and no token, so it
opts out of that gate explicitly here rather than having the production default
weakened for its benefit.

This lives at the sidecar root (not under tests/) so it is loaded for the test
modules that sit beside it as well as those under tests/. It must run before
main.py is imported, because main reads the flag once at import time.
"""
from __future__ import annotations

import os

os.environ.setdefault("MERCWIZARD_ALLOW_NO_AUTH", "1")
