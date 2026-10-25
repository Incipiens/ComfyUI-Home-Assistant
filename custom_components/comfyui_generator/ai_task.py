"""AI Task integration for ComfyUI Image Generation."""

from __future__ import annotations

import logging
from typing import Any, Dict
import aiohttp
import mimetypes
import json
import asyncio
import random
import uuid

from homeassistant.components import ai_task
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

_LOGGER = logging.getLogger(__name__)

from .const import (
    CONF_BASE_URL, CONF_TIMEOUT,
    DEFAULT_TIMEOUT,
    CONF_WORKFLOW_PATH, CONF_SEED_NODE_ID,
    CONF_WORKFLOW_RESOLUTION_NODE_ID, CONF_WORKFLOW_PROMPT_NODE_ID,
    CONF_IMAGE_W, CONF_IMAGE_H, CONF_WORKFLOW_TITLE,
)
from .websocket_client import ComfyUIWebSocketClient

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    # Set up the ComfyUI AI Task entity
    async_add_entities([ComfyUITaskEntity(config_entry)])

# ComfyUI AI Task Entity
class ComfyUITaskEntity(ai_task.AITaskEntity):
    """AI Task entity that uses ComfyUI for image generation."""
    def __init__(self, entry: ConfigEntry) -> None:
        super().__init__()
        self._attr_has_entity_name = True
        self.entry = entry
        # Options override the original setup data
        data = {**entry.data, **entry.options}
        self._attr_name = data.get(CONF_WORKFLOW_TITLE, "")
        self._base_url = data.get(CONF_BASE_URL, "")
        self._timeout = data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
        self._wf_path = data.get(CONF_WORKFLOW_PATH, "")
        # None means leave the workflow's own values
        self._wf_resolution_node_id = data.get(CONF_WORKFLOW_RESOLUTION_NODE_ID) or None
        self._wf_resolution_w = data.get(CONF_IMAGE_W, 800)
        self._wf_resolution_h = data.get(CONF_IMAGE_H, 480)
        self._wf_seed_node_id = data.get(CONF_SEED_NODE_ID) or None
        self._wf_prompt_node_id = data.get(CONF_WORKFLOW_PROMPT_NODE_ID, "")

        self._attr_supported_features = ai_task.AITaskEntityFeature.GENERATE_IMAGE

    async def _prepare_workflow(self, prompt_text: str) -> Dict[str, Any]:
        wf = json.loads(await self._load_workflow_json())
        if not isinstance(wf, dict):
            raise ValueError("Workflow JSON must be an object")

        # Check for graph/UI format instead of API format
        if "nodes" in wf and isinstance(wf.get("nodes"), list):
            raise ValueError(
                "Workflow file is in UI/graph format. "
                "Please re-export from ComfyUI using 'Save (API Format)'."
            )

        # Checking for prompt object before proceeding, quick sanity check of file
        nodes = wf.get("prompt", wf)
        if not isinstance(nodes, dict):
            raise ValueError("Workflow JSON must contain a node dict or a {'prompt': {...}} object")

        # Only keep valid ComfyUI nodes
        nodes = {k: v for k, v in nodes.items() if isinstance(v, dict) and "class_type" in v}

        self._inject_inputs(nodes, prompt_text)
        return nodes
    
    # Raise rather than silently using the workflow's saved values
    @staticmethod
    def _node_inputs(nodes: Dict[str, Any], node_id: int | str, label: str, required: tuple[str, ...]) -> Dict[str, Any]:
        node = nodes.get(str(node_id))
        if not isinstance(node, dict):
            raise ValueError(
                f"Configured {label} node '{node_id}' not found in workflow. "
                f"Available node IDs: {list(nodes.keys())}"
            )
        inputs = node.get("inputs")
        missing = [k for k in required if not isinstance(inputs, dict) or k not in inputs]
        if missing:
            raise ValueError(
                f"Configured {label} node '{node_id}' ({node.get('class_type')}) has no "
                f"{', '.join(missing)} input. Reconfigure the integration to pick a different node."
            )
        return inputs

    # Set prompt, resolution and seed on the configured nodes
    def _inject_inputs(self, nodes: Dict[str, Any], prompt_text: str) -> None:
        inputs = self._node_inputs(nodes, self._wf_prompt_node_id, "prompt", ("text",))
        inputs["text"] = prompt_text

        if self._wf_resolution_node_id:
            inputs = self._node_inputs(nodes, self._wf_resolution_node_id, "resolution", ("width", "height"))
            inputs["width"] = self._wf_resolution_w
            inputs["height"] = self._wf_resolution_h

        # RandomNoise and KSamplerAdvanced use noise_seed
        if self._wf_seed_node_id:
            node = nodes.get(str(self._wf_seed_node_id))
            node_inputs = node.get("inputs") if isinstance(node, dict) else None
            seed_key = "noise_seed" if isinstance(node_inputs, dict) and "noise_seed" in node_inputs else "seed"
            inputs = self._node_inputs(nodes, self._wf_seed_node_id, "seed", (seed_key,))
            inputs[seed_key] = random.randint(0, 2**64 - 1)

    # Generator
    async def _async_generate_image(
        self,
        task: ai_task.GenImageTask,
        chat_log,  # kept for compatibility; unused
    ) -> ai_task.GenImageTaskResult:
        prompt_text: str = task.instructions or ""

        try:
            workflow_obj = await self._prepare_workflow(prompt_text)

            # Connect before posting so a fast generation can't finish unseen
            client_id = str(uuid.uuid4())
            ws_client: ComfyUIWebSocketClient | None = ComfyUIWebSocketClient(self._base_url, self._timeout)
            try:
                await ws_client.connect(client_id)
            except (aiohttp.ClientError, TimeoutError) as ws_err:
                _LOGGER.warning(
                    "WebSocket unavailable, falling back to polling: %s", ws_err
                )
                ws_client = None

            try:
                prompt_id = await self._post_prompt(workflow_obj, client_id=client_id)
                if ws_client is not None:
                    await ws_client.wait_for_completion(prompt_id)
            finally:
                if ws_client is not None:
                    await ws_client.close()

            # Returns immediately if the WebSocket already saw completion
            image_bytes, mime_type = await self._fetch_first_image_bytes(prompt_id)

        except Exception as err:
            raise HomeAssistantError(f"Error generating image: {err}") from err

        has_resolution = self._wf_resolution_node_id is not None
        return ai_task.GenImageTaskResult(
            image_data=image_bytes,
            mime_type=mime_type,
            model="ComfyUI",
            conversation_id=None,
            revised_prompt=prompt_text,
            width=self._wf_resolution_w if has_resolution else None,
            height=self._wf_resolution_h if has_resolution else None,
        )
    
    async def _load_workflow_json(self) -> str:
        p = self._wf_path.strip()
        if not p or not p.startswith("/config/"):
            raise ValueError("Workflow file must be an absolute path under /config")

        def _read_file() -> str:
            with open(p, "r", encoding="utf-8") as f:
                return f.read()

        return await self.hass.async_add_executor_job(_read_file)
    
    # POST /prompt with node graph
    async def _post_prompt(
        self, nodes: dict, client_id: str | None = None
    ) -> str:
        """Wrap node graph exactly once and POST to ComfyUI."""
        url = f"{self._base_url}/prompt"
        payload: dict = {"prompt": nodes}
        if client_id:
            payload["client_id"] = client_id
        async with aiohttp.ClientSession() as sess:
            async with asyncio.timeout(self._timeout):
                async with sess.post(url, json=payload) as resp:
                    resp.raise_for_status()
                    js = await resp.json()
                    prompt_id = js.get("prompt_id") or js.get("promptId")
                    if not prompt_id:
                        raise RuntimeError(f"ComfyUI did not return prompt_id: {js}")
                    return prompt_id
                
    # Poll /history/{prompt_id} and GET /view for image bytes, times out after self._timeout seconds
    async def _fetch_first_image_bytes(self, prompt_id: str) -> tuple[bytes, str]:
        """Poll /history/{prompt_id} then GET bytes from /view for the top image."""
        poll_interval = 0.5
        max_interval = 5.0

        try:
            # Covers request time as well as sleeps
            async with asyncio.timeout(self._timeout):
                async with aiohttp.ClientSession() as session:
                    while True:
                        hist_url = f"{self._base_url}/history/{prompt_id}"
                        async with session.get(hist_url) as r:
                            r.raise_for_status()
                            hist = await r.json()

                        entry = hist.get(prompt_id, {})

                        # Check for execution state
                        status = entry.get("status", {})
                        if status.get("status_str") == "error":
                            messages = status.get("messages", [])
                            raise HomeAssistantError(
                                f"ComfyUI workflow execution failed for {prompt_id}: {messages}"
                            )

                        outputs = entry.get("outputs", {})
                        img_ref = next(
                            (img for node_out in outputs.values() for img in node_out.get("images", [])),
                            None,
                        )

                        if img_ref:
                            params = {
                                "filename": img_ref.get("filename", ""),
                                "subfolder": img_ref.get("subfolder", ""),
                                "type": img_ref.get("type", "output"),
                            }
                            view_url = f"{self._base_url}/view"
                            async with session.get(view_url, params=params) as resp:
                                resp.raise_for_status()
                                data = await resp.read()

                            mime, _ = mimetypes.guess_type(img_ref.get("filename", "image.png"))
                            return data, (mime or "image/png")

                        await asyncio.sleep(poll_interval)
                        # Exponential backoff capped at max_interval
                        poll_interval = min(poll_interval * 1.5, max_interval)
        except TimeoutError as err:
            raise HomeAssistantError(f"Timed out waiting for ComfyUI result for {prompt_id}") from err