"""PyInstaller entry point.

A plain module rather than ``autoclicker/__main__.py`` so that the package is
imported normally and its relative imports keep working.
"""

from autoclicker.cli import main

raise SystemExit(main())
