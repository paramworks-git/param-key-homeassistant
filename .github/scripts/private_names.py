"""pre-commit hook: refuse private names in staged files.

The patterns are private, so they aren't in this repository: one case-insensitive regular
expression per line in ~/.config/paramworks/private-patterns.txt (or $PK_PRIVATE_PATTERNS).
Without that file the hook passes and says so.
"""

import os
from pathlib import Path
import re
import sys

PATTERNS = Path(os.environ.get("PK_PRIVATE_PATTERNS", Path.home() / ".config/paramworks/private-patterns.txt"))


def main() -> int:
    if not PATTERNS.is_file():
        print(f"private-names: no {PATTERNS}, skipped")
        return 0
    lines = [l.strip() for l in PATTERNS.read_text().splitlines()]
    patterns = [re.compile(l.encode(), re.I) for l in lines if l and not l.startswith("#")]
    bad = 0
    for name in sys.argv[1:]:
        path = Path(name)
        if not path.is_file():
            continue
        data = path.read_bytes()
        for p in patterns:
            for m in p.finditer(data):
                line = data.count(b"\n", 0, m.start()) + 1
                print(f"{name}:{line}: private name ({p.pattern.decode()})")
                bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
