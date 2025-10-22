# Unofficial ComfyUI Home Assistant integration

This is an unofficial custom component for Home Assistant that allows for the integration of ComfyUI into Home Assistant. It uses the Home Assistant "AI Task" platform, specifically the "Generate Image" action, to make a request to generate an image using the ComfyUI API.

This integration was originally written for an [XDA article](https://www.xda-developers.com/made-custom-home-assistant-integration-dashboard-new-art/).

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Incipiens&repository=ComfyUI-Home-Assistant)

## Manual installation and setup

To manually install this component (without HACS), upload the contents of the "custom_components" folder in this repository to your Home Assistant's custom components folder. After that, restart Home Assistant. Then, go to your integrations, and add the ComfyUI integration. In ComfyUI, export your saved workflow as a JSON (API) file and download it. Place it anywhere accessible in your Home Assistant storage. Mine is in /config/comfyui.

You will need the following:

* A title for the service that will be added
* The URL of your ComfyUI server. For example, for me, this is "http://192.168.2.110:8188"
* A timeout value in seconds
* The node ID of the prompt. This is found by finding the root-level number in the exported JSON file.
* The resolution ID for image generation. This is found by finding the root-level number in the exported JSON file.
* Width of generated images
* Height of generated images
* The seed ID for image generation, to ensure that images are random, even when the same prompt is invoked. This is found by finding the root-level number in the exported JSON file.
* The workflow path. This is where you uploaded your exported JSON API workflow file. Mine is in /config/comfyui.

Once you add it, you can test it by going to Developer Tools, and select "Generate Image." The entity should now be available with the title that you used.

## Acknowledgements

A big thank you to @loryanstrant, @tronikos, @ivanlh, and @synesthesiam. Through @loryanstrant's Azure AI integration, I was able to nail down the last required pieces of information in order to get this working correctly. I consistently referred back to the work completed by @tronikos and @ivanlh to build the Google Generative AI integration. Finally, @synesthesiam's Ollama integration helped fill in gaps in understanding the AI Task platform in Home Assistant in general.

This project is far from complete, and largely serves as a proof of concept more than anything else. I will likely tweak and refine it over time, but no updates are promised. This was primarily written for an XDA article with a very specific purpose. It does not support attachments for image to image workflows, and only works with a text to image model.