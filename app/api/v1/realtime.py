"""WebSocket stream of vehicle snapshots: ws://host/ws/{provider}."""
import contextlib
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)
router = APIRouter(tags=["realtime"])


@router.websocket("/ws/{provider}")
async def stream(ws: WebSocket, provider: str) -> None:
    hub = ws.app.state.hub
    if provider not in hub.providers:
        await ws.close(code=4404, reason="unknown provider")
        return
    await ws.accept()
    await hub.subscribe(provider, ws)
    try:
        p = hub.get(provider)
        if p.last_refresh:  # send what we have right away
            await ws.send_json({"type": "vehicles", **p.snapshot().model_dump(mode="json"), "status": p.status})
        while True:
            # clients may send {"type":"ping"}; anything else is ignored
            msg = await ws.receive_json()
            if isinstance(msg, dict) and msg.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception as exc:  # malformed frame etc.
        log.debug("ws closed: %s", exc)
        with contextlib.suppress(Exception):
            await ws.close()
    finally:
        hub.unsubscribe(provider, ws)
