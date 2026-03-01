"""WebSocket client for ComfyUI progress tracking."""

from __future__ import annotations

import json
import logging

import aiohttp
import async_timeout

from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger(__name__)


class ComfyUIWebSocketClient:

    def __init__(self, base_url: str, timeout: int) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def _ws_url(self, client_id: str) -> str:
        # Convert base to WS URL
        url = self._base_url
        if url.startswith("https://"):
            url = "wss://" + url[len("https://"):]
        elif url.startswith("http://"):
            url = "ws://" + url[len("http://"):]
        return f"{url}/ws?clientId={client_id}"

    # Connect via WS and wait until the prompt finishes executing.
    async def wait_for_completion(
        self, prompt_id: str, client_id: str
    ) -> None:
        ws_url = self._ws_url(client_id)
        _LOGGER.debug("Connecting to ComfyUI WebSocket: %s", ws_url)

        async with aiohttp.ClientSession() as session:
            async with async_timeout.timeout(self._timeout):
                async with session.ws_connect(ws_url) as ws:
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            # Preview image frames, skip
                            continue

                        if msg.type in (
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.CLOSING,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            raise HomeAssistantError(
                                f"ComfyUI WebSocket closed unexpectedly: {msg}"
                            )

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
                                f"ComfyUI execution error: "
                                f"{err_data.get('exception_type', 'Unknown')}: "
                                f"{err_data.get('exception_message', str(err_data))}"
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
                                return
