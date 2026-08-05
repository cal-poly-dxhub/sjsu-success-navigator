TOOL_CONFIG = {
    "tools": [
        {
            "toolSpec": {
                "name": "retrieve_campus_resources",
                "description": (
                    "Search the official SJSU campus knowledge base for offices, programs, "
                    "services, hours, and how to access help. Use when campus-specific facts are needed."
                ),
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": (
                                    "Focused search query describing the campus information needed."
                                ),
                            }
                        },
                        "required": ["query"],
                    }
                },
            }
        },
        {
            "toolSpec": {
                "name": "submit_chat_response",
                "description": (
                    "Submit the final answer to the student. Call when ready to respond. "
                    "Use an empty cards array for talk-only replies."
                ),
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "conversationalText": {
                                "type": "string",
                                "description": "Sammy's spoken reply in the chat UI.",
                            },
                            "needsSafetyHandoff": {
                                "type": "boolean",
                                "description": (
                                    "Set true when the student may be in crisis, at risk of self-harm, "
                                    "experiencing violence or assault, or needs urgent mental health help. "
                                    "The UI will show official crisis buttons (988, CAPS, etc.). "
                                    "Use cards: [] and do not list hotline numbers in conversationalText."
                                ),
                            },
                            "cards": {
                                "type": "array",
                                "description": "Up to 4 resource referral cards. Empty for talk-only.",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "title": {"type": "string"},
                                        "body": {
                                            "type": "string",
                                            "description": "Short summary grounded in retrieved text.",
                                        },
                                        "sourceUrl": {
                                            "type": "string",
                                            "description": "Official SJSU page URL from retrieval results.",
                                        },
                                    },
                                    "required": ["title", "body", "sourceUrl"],
                                },
                            },
                        },
                        "required": ["conversationalText", "cards"],
                    }
                },
            }
        },
    ]
}
