"""WebSocket client for ComfyUI progress tracking."""

from __future__ import annotations

import asyncio
import json
import logging

import aiohttp

from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 10


class ComfyUIWebSocketClient:

    def __init__(self, session: aiohttp.ClientSession, base_url: str, timeout: int) -> None:
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._ws: aiohttp.ClientWebSocketResponse | None = None

    def _ws_url(self, client_id: str) -> str:
        # Convert base to WS URL
        url = self._base_url
        if url.startswith("https://"):
            url = "wss://" + url[len("https://"):]
        elif url.startswith("http://"):
            url = "ws://" + url[len("http://"):]
        return f"{url}/ws?clientId={client_id}"

    # Connect before posting the prompt so completion can't be missed
    async def connect(self, client_id: str) -> None:
        ws_url = self._ws_url(client_id)
        _LOGGER.debug("Connecting to ComfyUI WebSocket: %s", ws_url)
        async with asyncio.timeout(CONNECT_TIMEOUT):
            self._ws = await self._session.ws_connect(ws_url)

    # Only closes the socket, the session is shared with the rest of Home Assistant
    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    # Returns False if socket dropped, so caller can poll
    async def wait_for_completion(self, prompt_id: str) -> bool:
        if self._ws is None:
            raise RuntimeError("WebSocket is not connected")

        async with asyncio.timeout(self._timeout):
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.BINARY:
                    # Preview image frames, skip
                    continue

                if msg.type in (
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.CLOSING,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break

                if msg.type != aiohttp.WSMsgType.TEXT:
                    continue

                try:
                    data = json.loads(msg.data)
                except (json.JSONDecodeError, TypeError):
                    continue

                msg_type = data.get("type")

                if msg_type == "progress":
                    progress = data.get("data", {})
                    _LOGGER.debug(
                        "ComfyUI progress: %s/%s",
                        progress.get("value"),
                        progress.get("max"),
                    )

                elif msg_type == "execution_error":
                    err_data = data.get("data", {})
                    raise HomeAssistantError(
                        translation_domain=DOMAIN,
                        translation_key="execution_failed",
                        translation_placeholders={
                            "error": f"{err_data.get('exception_type', 'Unknown')}: "
                            f"{err_data.get('exception_message', str(err_data))}"
                        },
                    )

                elif msg_type == "executing":
                    exec_data = data.get("data", {})
                    # node == null means execution finished
                    if (
                        exec_data.get("node") is None
                        and exec_data.get("prompt_id") == prompt_id
                    ):
                        _LOGGER.debug(
                            "ComfyUI prompt %s completed via WebSocket",
                            prompt_id,
                        )
                        return True

        _LOGGER.warning(
            "ComfyUI WebSocket closed before prompt %s finished, falling back to polling",
            prompt_id,
        )
        return False
