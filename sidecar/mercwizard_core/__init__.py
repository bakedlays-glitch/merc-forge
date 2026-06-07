"""Mercwizard core library.

Engine-correct reads and writes against a Jagged Alliance 2 v1.13 install.

The public API is intentionally narrow: callers compose a `Merc` model,
optionally a `Gear` and `AimBinding`, run `audit.validate()`, then call the
appropriate `inject` module to write.

This package has no FastAPI dependency. It can be used as a CLI library or
imported by the sidecar's HTTP routes layer.
"""

__version__ = "2.0.0"
