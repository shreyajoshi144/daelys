"""Request bodies with a hard size cap.

`await request.body()` buffers whatever the client sends, and a chunked upload has no Content-Length to
check first. This reads the stream and stops as soon as the cap is passed, so an oversized upload never
sits in memory.
"""

from fastapi import HTTPException, Request, status


async def read_limited(request: Request, max_bytes: int, detail: str = "payload too large") -> bytes:
    too_large = HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=detail)
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise too_large
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > max_bytes:
            raise too_large
    return bytes(body)
