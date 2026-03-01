DOMAIN = "comfyui_generator"

CONF_BASE_URL = "base_url"
CONF_TIMEOUT = "timeout"
CONF_WORKFLOW = "workflow"
CONF_PROMPT_PLACEHOLDER = "prompt_placeholder" 

CONF_WORKFLOW_MODE = "workflow_mode" # Remnant of old workflow mode system
CONF_WORKFLOW_INLINE = "workflow_inline"
CONF_WORKFLOW_PATH = "workflow_path"
CONF_WORKFLOW_URL = "workflow_url"

CONF_WORKFLOW_PROMPT_NODE_ID = "workflow_prompt_node_id"  # node ID to target for prompt replacement
CONF_WORKFLOW_RESOLUTION_NODE_ID = "workflow_resolution_node_id"  # node ID to target for resolution override
CONF_SEED_NODE_ID = "seed_node_id"  # node ID to target for seed replacement

CONF_IMAGE_W = "image_width"
CONF_IMAGE_H = "image_height"

CONF_INPUT_IMAGE_NODE_ID = "input_image_node_id"  # node ID for Load Image node (img2img)
CONF_DENOISE_NODE_ID = "denoise_node_id"  # node ID for denoise strength (optional)
CONF_DEFAULT_DENOISE = "default_denoise"  # default denoise value
DEFAULT_DENOISE = 0.75

CONF_WORKFLOW_TITLE = "workflow_title"  # optional title for the workflow / AI task

DEFAULT_AI_TASK_NAME = "ComfyUI Task"

DEFAULT_TIMEOUT = 120
DEFAULT_PLACEHOLDER = "{{prompt}}"

