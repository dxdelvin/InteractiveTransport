"""Entry point.

    python main.py            # dev server with auto-reload (see .env)
    uvicorn main:app          # or run the ASGI app directly
"""
from app.core.config import get_settings
from app.factory import create_app

app = create_app()

if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("main:app", host=s.host, port=s.port, reload=s.reload, log_level=s.log_level)
