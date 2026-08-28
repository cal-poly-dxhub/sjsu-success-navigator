"""The transport-free half of a streamed turn's preview: what to send, and when.

A subclass owes `_post(payload) -> bool`. What streams is a preview; the final payload
replaces it wholesale.
"""

from __future__ import annotations

import time

from cards import card_block_started, preview_safe_prefix

# A wire value, never a sentence: what the student reads is in the frontend's catalogue.
CARDS_STAGE = "composing_cards"


class PreviewSink:
    """The preview logic with the wire left out. Batching thresholds are the caller's."""

    def __init__(self, *, min_chars: int, max_delay_ms: int):
        self._min_chars = min_chars
        self._max_delay = max_delay_ms / 1000.0
        # Checked by every push, so one dead client stops the rest of the turn's frames.
        self.gone = False
        # `_sent` indexes `_accumulated`'s safe prefix and is meaningless against anything else.
        self._sent = 0
        self._accumulated = ""
        self._last_push = time.monotonic()
        # The cards frame goes out at most once per turn.
        self._cards_announced = False
        self.frames = 0

    def _post(self, payload) -> bool:
        """True if the frame left. The one thing a transport owes."""
        raise NotImplementedError

    def accepted(self, conversation_id):
        """The first frame of a turn, sent on a continuing conversation as well as a new one."""
        self._post({"type": "accepted", "conversationId": conversation_id})

    def status(self, stage):
        """Something is happening that produces no text."""
        self._post({"type": "status", "stage": stage})

    def text(self, accumulated):
        """The reply so far. Pushes what is newly safe to show, once enough has built up."""
        self._accumulated = accumulated
        safe = preview_safe_prefix(accumulated)
        pending = len(safe) - self._sent
        if pending > 0:
            now = time.monotonic()
            if pending >= self._min_chars or (now - self._last_push) >= self._max_delay:
                self._flush_to(safe, now)
        self._announce_cards(accumulated)

    def _announce_cards(self, accumulated):
        """Once per turn, and the prose is flushed first: an arriving delta clears the indicator."""
        if self._cards_announced or not card_block_started(accumulated):
            return
        self._cards_announced = True
        self.flush()
        self.status(CARDS_STAGE)

    def flush(self):
        """Takes no argument: the offset only indexes the raw text this sink accumulated."""
        safe = preview_safe_prefix(self._accumulated)
        if len(safe) > self._sent:
            self._flush_to(safe, time.monotonic())

    def _flush_to(self, safe, now):
        if self._post({"type": "delta", "text": safe[self._sent :]}):
            self._sent = len(safe)
            self._last_push = now

    def final(self, payload):
        """Exactly what POST /chat returns, and the only frame a client renders as the answer."""
        self._post({"type": "final", "payload": payload})

    def error(self, message, **extra):
        """The server saying something definite, so the client shows it instead of retrying."""
        self._post({"type": "error", "message": message, **extra})
