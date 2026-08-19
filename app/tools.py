"""The Converse tool config: retrieval, and nothing else.

The model writes its whole turn as text and cards.py reads it; see docs/cards-v2.md.
"""

TOOL_CONFIG = {
    "tools": [
        {
            "toolSpec": {
                "name": "retrieve_campus_resources",
                "description": (
                    "Search the official SJSU campus knowledge base for offices, programs, "
                    "services, hours, and how to access help. Use when campus-specific facts "
                    "are needed. Results come back numbered; cite a result in a card with "
                    "ref=\"<id>\"."
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
        }
    ]
}
