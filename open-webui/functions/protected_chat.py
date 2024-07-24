"""
title: Protected Chat
author: LocalLLM
version: 0.1.0
description: Keeps a protected chat (a chat type made by install/chat-types.sh) inside the computer. On every message it allows only the Protected Lookup tool and switches off web search, image generation, the code interpreter, memory, term ... [+242 chars omitted]
"""

from pydantic import BaseModel, Field

OFF_FEATURES = ("web_search", "image_generation", "code_interpreter", "memory")


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=1000, description="Order among filters (lower runs first). Runs last, so nothing switches tools back on."
        )
        lookup_tool_id: str = Field(
            default="protected_lookup", description="The only tool a protected chat may use (Workspace > Tools id)."
        )

    def __init__(self):
        self.valves = self.Valves()
        # No self.toggle: a filter with a toggle could be switched off in the chat.

    async def inlet(self, body: dict, __metadata__: dict = None) -> dict:
        body["tool_ids"] = [self.valves.lookup_tool_id]
        features = body.get("features")
        features = dict(features) if isinstance(features, dict) else {}
        for name in OFF_FEATURES:
            features[name] = False
        body["features"] = features
        body["terminal_id"] = None
        # Tool servers the browser connects to directly are read from the request's metadata.
        for md in (__metadata__, body.get("metadata")):
            if isinstance(md, dict):
                md["tool_servers"] = None
                md["terminal_id"] = None
                if isinstance(md.get("features"), dict):
                    md["features"] = {**md["features"], **{n: False for n in OFF_FEATURES}}
        return body
