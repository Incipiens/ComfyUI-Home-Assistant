"""AI Task integration for ComfyUI Image Generation."""

from __future__ import annotations

import logging
from typing import Any, Dict, TYPE_CHECKING
import aiohttp
import mimetypes
import json
import asyncio
import async_timeout
import random
import uuid

from homeassistant.components import ai_task
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

_LOGGER = logging.getLogger(__name__)

from .const import (
    DOMAIN,
    CONF_BASE_URL, CONF_TIMEOUT,
    DEFAULT_TIMEOUT, DEFAULT_AI_TASK_NAME,
    CONF_WORKFLOW_MODE, CONF_WORKFLOW_PATH, CONF_SEED_NODE_ID,
    CONF_WORKFLOW_RESOLUTION_NODE_ID, CONF_WORKFLOW_PROMPT_NODE_ID,
    CONF_IMAGE_W, CONF_IMAGE_H, CONF_WORKFLOW_TITLE,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    # Set up the ComfyUI AI Task entity
    async_add_entities([ComfyUITaskEntity(hass, config_entry)])

# ComfyUI AI Task Entity
class ComfyUITaskEntity(ai_task.AITaskEntity):
    """AI Task entity that uses ComfyUI for image generation."""
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__()
        self._attr_has_entity_name = True
        self.hass = hass
        self.entry = entry
        data = entry.data
        self._attr_name = data.get(CONF_WORKFLOW_TITLE, "")
        self._base_url = data.get(CONF_BASE_URL, "")
        self._timeout = data.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
        self._wf_mode = data.get(CONF_WORKFLOW_MODE, "file")
        self._wf_path = data.get(CONF_WORKFLOW_PATH, "")
        self._wf_resolution_node_id = data.get(CONF_WORKFLOW_RESOLUTION_NODE_ID, "")
        self._wf_resolution_w = data.get(CONF_IMAGE_W, 800)
        self._wf_resolution_h = data.get(CONF_IMAGE_H, 480)
        self._wf_seed_node_id = data.get(CONF_SEED_NODE_ID, "")
        self._wf_prompt_node_id = data.get(CONF_WORKFLOW_PROMPT_NODE_ID, "")

        self._attr_supported_features = ai_task.AITaskEntityFeature.GENERATE_IMAGE

        self._cached_wf_text: str | None = None

    def _validate_node_ids(self, nodes: Dict[str, Any]) -> None:
        """Log warnings for configured missing node IDs"""
        checks = {
            "prompt": self._wf_prompt_node_id,
            "resolution": self._wf_resolution_node_id,
            "seed": self._wf_seed_node_id,
        }
        for label, node_id in checks.items():
            if node_id and str(node_id) not in nodes:
                _LOGGER.warning(
                    "Configured %s node ID '%s' not found in workflow. "
                    "Available node IDs: %s",
                    label, node_id, list(nodes.keys()),
                )

    async def _prepare_workflow(self, prompt_text: str) -> Dict[str, Any]:
        wf = await self._load_workflow_json()

        # Normalize to dict
        if not isinstance(wf, dict):
            try:
                wf = json.loads(wf)
            except Exception as e:
                raise ValueError(
                    f"_load_workflow_json() must return a dict or JSON string. Got {type(wf).__name__}"
                ) from e

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

        self._validate_node_ids(nodes)

        # Injecting node values
        nodes = self._inject_prompt_text_at_node(nodes, self._wf_prompt_node_id, self._wf_resolution_node_id, self._wf_seed_node_id,
                                                self._wf_resolution_w, self._wf_resolution_h, prompt_text)

        # Returning nodes
        return nodes
    
    # Injector, should really be renamed as it doesn't just handle prompts
    @staticmethod
    def _inject_prompt_text_at_node(nodes: Dict[str, Any], prompt_node_id: int | str, resolution_node_id: int | str, seed_node_id: int | str, 
                                    resolution_w: int | str, resolution_h: int | str, prompt_text: str) -> Dict[str, Any]:
        # Set prompt
        node = nodes.get(str(prompt_node_id)) 
        if isinstance(node, dict):
            inputs = node.get("inputs")
            if isinstance(inputs, dict) and "text" in inputs:
                inputs["text"] = prompt_text

        # Set resolution
        node = nodes.get(str(resolution_node_id)) 
        if isinstance(node, dict):
            inputs = node.get("inputs")
            if isinstance(inputs, dict) and "width" in inputs:
                inputs["width"] = resolution_w
            if isinstance(inputs, dict) and "height" in inputs:
                inputs["height"] = resolution_h

        # Randomise seed
        node = nodes.get(str(seed_node_id))
        if isinstance(node, dict):
            inputs = node.get("inputs")
            if isinstance(inputs, dict) and "seed" in inputs:
                inputs["seed"] = random.randint(0, 2**64 - 1)
        return nodes

    # Generator
    async def _async_generate_image(
        self,
        task: ai_task.GenImageTask,
        chat_log,  # kept for compatibility; unused
    ) -> ai_task.GenImageTaskResult:
        prompt_text: str = task.instructions or ""

        try:
            workflow_obj = await self._prepare_workflow(prompt_text)

            # WebSocket-first with polling fallback
            try:
                from .websocket_client import ComfyUIWebSocketClient

                client_id = str(uuid.uuid4())
                prompt_id = await self._post_prompt(workflow_obj, client_id=client_id)
                ws_client = ComfyUIWebSocketClient(self._base_url, self._timeout)
                await ws_client.wait_for_completion(prompt_id, client_id)
                image_bytes, mime_type = await self._fetch_image_from_history(
                    prompt_id
                )
            except (aiohttp.WSServerHandshakeError, ConnectionError) as ws_err:
                _LOGGER.warning(
                    "WebSocket unavailable, falling back to polling: %s", ws_err
                )
                prompt_id = await self._post_prompt(workflow_obj)
                image_bytes, mime_type = await self._fetch_first_image_bytes(
                    prompt_id
                )

        except Exception as err:
            raise HomeAssistantError(f"Error generating image: {err}") from err

        return ai_task.GenImageTaskResult(
            image_data=image_bytes,
            mime_type=mime_type,
            model="ComfyUI",
            conversation_id=None,
            revised_prompt=prompt_text,
            width=self._wf_resolution_w,
            height=self._wf_resolution_h,
        )
    
    # Structured so that it can be expanded to add more loaders
    async def _load_workflow_json(self) -> str:
        mode = self._wf_mode
        if mode == "file":
            p = self._wf_path.strip()
            if not p or not p.startswith("/config/"):
                raise ValueError("Workflow file must be an absolute path under /config")

            def _read_file() -> str:
                with open(p, "r", encoding="utf-8") as f:
                    return f.read()
                
            # Converted to async
            return await self.hass.async_add_executor_job(_read_file)
        raise ValueError(f"Unknown workflow mode: {mode}")
    
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
            with async_timeout.timeout(self._timeout):
                async with sess.post(url, json=payload) as resp:
                    resp.raise_for_status()
                    js = await resp.json()
                    prompt_id = js.get("prompt_id") or js.get("promptId")
                    if not prompt_id:
                        raise RuntimeError(f"ComfyUI did not return prompt_id: {js}")
                    return prompt_id
                
    # Single GET to /history/{prompt_id} (used after WebSocket confirms completion)
    async def _fetch_image_from_history(
        self, prompt_id: str
    ) -> tuple[bytes, str]:
        """Fetch the first image from /history/{prompt_id} (already completed)."""
        async with aiohttp.ClientSession() as session:
            hist_url = f"{self._base_url}/history/{prompt_id}"
            async with session.get(hist_url) as r:
                r.raise_for_status()
                hist = await r.json()

            entry = hist.get(prompt_id, {})
            outputs = entry.get("outputs", {})

            img_ref = None
            for node_out in outputs.values():
                for img in node_out.get("images", []):
                    img_ref = img
                    break
                if img_ref:
                    break

            if not img_ref:
                raise HomeAssistantError(
                    f"No images found in ComfyUI history for {prompt_id}"
                )

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

    # Poll /history/{prompt_id} and GET /view for image bytes, times out after self._timeout seconds
    async def _fetch_first_image_bytes(self, prompt_id: str) -> tuple[bytes, str]:
        """Poll /history/{prompt_id} then GET bytes from /view for the top image."""
        timeout_s = self._timeout
        poll_interval = 0.5
        max_interval = 5.0
        elapsed = 0.0

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_s)) as session:
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

                img_ref = None
                for node_out in outputs.values():
                    for img in node_out.get("images", []):
                        img_ref = img
                        break
                    if img_ref:
                        break

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
                elapsed += poll_interval
                if elapsed >= timeout_s:
                    raise HomeAssistantError(f"Timed out waiting for ComfyUI result for {prompt_id}")
                # Exponential backoff capped at max_interval
                poll_interval = min(poll_interval * 1.5, max_interval)