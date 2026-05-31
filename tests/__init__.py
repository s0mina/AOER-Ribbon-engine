# Marks ``tests`` as a package so ``python -m unittest discover -s tests -t .``
# can import the test modules while the repo root (top-level dir) stays on
# sys.path for the engine modules they import (renderer, profiles, factions...).
