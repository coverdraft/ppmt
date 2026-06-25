"""
patch_paths.py — Parchear todos los scripts v6/v7 para usar rutas portátiles.

Reemplaza "/home/z/my-project" (ruta hardcoded del entorno Linux original)
por _PROJECT_ROOT_STR calculado dinámicamente desde __file__.

Antes:  DB_PATH = os.environ.get("PPMT_DB_PATH", "/home/z/my-project/data/ppmt.db")
Después: DB_PATH = os.environ.get("PPMT_DB_PATH", _PROJECT_ROOT_STR + "/data/ppmt.db")

Esto permite que el repo funcione en cualquier máquina sin symlink ni env vars.
"""
from pathlib import Path
import re

REPO = Path("/home/z/my-project/ppmt_v7")

HEADER = """# === Auto-detected project root (portable paths, patched) ===
import os as _os
from pathlib import Path as _Path
_PROJECT_ROOT = _Path(__file__).resolve().parents[2]
_PROJECT_ROOT_STR = str(_PROJECT_ROOT)
# === End path setup ===

"""

def patch_file(py: Path) -> bool:
    """Patch a single .py file. Returns True if changed."""
    text = py.read_text()
    if "/home/z/my-project" not in text:
        return False
    if "_PROJECT_ROOT_STR" in text:
        return False  # already patched

    # Replace all "/home/z/my-project → _PROJECT_ROOT_STR + "
    new_text = text.replace('"/home/z/my-project', '_PROJECT_ROOT_STR + "')

    # Find insertion point: after the module docstring AND after
    # `from __future__ import annotations` (which must be first statement).
    # Strategy: scan tokens to find end of docstring + future imports + initial imports.
    # Simpler: find the first line that is NOT one of:
    #   - blank
    #   - comment
    #   - docstring (""" or ''')
    #   - `from __future__ import ...`
    # Insert the header just before that first "real" line.

    lines = new_text.split('\n')
    i = 0
    n = len(lines)
    in_docstring = False
    docstring_marker = None

    # Skip leading docstring
    if i < n and lines[i].lstrip().startswith(('"""', "'''")):
        marker = lines[i].lstrip()[:3]
        # Check if it closes on the same line
        rest = lines[i].lstrip()[3:]
        if marker in rest:
            i += 1
        else:
            in_docstring = True
            docstring_marker = marker
            i += 1
            while i < n:
                if docstring_marker in lines[i]:
                    i += 1
                    in_docstring = False
                    break
                i += 1

    # Skip blank lines, comments, __future__ imports, and __future__ import block
    while i < n:
        line = lines[i].strip()
        if not line:
            i += 1
        elif line.startswith('#'):
            i += 1
        elif line.startswith('from __future__ import'):
            i += 1
        else:
            break

    # Insert header here
    header_lines = HEADER.split('\n')
    new_lines = lines[:i] + [''] + header_lines + [''] + lines[i:]
    new_text = '\n'.join(new_lines)

    py.write_text(new_text)
    return True

def main():
    files = list((REPO / "scripts/v6").glob("*.py")) + list((REPO / "scripts/v7").glob("*.py"))
    patched = 0
    skipped = 0
    for py in sorted(files):
        if patch_file(py):
            print(f"PATCHED: {py.relative_to(REPO)}")
            patched += 1
        else:
            print(f"skipped: {py.relative_to(REPO)}")
            skipped += 1
    print(f"\nDone. Patched {patched} files, skipped {skipped} (already patched or no hardcoded paths).")

if __name__ == "__main__":
    main()
