"""Shared utilities used across modules."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path


def atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON data to *path* atomically via temp file + fsync + rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        fd = os.open(str(tmp), os.O_RDONLY)
        os.fsync(fd)
        os.close(fd)
        os.replace(str(tmp), str(path))
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


def generate_order_id(restaurant_id: str) -> str:
    """Generate a unique order ID: {restaurant}_{timestamp}_{uuid6}."""
    ts = datetime.now(timezone.utc)
    return (
        f"{restaurant_id}_{ts.strftime('%Y%m%d%H%M%S')}_"
        f"{str(uuid.uuid4())[:6]}"
    )
