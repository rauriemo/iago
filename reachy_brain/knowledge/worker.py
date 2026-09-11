"""Parser entrypoint: install resource limits before loading extraction libraries."""

import json
import sys
from pathlib import Path

from .limits import limit_parser_memory


def main():
    try:
        limit_parser_memory()
    except Exception:
        print(json.dumps({"status": "resource_limits_unavailable", "passages": []}))
        return
    try:
        from .parser import extract

        result = extract(Path(sys.argv[1]))
    except MemoryError:
        result = {"status": "over_limit", "passages": []}
    except Exception:
        result = {"status": "unreadable", "passages": []}
    try:
        sys.stdout.write(json.dumps(result))
    except MemoryError:
        sys.stdout.write('{"status":"over_limit","passages":[]}')


if __name__ == "__main__":
    main()
