#!/usr/bin/env python3
"""Ancien chemin supporté : utiliser scripts/verify/stack.py ou `autokom check`."""
from pathlib import Path
_source = Path(__file__).with_name('stack.py')
exec(compile(_source.read_bytes(), str(_source), 'exec'), globals())
