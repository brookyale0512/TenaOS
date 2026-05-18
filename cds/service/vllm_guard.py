from __future__ import annotations

import json

from cds_service.config import Settings
from cds_service.vllm import guard_before_launch


if __name__ == "__main__":
    status = guard_before_launch(Settings.from_env())
    print(json.dumps(status.to_dict(), indent=2))
    raise SystemExit(0 if status.healthy or "started" in status.message else 1)
