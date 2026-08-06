"""The Converse tool config: retrieval, and nothing else.

`submit_chat_response` is GONE. It was the tool the model called with a JSON payload of
conversationalText plus a cards array, and removing it is the substance of this change
rather than a side effect of it. Under that schema the model typed a `sourceUrl` per card,
which made an invented URL a thing the server had to detect after the fact; and the answer
arrived as tool input, which meant the loop needed a second exit path for the case where the
model just talked instead. Now the model writes one text reply - prose plus <card> blocks -
and `end_turn` is the only way a turn ends. cards.py reads the reply; see prompts.py for the
contract the model is given.
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
