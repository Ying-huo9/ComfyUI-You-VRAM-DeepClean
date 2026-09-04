# -*- coding: utf-8 -*-
"""ComfyUI-VRAM-DeepClean —— 显存彻底释放,独立通用节点包。"""

from .vram_release import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
