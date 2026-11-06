"""Test setup: fast streaming, and a minimal fastapi stand-in when fastapi isn't installed."""
import os
import sys
import types

os.environ.setdefault("STREAM_SPEED", "2000")       # stream shots ~instantly
os.environ.setdefault("AI_THINK_SECONDS", "0.01")
os.environ.setdefault("TURN_SECONDS", "30")
os.environ.setdefault("DISCONNECT_GRACE_SECONDS", "30")
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

try:
    import fastapi  # noqa: F401
except ImportError:  # the manager only needs these two names from fastapi
    stub = types.ModuleType("fastapi")

    class WebSocket:  # noqa: D401 - marker type
        pass

    class WebSocketDisconnect(Exception):
        pass

    stub.WebSocket, stub.WebSocketDisconnect = WebSocket, WebSocketDisconnect
    sys.modules["fastapi"] = stub
