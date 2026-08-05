"""Follow-up button presets, keyed on the crawl list's `section` vocabulary.

WHY THIS IS APPLICATION CONTENT AND NOT config.yaml: the table is identical for every
deploy - it is a property of the corpus, not of an environment - so it ships with the
function asset. It would also not fit the alternative: Lambda caps all environment
variables at 4 KB in aggregate, and this table alone is over 2 KB (the same limit that
forced the crawl list into its own layer).

WHY IT REPLACED CAMP'S TABLE OUTRIGHT. Camp keyed its presets on ITS section vocabulary
(`peerconnections`, `faso`, `aec`, ...). Ours is different (`tutoring-academic-support`,
`financial-aid`, `accessibility-aec`, ...) and the two do not overlap on a single value,
so every card was silently getting the generic follow-up - the exact silent degradation
the scraper's `section` requirement exists to prevent.

TWO RULES, both load-bearing:

  1. EVERY section in url-list.csv has an EXPLICIT entry here. There is no fallthrough
     that quietly invents a label, and test_every_crawl_list_section_has_an_explicit_entry
     reads the crawl list at test time to prove it - so adding a section to the corpus
     without deciding its follow-up fails the build rather than shipping.
  2. A section with no honest follow-up maps to GENERIC, explicitly, and is never pointed
     at a plausibly-related office. A student sent to the wrong department has been given
     a worse answer than one handed a neutral "tell me more" - they act on it, and the
     mistake costs them a trip.

Each prompt has to be answerable from that section's OWN pages. A prompt that routes
somewhere else is the referral mistake wearing a different hat.
"""

from __future__ import annotations

# The label used when a section has no honest section-specific follow-up. The prompt is
# built from the card's title at call time, so it can never name an office we did not
# retrieve.
GENERIC_LABEL = "Learn more"

# section -> (button label, prompt sent when the student clicks it), or None for GENERIC.
# Ordered as url-list.csv orders them, so the two are diffable side by side.
SECTION_FOLLOWUPS: dict[str, tuple[str, str] | None] = {
    "academic-advising": ("Find advisor", "How do I meet with an academic advisor?"),
    "accessibility-aec": (
        "Register with AEC",
        "How do I register with the Accessible Education Center?",
    ),
    "basic-needs": (
        "Get basic needs help",
        "How do I get help with food or housing insecurity?",
    ),
    "bursar-billing": ("Billing help", "How do I pay my bill or ask about a charge?"),
    "career-services": ("Explore careers", "What career services are available to me?"),
    "counseling-psych": (
        "Counseling options",
        "How do I make a counseling appointment?",
    ),
    "eop-guardian-scholars": (
        "Check eligibility",
        "Am I eligible for EOP or Guardian Scholars?",
    ),
    "financial-aid": ("Financial aid help", "How do I get help with my financial aid?"),
    "graduate-studies": (
        "Grad requirements",
        "What do graduate students need to know about this?",
    ),
    "health-services": (
        "Health services",
        "How do I make a health services appointment?",
    ),
    "housing": ("Housing options", "How do I apply for campus housing?"),
    "identity-belonging": (
        "Find community",
        "What does this center offer students?",
    ),
    "international-isss": (
        "International support",
        "What support is available for international students?",
    ),
    "library": ("Library help", "How do I use this library service?"),
    "ombuds": ("Ombuds help", "What does the Ombuds office do and how do I reach them?"),
    "registrar-enrollment": (
        "Enrollment help",
        "How do I register for classes or request my records?",
    ),
    # A hub page rather than a service: any specific follow-up would have to name an
    # office the card did not come from. GENERIC on purpose - see rule 2.
    "student-affairs-hub": None,
    "student-conduct": (
        "Conduct process",
        "How does the student conduct process work?",
    ),
    "student-involvement": (
        "Get involved",
        "How do I join a club or student organization?",
    ),
    "testing": ("Testing info", "How do I schedule a test with the testing office?"),
    "title-ix": ("Title IX support", "How do I report a concern or get support?"),
    "tutoring-academic-support": (
        "Book tutor",
        "How do I book a tutoring appointment?",
    ),
    "veterans": (
        "Veterans support",
        "What support is available for student veterans?",
    ),
}


def followup_for_section(section: str | None, title: str) -> tuple[str, str]:
    """The (label, prompt) pair for a card in `section`.

    An unknown or absent section is NOT an error at runtime - it means retrieval returned
    a document whose sidecar predates a crawl-list change, and a student's answer must not
    fail over that. It gets GENERIC, which is the same thing an honest-match-less section
    gets. The build-time guarantee that every CONFIGURED section is present lives in the
    test, where it can fail loudly without costing anyone an answer.
    """
    preset = SECTION_FOLLOWUPS.get((section or "").strip().lower())
    if preset is None:
        return (GENERIC_LABEL, f"Tell me more about {title}.")
    return preset


def known_sections() -> frozenset[str]:
    """Every section this table has an explicit entry for. Read by the coverage test."""
    return frozenset(SECTION_FOLLOWUPS)
