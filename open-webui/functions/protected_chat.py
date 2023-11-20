"""
title: Protected Chat
author: LocalLLM
version: 0.1.0
description: Keeps a protected chat (a chat type made by install/chat-types.sh) inside the computer. On every message it allows only the Protected Lookup tool and switches off web search, image generation, the code interpreter, memory, term ... [+242 chars omitted]
"""

import os
from pydantic import BaseModel, Field

OFF_FEATURES = ("web_search", "image_generation", "code_interpreter", "memory")


class Filter(BaseModel):
    """Filter that disables external tools for protected chats."""

    features_to_disable: tuple = OFF_FEATURES

    def apply(self, request_data: dict) -> dict:
        """Remove disabled features from the request and add the lookup tool."""
        if not request_data.get("protected"):
            return request_data

        # Strip off web-accessing and execution features
        for feature in self.features_to_disable:
            request_data.pop(feature, None)

        # Ensure only the protected lookup tool is available
        tools = request_data.get("tools", [])
        request_data["tools"] = [t for t in tools if t.get("name") == "protected_lookup"]

        # Disable streaming for consistency
        request_data["stream"] = False

        return request_data


def filter_request(request_data: dict) -> dict:
    """Entry point for Open WebUI to filter chat requests."""
    return Filter().apply(request_data)
