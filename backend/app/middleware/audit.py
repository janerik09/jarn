"""
Records API activity in the audit trail (see app/services/audit.py):
- every change (POST, PUT, PATCH, DELETE) under /api/v1, with the outcome and the request body with secrets removed
- every refused request (401, 403, 429), whatever the method, with the reason the server gave
- sign-in attempts, with the username tried (the password is never recorded)
Ordinary reads that succeed are not recorded, to keep the trail readable.
"""

import json
import uuid
from typing import Any, Optional

from app.logging_config import get_logger
from app.security import decode_token
from app.services import audit

logger = get_logger(__name__)

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
MAX_BODY_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 4 * 1024


def _header(scope, name: bytes) -> str:
    return dict(scope.get("headers", [])).get(name, b"").decode(errors="ignore")


def _actor(scope) -> Optional[dict]:
    header = _header(scope, b"authorization")
    if not header.lower().startswith("bearer "):
        return None
    payload = decode_token(header[7:].strip())
    if not payload or payload.get("type") != "access" or not payload.get("sub"):
        return None
    try:
        return {"id": uuid.UUID(payload["sub"]), "name": payload.get("username")}
    except (ValueError, TypeError):
        return None


def _client_ip(scope) -> Optional[str]:
    # Requests come through the web container, which passes the real address in these headers
    forwarded = _header(scope, b"x-real-ip") or _header(scope, b"x-forwarded-for").split(",")[0].strip()
    if forwarded:
        return forwarded
    client = scope.get("client")
    return client[0] if client else None


class AuditMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/v1/"):
            return await self.app(scope, receive, send)

        method = scope["method"]
        path = scope["path"]
        request_id = _header(scope, b"x-request-id")[:64] or uuid.uuid4().hex
        job_match = audit.UUID_RE.search(path) if "/jobs/" in path else None
        correlation = job_match.group(0) if job_match else request_id

        # Read the whole request body once, then hand the same messages to the application
        messages = []
        while True:
            message = await receive()
            messages.append(message)
            if message["type"] != "http.request" or not message.get("more_body"):
                break
        body = b"".join(m.get("body", b"") for m in messages)
        pending = list(messages)

        async def replay():
            if pending:
                return pending.pop(0)
            return await receive()

        state = {"status": None, "chunks": [], "size": 0, "headers_sent": False}

        async def capture(message):
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            elif message["type"] == "http.response.body":
                if state["status"] and (state["status"] >= 400 or _wants_response(path)) and state["size"] < MAX_RESPONSE_BYTES:
                    chunk = message.get("body", b"")
                    state["chunks"].append(chunk)
                    state["size"] += len(chunk)
            await send(message)

        try:
            await self.app(scope, replay, capture)
        finally:
            await self._record(scope, method, path, body, state, correlation)

    async def _record(self, scope, method, path, body: bytes, state, correlation: str) -> None:
        status = state["status"]
        if status is None:
            return
        refused = status in (401, 403, 429)
        if method not in MUTATING and not refused:
            return
        try:
            request_details: Any = {}
            content_type = _header(scope, b"content-type")
            if "application/json" in content_type and body and len(body) <= MAX_BODY_BYTES:
                try:
                    request_details = json.loads(body)
                except ValueError:
                    request_details = {"unparsed_body": True}
            elif body:
                request_details = {"body_bytes": len(body)}
            if isinstance(request_details, dict) and "password" in request_details and "username" in request_details:
                pass  # The redaction in the audit service removes the password

            response: Any = None
            raw = b"".join(state["chunks"])
            if raw and "application/json" in _response_type(state):
                try:
                    response = json.loads(raw)
                except ValueError:
                    response = None

            outcome = "success" if status < 400 else ("denied" if refused else "failure")
            reason = None
            if status >= 400 and isinstance(response, dict):
                detail = response.get("detail")
                reason = detail if isinstance(detail, str) else json.dumps(detail, default=str)[:500]

            actor = _actor(scope)
            actor_name = None
            actor_id = actor["id"] if actor else None
            if actor:
                actor_name = actor["name"]
            if path.startswith("/api/v1/auth/login") and isinstance(request_details, dict):
                actor_name = str(request_details.get("username", ""))[:255] or None
            if isinstance(response, dict) and isinstance(response.get("user"), dict) and response["user"].get("id"):
                actor_id = uuid.UUID(response["user"]["id"])
                actor_name = response["user"].get("username", actor_name)

            await audit.record(
                f"{method} {audit.normalise_path(path)}"[:100],
                category=audit.category_for(path),
                outcome=outcome,
                source="api",
                actor_id=actor_id,
                actor_name=actor_name,
                resource_type=audit.category_for(path),
                resource_id=(audit.UUID_RE.search(path).group(0) if audit.UUID_RE.search(path) else None),
                reason=reason,
                details={"request": request_details, "status": status, "path": path},
                correlation_id=correlation,
                ip_address=_client_ip(scope),
                user_agent=_header(scope, b"user-agent") or None,
            )
        except Exception as e:
            logger.warning("audit_middleware_failed", error=str(e))


def _wants_response(path: str) -> bool:
    return path.startswith("/api/v1/auth/")


def _response_type(state) -> str:
    return state.get("content_type", "application/json")
