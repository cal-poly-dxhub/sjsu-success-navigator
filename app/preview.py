"""The transport-free half of a streamed turn's preview: what to send, and when.

WHY THIS IS ITS OWN MODULE. A streamed turn has two independent questions in it. "Which
bytes of a half-written reply is it safe to put in front of a student, and how often?" is
one, and the answer is the same wherever the bytes are going. "How do I get a frame to the
client?" is the other, and it is different for every transport - post_to_connection down a
WebSocket, a chunk on an open HTTP response.

The second question already has two answers in this repo and is about to have them at the
same time. The first must never have two, and it nearly did: app/streaming.py's docstring
records the bug that comes from getting it wrong twice - `flush` sliced the PARSED prose
with an offset measured against the RAW stream, and the student watched a sentence restart
in the middle of itself. That bookkeeping lives here now, once.

WHAT A SUBCLASS OWES: `_post(payload) -> bool`, returning True if the frame left. Everything
else - the offset, the accumulated raw text, the batching thresholds, the card announcement,
the safe prefix - is here and is not a transport's business.

WHAT STREAMS IS A PREVIEW, and that is the property this module enforces. `text` is handed
the whole reply so far and pushes `cards.preview_safe_prefix` of it, which stops at the
first tag of the card contract - so a student sees the lead-in the model wrote and never a
half-typed `<card ref="2">`. Nothing here caps it, normalises it, or reads it for meaning.
The final payload replaces it wholesale, and it comes out of the same `_response_from_text`
the buffered path uses. There is no second card parser anywhere near this file.
"""

from __future__ import annotations

import time

from cards import card_block_started, preview_safe_prefix

# The stage a `status` frame carries once the model has started writing card blocks. A WIRE
# VALUE, never a sentence: what the student reads about it is a string in the frontend's
# catalogue, in whichever language they chose. Alongside "retrieving", which the orchestrator
# sends before any text exists; this one marks the other end of the reply.
CARDS_STAGE = "composing_cards"


class PreviewSink:
    """A `orchestrator.StreamSink` with the preview logic filled in and the wire left out.

    BATCHED, and the thresholds are the caller's to choose because the reason to batch is
    the caller's too. Down a WebSocket every push is a billable API Gateway message, so a
    frame per token multiplies the message count by the token count to no visible end - the
    browser reveals text at ~108 characters a second and the model outruns it either way.
    On a response-streamed HTTP body there is no per-frame charge, so the same numbers would
    only add latency. Pass `min_chars=1, max_delay_ms=0` to push everything.

    `gone` and `frames` belong to the subclass's transport but live here because `_post` is
    the only place either changes, and every method above it reads them.
    """

    def __init__(self, *, min_chars: int, max_delay_ms: int):
        self._min_chars = min_chars
        self._max_delay = max_delay_ms / 1000.0
        # True once the client is known to be gone. Every push checks it, so one dead
        # connection stops the rest of the turn's frames rather than raising per delta.
        self.gone = False
        # How much of the preview has been sent, and the RAW accumulated reply it indexes
        # into. The two belong together: `_sent` is an offset into `_accumulated`'s safe
        # prefix and is meaningless against any other string - which is exactly the mistake
        # `flush` used to make, slicing the PARSED prose with an index taken from the raw
        # stream and sending a fragment that began mid-word.
        self._sent = 0
        self._accumulated = ""
        self._last_push = time.monotonic()
        # Whether the "the model has started writing cards" frame has gone out. Sent at
        # most once per turn - see _announce_cards.
        self._cards_announced = False
        self.frames = 0

    def _post(self, payload) -> bool:
        """Put one frame on the wire. True if it left. The one thing a transport owes."""
        raise NotImplementedError

    def accepted(self, conversation_id):
        """The server has taken this turn on, and this is the id it lives under.

        THE FIRST FRAME OF A TURN, ahead of the retrieval status and every delta. The
        server mints the id and an absent one means a new conversation
        (docs/accounts-and-storage.md), so on a fresh conversation the stream is the only
        place a browser can learn it. Learning it from the final payload would be too late
        twice over: there would be nowhere to put the sidebar row until the reply finished,
        and a student who sent their next message first would open a SECOND conversation
        and orphan this one.

        SENT ON A CONTINUING CONVERSATION TOO, echoing the id the client sent. A frame
        whose presence depended on newness would make the client's own state decide whether
        it gets told, and a client that has to know which case it is in cannot use the
        answer to find out.
        """
        self._post({"type": "accepted", "conversationId": conversation_id})

    def status(self, stage):
        """Something is happening that produces no text. The UI can say a true thing."""
        self._post({"type": "status", "stage": stage})

    def text(self, accumulated):
        """The reply so far. Pushes whatever is newly safe to show, if enough has built up."""
        self._accumulated = accumulated
        safe = preview_safe_prefix(accumulated)
        pending = len(safe) - self._sent
        if pending > 0:
            now = time.monotonic()
            if pending >= self._min_chars or (now - self._last_push) >= self._max_delay:
                self._flush_to(safe, now)
        self._announce_cards(accumulated)

    def _announce_cards(self, accumulated):
        """Say ONCE that the model has begun writing cards, and finish the prose first.

        THE SIGNAL IS THE MODEL'S OWN OUTPUT, not a timer and not a guess: `<card` in the
        stream is the same event that stops the preview, so this frame marks the exact
        instant the prose ended and the part the student cannot see began. A reply that
        never writes a card never sends it, which is what stops the browser promising
        resources that are not coming.

        The tail of the preview is flushed FIRST, and that ordering is load-bearing twice
        over. The safe prefix cannot grow past this point, so there is nothing left to wait
        for and the last words of the lead-in should not sit in the batcher behind a
        min_chars threshold they may never reach. And the browser clears the indicator on
        any arriving prose, so a delta landing after this frame would take it back off.
        """
        if self._cards_announced or not card_block_started(accumulated):
            return
        self._cards_announced = True
        self.flush()
        self.status(CARDS_STAGE)

    def flush(self):
        """Push the tail of the preview, once the model has stopped.

        Takes no argument on purpose. It works from the RAW text the sink accumulated, so
        the offset it slices with indexes the string it was measured against - the tail is
        whatever is left of this turn's own preview, never a slice of some other string
        that happens to start the same way.
        """
        safe = preview_safe_prefix(self._accumulated)
        if len(safe) > self._sent:
            self._flush_to(safe, time.monotonic())

    def _flush_to(self, safe, now):
        if self._post({"type": "delta", "text": safe[self._sent :]}):
            self._sent = len(safe)
            self._last_push = now

    def final(self, payload):
        """THE AUTHORITATIVE MESSAGE, and the only one a client renders as the answer.

        `payload` is exactly what POST /chat returns for this turn - the same ChatResponse,
        serialised through the same aliases - so the finished turn is identical whichever
        transport carried it. The preview above was never anything else's input.
        """
        self._post({"type": "final", "payload": payload})

    def error(self, message, **extra):
        """A turn that will not produce an answer, described well enough to render.

        Distinct from a transport failure on purpose: this is the server saying something
        definite (a rate-limit refusal, a failed loop), so the client shows it rather than
        falling back and asking the same question twice.
        """
        self._post({"type": "error", "message": message, **extra})
