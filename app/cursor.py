"""
Cursor encoding for keyset pagination.

The cursor represents "the last row the client saw": a (created_at, id)
pair. We base64-encode it into a single opaque string so:
  - the client doesn't need to know or construct timestamp formats
  - we're free to change the internal cursor format later without
    breaking the API shape
  - it visually signals "don't try to construct this yourself" (it's not
    a page number)

This is NOT meant to be cryptographically secure -- it's just an encoding,
not a signed/tamper-proof token. That's fine here: a malicious or malformed
cursor can at worst make a query return a weird-but-harmless result page
(or a 400), it can't leak data the API wouldn't otherwise expose, and we
validate it decodes into the expected shape before using it.
"""

import base64
import json
from datetime import datetime
from typing import Optional

from fastapi import HTTPException


def encode_cursor(created_at: datetime, product_id: int) -> str:
    payload = json.dumps({"created_at": created_at.isoformat(), "id": product_id})
    return base64.urlsafe_b64encode(payload.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        return datetime.fromisoformat(payload["created_at"]), int(payload["id"])
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid pagination cursor")
