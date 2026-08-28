"""Load and validate the repo-root config.yaml for the CDK app.

The stack is L1 `Cfn*` all the way down and L1 constructs enforce no property
constraints at synth, so these validators move that enforcement to where being wrong
costs nothing. Every global name derives from here, so each is spelled once.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = _REPO_ROOT / "config.yaml"
# Read at synth as well as at runtime, so a broken file fails `cdk synth` rather than deploy.
_DATA_DIR = _REPO_ROOT / "data"

# Bedrock's own pattern, verbatim: up to 100 groups of one alphanumeric and at most one
# separator, so a leading or doubled separator is rejected and a trailing one is not.
_BEDROCK_NAME_RE = re.compile(r"^([0-9a-zA-Z][_-]?){1,100}$")

# Lowercase only and no underscores, which is the difference from the Bedrock pattern above.
_S3_VECTORS_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_S3_VECTORS_NAME_MIN = 3
_S3_VECTORS_NAME_MAX = 63

# Immutable after the index is created, so exceeding it means replacing the index.
_MAX_NON_FILTERABLE_KEYS = 10

# The index dimension must equal the model's output or every ingestion fails, and it is
# immutable, so a mismatch is another replacement rather than a fix.
_EMBEDDING_MODEL_DIMENSIONS = {
    "amazon.titan-embed-text-v2:0": (1024, 512, 256),
}
# S3 Vectors' own hard range, for an embedding model not in the table above.
_DIMENSION_MIN = 1
_DIMENSION_MAX = 4096

# Listed so an unsupported value is named here rather than rejected at deploy.
_CHUNKING_STRATEGIES = ("FIXED_SIZE", "NONE", "HIERARCHICAL", "SEMANTIC")

# `section` is required rather than defaulted because a blank one degrades silently.
_SEED_COLUMNS = ("url", "section", "title")

# Not a knob: an HTTP API integration tops out at 30,000 ms, so this is the ceiling minus
# one and the function's own timeout wins. Here, beside the validator that reads it.
CHAT_LAMBDA_TIMEOUT_SECONDS = 29

# Enforced by the service and not by the L1, so without these a bad value first surfaces
# as a failed change set.
_GUARDRAIL_NAME_RE = re.compile(r"^[0-9a-zA-Z_-]+$")
_GUARDRAIL_NAME_MAX = 50
_GUARDRAIL_MESSAGE_MAX = 500
_GUARDRAIL_FILTER_TYPES = (
    "SEXUAL",
    "VIOLENCE",
    "HATE",
    "INSULTS",
    "MISCONDUCT",
    "PROMPT_ATTACK",
)
_GUARDRAIL_FILTER_STRENGTHS = ("NONE", "LOW", "MEDIUM", "HIGH")

# Out of range is a runtime exception on every query, not a deploy failure, so the stack
# comes up clean and every request 502s.
_NUMBER_OF_RESULTS_MIN = 1
_NUMBER_OF_RESULTS_MAX = 100

# Dropped-digit detectors rather than design minimums: low enough that any deliberate value
# clears them, high enough that 60-for-600 or 8-for-80 does not.
_CONVERSATION_TITLE_MIN_CHARS = 20

_CARD_TITLE_MIN_CHARS = 20
_CARD_DESC_MIN_CHARS = 100
_CARD_FOLLOWUP_MIN_CHARS = 20

# The same detector one size up, and this is the one cap whose violation drops the offer
# rather than shortening anything.
_ESCALATION_MIN_CHARS = 300

# Wider than the S3 Vectors charset, which is why it is its own pattern. A rename discovered
# after the first deploy replaces the table and takes the history with it.
_DYNAMODB_TABLE_NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")
_DYNAMODB_TABLE_NAME_MIN = 3
_DYNAMODB_TABLE_NAME_MAX = 255

# A profile and a bare model id need different IAM shapes, so the distinction lives here.
_INFERENCE_PROFILE_PREFIXES = ("us", "eu", "apac", "us-gov")

# Named for the provider's role, never for whose org is behind it, and a constant rather
# than a key because a key is an invitation to set it to "SJSU". Renaming it is a migration:
# a federated username is `<providerName>_<nameid>`, so a new name mints a new `sub`, which
# is the DynamoDB partition key, and orphans every conversation the old identity wrote.
OKTA_PROVIDER_NAME = "Okta"

# A default, so an org that spells it otherwise sets a key instead of editing code.
_DEFAULT_OKTA_EMAIL_ATTRIBUTE = "email"


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Parse config.yaml into a dict. Defaults to the repo-root config.yaml."""
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    with open(config_path, "r") as f:
        return yaml.safe_load(f)


def _require_mapping(config: Dict[str, Any], section: str) -> Dict[str, Any]:
    """One top-level config block, or a synth-time error naming the block."""
    block = config.get(section)
    if not isinstance(block, dict):
        raise ValueError(
            f"config.yaml is missing the `{section}` block (or it is not a mapping) - "
            "every stack section reads its knobs from config, never from a literal in the stack."
        )
    return block


def _positive_int(block: Dict[str, Any], section: str, key: str) -> int:
    """`bool` is rejected explicitly, or `True` would pass as 1."""
    value = block.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{section}.{key} must be a positive integer (got {value!r}).")
    return value


def _non_empty_str(block: Dict[str, Any], section: str, key: str) -> str:
    """A non-empty string knob, or a synth-time error."""
    value = block.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{section}.{key} must be a non-empty string (got {value!r}).")
    return value.strip()


def _check_bedrock_name(name: str, field: str) -> str:
    """Validate a Bedrock resource name against the service's own pattern."""
    if not _BEDROCK_NAME_RE.match(name):
        raise ValueError(
            f"{field} is not a valid Bedrock resource name: {name!r}. Bedrock requires "
            "alphanumeric groups joined by at most one '-' or '_' each (max 100 groups), so "
            "a leading/trailing separator or a doubled '--' is rejected - at DEPLOY time, "
            "since L1 Cfn* constructs do not check patterns at synth."
        )
    return name


def _check_s3_vectors_name(name: str, field: str) -> str:
    """Validate an S3 Vectors bucket or index name against the service's own rules."""
    if not (_S3_VECTORS_NAME_MIN <= len(name) <= _S3_VECTORS_NAME_MAX):
        raise ValueError(
            f"{field} must be {_S3_VECTORS_NAME_MIN}-{_S3_VECTORS_NAME_MAX} characters "
            f"(got {len(name)}: {name!r})."
        )
    if not _S3_VECTORS_NAME_RE.match(name):
        raise ValueError(
            f"{field} is not a valid S3 Vectors name: {name!r}. Allowed: lowercase letters, "
            "digits, '-' and '.', starting and ending with a letter or digit. Note there are "
            "NO underscores and NO uppercase here, unlike the Bedrock name pattern."
        )
    return name


def resolve_knowledge_base(config: Dict[str, Any]) -> Dict[str, Any]:
    """The `knowledge_base` block: KB name, embedding model, vector dimension."""
    kb_cfg = _require_mapping(config, "knowledge_base")
    name = _check_bedrock_name(
        _non_empty_str(kb_cfg, "knowledge_base", "name"), "knowledge_base.name"
    )
    embedding_model_id = _non_empty_str(
        kb_cfg, "knowledge_base", "embedding_model_id"
    )
    dimension = _positive_int(kb_cfg, "knowledge_base", "vector_dimension")

    supported = _EMBEDDING_MODEL_DIMENSIONS.get(embedding_model_id)
    if supported is not None:
        if dimension not in supported:
            raise ValueError(
                f"knowledge_base.vector_dimension {dimension} is not an output size of "
                f"{embedding_model_id} (supported: {', '.join(str(d) for d in supported)}). "
                "The index dimension must equal the model's output or ingestion fails, and "
                "the index dimension is immutable - so this is an index replacement, not a fix."
            )
    elif not (_DIMENSION_MIN <= dimension <= _DIMENSION_MAX):
        # No table for this model, so fall back to S3 Vectors' own range.
        raise ValueError(
            f"knowledge_base.vector_dimension must be {_DIMENSION_MIN}-{_DIMENSION_MAX} "
            f"(got {dimension}); S3 Vectors rejects anything outside that range."
        )

    return {
        "name": name,
        "embedding_model_id": embedding_model_id,
        "vector_dimension": dimension,
    }


def resolve_vector_store(config: Dict[str, Any]) -> Dict[str, Any]:
    """The `vector_store` block: S3 Vectors bucket/index names and index shape."""
    vs_cfg = _require_mapping(config, "vector_store")
    vector_bucket_name = _check_s3_vectors_name(
        _non_empty_str(vs_cfg, "vector_store", "vector_bucket_name"),
        "vector_store.vector_bucket_name",
    )
    index_name = _check_s3_vectors_name(
        _non_empty_str(vs_cfg, "vector_store", "index_name"), "vector_store.index_name"
    )

    data_type = _non_empty_str(vs_cfg, "vector_store", "data_type")
    if data_type != "float32":
        raise ValueError(
            f"vector_store.data_type must be 'float32' (got {data_type!r}) - it is the only "
            "data type S3 Vectors supports."
        )
    distance_metric = _non_empty_str(vs_cfg, "vector_store", "distance_metric")
    if distance_metric not in ("cosine", "euclidean"):
        raise ValueError(
            f"vector_store.distance_metric must be 'cosine' or 'euclidean' (got "
            f"{distance_metric!r})."
        )

    keys = vs_cfg.get("non_filterable_metadata_keys")
    if not keys or not isinstance(keys, list) or not all(isinstance(k, str) and k.strip() for k in keys):
        raise ValueError(
            "vector_store.non_filterable_metadata_keys must be a non-empty list of key "
            "strings. Bedrock's internal metadata keys have to be marked non-filterable or "
            "every ingestion fails on S3 Vectors' filterable-metadata limit."
        )
    if len(keys) > _MAX_NON_FILTERABLE_KEYS:
        raise ValueError(
            f"vector_store.non_filterable_metadata_keys holds {len(keys)} keys; S3 Vectors "
            f"allows at most {_MAX_NON_FILTERABLE_KEYS} per index. This setting is IMMUTABLE "
            "after index creation, so exceeding it means replacing the index, not editing it."
        )
    if len(set(keys)) != len(keys):
        raise ValueError(
            "vector_store.non_filterable_metadata_keys contains duplicates; each duplicate "
            f"spends one of the {_MAX_NON_FILTERABLE_KEYS} available slots for nothing."
        )

    return {
        "vector_bucket_name": vector_bucket_name,
        "index_name": index_name,
        "data_type": data_type,
        "distance_metric": distance_metric,
        "non_filterable_metadata_keys": list(keys),
    }


def resolve_chunking(config: Dict[str, Any]) -> Dict[str, Any]:
    """The `chunking` block, plus the name suffix that encodes it."""
    chunking_cfg = _require_mapping(config, "chunking")
    strategy = _non_empty_str(chunking_cfg, "chunking", "strategy")
    if strategy not in _CHUNKING_STRATEGIES:
        raise ValueError(
            f"chunking.strategy {strategy!r} is not a Bedrock chunking strategy "
            f"({', '.join(_CHUNKING_STRATEGIES)})."
        )
    if strategy != "FIXED_SIZE":
        raise ValueError(
            f"chunking.strategy is {strategy!r}, but the stack only wires FIXED_SIZE "
            "chunking (gav's baseline). Supporting another strategy is a code change in the "
            "KB section, not a config edit - otherwise the strategy name and the chunking "
            "configuration the data source actually gets would disagree silently."
        )

    max_tokens = _positive_int(chunking_cfg, "chunking", "max_tokens")
    overlap = chunking_cfg.get("overlap_percentage")
    if isinstance(overlap, bool) or not isinstance(overlap, int) or not (1 <= overlap <= 99):
        raise ValueError(
            f"chunking.overlap_percentage must be an integer 1-99 (got {overlap!r}); Bedrock "
            "expresses overlap as a percentage of max_tokens."
        )

    return {
        "strategy": strategy,
        "max_tokens": max_tokens,
        "overlap_percentage": overlap,
        # Underscores are dropped because this rides inside a Bedrock name.
        "name_suffix": f"{strategy.lower().replace('_', '')}-{max_tokens}t{overlap}p",
    }


def resolve_data_source_name(config: Dict[str, Any]) -> str:
    """The KB's S3 data source name, with the chunking configuration folded in."""
    kb_name = resolve_knowledge_base(config)["name"]
    suffix = resolve_chunking(config)["name_suffix"]
    return _check_bedrock_name(f"{kb_name}-s3-{suffix}", "the derived data source name")


def resolve_scraper(config: Dict[str, Any]) -> Dict[str, Any]:
    """The `scraper` block: single daily schedule, HTTP knobs, and the crawl-list filename."""
    scraper_cfg = _require_mapping(config, "scraper")
    schedule_cron = _non_empty_str(scraper_cfg, "scraper", "schedule_cron")
    if not (schedule_cron.startswith("cron(") and schedule_cron.endswith(")")):
        raise ValueError(
            f"scraper.schedule_cron must be an EventBridge cron expression of the form "
            f"'cron(...)' (got {schedule_cron!r}). EventBridge cron has SIX fields and a "
            "'?' in either day-of-month or day-of-week; a 5-field UNIX cron is rejected at "
            "deploy, not here."
        )
    return {
        "schedule_cron": schedule_cron,
        "url_list_file": _non_empty_str(scraper_cfg, "scraper", "url_list_file"),
        "timeout_seconds": _positive_int(scraper_cfg, "scraper", "timeout_seconds"),
        "user_agent": _non_empty_str(scraper_cfg, "scraper", "user_agent"),
    }


def data_file_path(name: str) -> Path:
    """Absolute path to one file in the repo-root `data/` directory."""
    return _DATA_DIR / name


def seed_list_path(config: Dict[str, Any]) -> Path:
    """Absolute path to the curated crawl list named by `scraper.url_list_file`."""
    return data_file_path(resolve_scraper(config)["url_list_file"])


def resolve_seed_pages(config: Dict[str, Any]) -> List[Dict[str, str]]:
    """The curated crawl list as `[{"url", "section", "title"}]`, in file order."""
    path = seed_list_path(config)
    if not path.exists():
        raise ValueError(
            f"the crawl list named by scraper.url_list_file does not exist: {path}. It is "
            "the scraper's only source of URLs, so a missing file means a run that fetches "
            "nothing - and prunes everything."
        )

    # Local, because this is the only function in the file that reads a CSV.
    import csv

    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        columns = reader.fieldnames or []
        missing = [c for c in _SEED_COLUMNS if c not in columns]
        if missing:
            raise ValueError(
                f"{path.name} is missing required column(s): {', '.join(missing)}. Required: "
                f"{', '.join(_SEED_COLUMNS)} (`section` reaches the metadata sidecars and "
                "drives card deprioritization and follow-up buttons)."
            )
        rows = list(reader)

    pages: List[Dict[str, str]] = []
    seen: set = set()
    for line_number, row in enumerate(rows, start=2):  # start=2: line 1 is the header
        page = {}
        for column in _SEED_COLUMNS:
            value = (row.get(column) or "").strip()
            if not value:
                raise ValueError(
                    f"{path.name} line {line_number}: `{column}` is empty. Every column in "
                    f"{', '.join(_SEED_COLUMNS)} is required for every page."
                )
            page[column] = value
        if not page["url"].startswith(("http://", "https://")):
            raise ValueError(
                f"{path.name} line {line_number}: {page['url']!r} is not an http(s) URL."
            )
        if page["url"] in seen:
            raise ValueError(
                f"{path.name} line {line_number}: {page['url']} is listed more than once. A "
                "duplicate costs an extra fetch per run and writes the same S3 object twice."
            )
        seen.add(page["url"])
        pages.append(page)

    if not pages:
        raise ValueError(
            f"{path.name} has a valid header but no pages. An empty crawl list means the "
            "scraper fetches nothing and its prune deletes the whole knowledge base."
        )
    return pages


def resolve_guardrail(config: Dict[str, Any]) -> Dict[str, Any]:
    """The `guardrail` block: one guardrail, PROMPT_ATTACK on input only."""
    guardrail_cfg = _require_mapping(config, "guardrail")

    name = _non_empty_str(guardrail_cfg, "guardrail", "name")
    if len(name) > _GUARDRAIL_NAME_MAX or not _GUARDRAIL_NAME_RE.match(name):
        raise ValueError(
            f"guardrail.name must be 1-{_GUARDRAIL_NAME_MAX} characters of letters, digits, "
            f"'-' or '_' (got {name!r}, {len(name)} chars). Bedrock enforces this at deploy; "
            "the L1 CfnGuardrail does not check it at synth."
        )

    blocked_input_messaging = _non_empty_str(
        guardrail_cfg, "guardrail", "blocked_input_messaging"
    )
    if len(blocked_input_messaging) > _GUARDRAIL_MESSAGE_MAX:
        raise ValueError(
            f"guardrail.blocked_input_messaging must be at most {_GUARDRAIL_MESSAGE_MAX} "
            f"characters (got {len(blocked_input_messaging)}). This is the text a student "
            "sees when the screen blocks, so it cannot be silently truncated."
        )

    filters = guardrail_cfg.get("content_filters")
    if not isinstance(filters, list) or not filters:
        raise ValueError(
            "guardrail.content_filters must be a non-empty list. A guardrail with no policy "
            "is billed on every request and screens nothing."
        )

    resolved_filters: List[Dict[str, str]] = []
    seen_types: set = set()
    for index, entry in enumerate(filters):
        if not isinstance(entry, dict):
            raise ValueError(
                f"guardrail.content_filters[{index}] must be a mapping with `type` and "
                f"`input_strength` (got {entry!r})."
            )
        filter_type = _non_empty_str(entry, f"guardrail.content_filters[{index}]", "type")
        if filter_type not in _GUARDRAIL_FILTER_TYPES:
            raise ValueError(
                f"guardrail.content_filters[{index}].type must be one of "
                f"{', '.join(_GUARDRAIL_FILTER_TYPES)} (got {filter_type!r})."
            )
        if filter_type in seen_types:
            raise ValueError(
                f"guardrail.content_filters lists {filter_type} more than once. Bedrock "
                "allows one filter per category, so the duplicate is rejected at deploy."
            )
        seen_types.add(filter_type)

        input_strength = _non_empty_str(
            entry, f"guardrail.content_filters[{index}]", "input_strength"
        )
        if input_strength not in _GUARDRAIL_FILTER_STRENGTHS:
            raise ValueError(
                f"guardrail.content_filters[{index}].input_strength must be one of "
                f"{', '.join(_GUARDRAIL_FILTER_STRENGTHS)} (got {input_strength!r})."
            )
        # Required by the API and meaningless on an input-only screen, so it is set, not read.
        resolved_filters.append(
            {
                "type": filter_type,
                "input_strength": input_strength,
                "output_strength": "NONE",
            }
        )

    return {
        "name": name,
        "blocked_input_messaging": blocked_input_messaging,
        "content_filters": resolved_filters,
    }


def resolve_generation(config: Dict[str, Any]) -> Dict[str, Any]:
    """The `generation` block: the Converse model and its inference knobs."""
    generation_cfg = _require_mapping(config, "generation")
    model_id = _non_empty_str(generation_cfg, "generation", "model_id")
    title_model_id = _non_empty_str(generation_cfg, "generation", "title_model_id")

    head = model_id.split(".", 1)[0]
    is_inference_profile = "." in model_id and head in _INFERENCE_PROFILE_PREFIXES

    # The same resolution, not a copy of the answer: a denied titling call is swallowed, so
    # a second rule for profile-versus-bare-id would fail quietly.
    title_head = title_model_id.split(".", 1)[0]
    title_is_profile = (
        "." in title_model_id and title_head in _INFERENCE_PROFILE_PREFIXES
    )

    temperature = generation_cfg.get("temperature")
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        raise ValueError(
            f"generation.temperature must be a number (got {temperature!r})."
        )
    if not 0 <= temperature <= 1:
        raise ValueError(
            f"generation.temperature must be between 0 and 1 inclusive (got {temperature})."
        )

    return {
        "model_id": model_id,
        # A profile's foundation model is its id minus the geographic prefix.
        "base_model_id": model_id.split(".", 1)[1] if is_inference_profile else None,
        "is_inference_profile": is_inference_profile,
        "title_model_id": title_model_id,
        "title_base_model_id": (
            title_model_id.split(".", 1)[1] if title_is_profile else None
        ),
        "title_is_inference_profile": title_is_profile,
        "max_tokens": _positive_int(generation_cfg, "generation", "max_tokens"),
        "temperature": float(temperature),
    }


def resolve_retrieval(config: Dict[str, Any]) -> Dict[str, Any]:
    """The `retrieval` block: how many chunks to ask the KB for, and the relevance floor."""
    retrieval_cfg = _require_mapping(config, "retrieval")
    number_of_results = _positive_int(retrieval_cfg, "retrieval", "number_of_results")
    if not _NUMBER_OF_RESULTS_MIN <= number_of_results <= _NUMBER_OF_RESULTS_MAX:
        raise ValueError(
            f"retrieval.number_of_results must be between {_NUMBER_OF_RESULTS_MIN} and "
            f"{_NUMBER_OF_RESULTS_MAX} - Bedrock's own range for Retrieve (got "
            f"{number_of_results}). Out of range is a ValidationException per query, not a "
            "failed deploy."
        )

    min_score = retrieval_cfg.get("min_score")
    if isinstance(min_score, bool) or not isinstance(min_score, (int, float)):
        raise ValueError(f"retrieval.min_score must be a number (got {min_score!r}).")
    if not 0 <= min_score <= 1:
        raise ValueError(
            f"retrieval.min_score must be between 0 and 1 inclusive (got {min_score}). "
            "Bedrock relevance scores are normalized to that range, so a value above 1 "
            "silently discards EVERY chunk and the bot answers from nothing."
        )

    return {"number_of_results": number_of_results, "min_score": float(min_score)}


def resolve_request(config: Dict[str, Any]) -> Dict[str, Any]:
    """The `request` block: the server-side query length cap."""
    request_cfg = _require_mapping(config, "request")
    return {
        "max_query_chars": _positive_int(request_cfg, "request", "max_query_chars"),
    }


def resolve_chat(config: Dict[str, Any]) -> Dict[str, Any]:
    """The `chat` block: the agent loop's own limits."""
    chat_cfg = _require_mapping(config, "chat")
    deadline = _positive_int(chat_cfg, "chat", "converse_deadline_seconds")
    title_deadline = _positive_int(chat_cfg, "chat", "title_deadline_seconds")
    title_max_chars = _positive_int(chat_cfg, "chat", "title_max_chars")

    if title_max_chars < _CONVERSATION_TITLE_MIN_CHARS:
        raise ValueError(
            f"chat.title_max_chars ({title_max_chars}) is below "
            f"{_CONVERSATION_TITLE_MIN_CHARS}, which is too short to hold a conversation "
            "name. This is the shape of a dropped digit and it fails silently: every "
            "sidebar row would be truncated to a fragment and every generated title would "
            "be rejected for running past the cap."
        )

    if deadline >= CHAT_LAMBDA_TIMEOUT_SECONDS:
        raise ValueError(
            f"chat.converse_deadline_seconds ({deadline}) must be less than the chat "
            f"Lambda's own timeout ({CHAT_LAMBDA_TIMEOUT_SECONDS}s). A deadline at or past "
            "the timeout never fires: the function is killed mid-Converse instead, which "
            "bills the invocation and returns a gateway 504 with no answer at all. Leave "
            "room for the card shaping and serialisation that run after the loop."
        )

    # Sequential inside one invocation, so they have to fit under the timeout together. An
    # oversized pair does not fail at runtime, it just means titling never gets a turn.
    if deadline + title_deadline >= CHAT_LAMBDA_TIMEOUT_SECONDS:
        raise ValueError(
            f"chat.converse_deadline_seconds ({deadline}) plus "
            f"chat.title_deadline_seconds ({title_deadline}) must be less than the chat "
            f"Lambda's own timeout ({CHAT_LAMBDA_TIMEOUT_SECONDS}s). The two budgets run "
            "one after the other in a single invocation, and they still have to leave room "
            "for the card shaping and serialisation that follow."
        )
    return {
        "max_converse_iterations": _positive_int(
            chat_cfg, "chat", "max_converse_iterations"
        ),
        "max_history_messages": _positive_int(chat_cfg, "chat", "max_history_messages"),
        # A zero here is a blank sidebar, which looks like data loss rather than a typo.
        "max_conversations_listed": _positive_int(
            chat_cfg, "chat", "max_conversations_listed"
        ),
        "max_conversation_messages": _positive_int(
            chat_cfg, "chat", "max_conversation_messages"
        ),
        "converse_deadline_seconds": deadline,
        # A zero in either is a feature that silently never works and reads as a bad model.
        "title_max_chars": title_max_chars,
        "title_deadline_seconds": title_deadline,
    }


def resolve_chat_history(config: Dict[str, Any]) -> Dict[str, Any]:
    """The `chat_history` block: the name of the one conversation-history table."""
    history_cfg = _require_mapping(config, "chat_history")
    table_name = _non_empty_str(history_cfg, "chat_history", "table_name")
    if not (_DYNAMODB_TABLE_NAME_MIN <= len(table_name) <= _DYNAMODB_TABLE_NAME_MAX):
        raise ValueError(
            f"chat_history.table_name must be {_DYNAMODB_TABLE_NAME_MIN}-"
            f"{_DYNAMODB_TABLE_NAME_MAX} characters (got {len(table_name)}: {table_name!r})."
        )
    if not _DYNAMODB_TABLE_NAME_RE.match(table_name):
        raise ValueError(
            f"chat_history.table_name is not a valid DynamoDB table name: {table_name!r}. "
            "Allowed: letters, digits, '_', '-' and '.'. DynamoDB rejects anything else at "
            "CreateTable, which fails the deploy rather than the synth."
        )
    return {"table_name": table_name}


def resolve_cards(config: Dict[str, Any]) -> Dict[str, Any]:
    """The `cards` block: the model-emitted card contract's length and count caps."""
    cards_cfg = _require_mapping(config, "cards")

    max_cards = _positive_int(cards_cfg, "cards", "max_cards")
    max_results = _positive_int(cards_cfg, "cards", "max_retrieval_results")
    if max_results < max_cards:
        raise ValueError(
            f"cards.max_retrieval_results ({max_results}) must be at least cards.max_cards "
            f"({max_cards}). The model cites one retrieved id per card, so it can never "
            "emit more cards than it was shown sources - the extra ceiling is unreachable."
        )

    caps = {}
    for key, floor in (
        ("title_max_chars", _CARD_TITLE_MIN_CHARS),
        ("desc_max_chars", _CARD_DESC_MIN_CHARS),
        ("followup_max_chars", _CARD_FOLLOWUP_MIN_CHARS),
    ):
        value = _positive_int(cards_cfg, "cards", key)
        if value < floor:
            raise ValueError(
                f"cards.{key} ({value}) is below {floor}, which is too small to hold a "
                "usable value. This is the shape of a dropped digit, and it would fail "
                "silently: the prompt would tell the model to write to the short cap and "
                "the server would truncate anything longer."
            )
        caps[key] = value

    return {
        "max_cards": max_cards,
        "max_retrieval_results": max_results,
        **caps,
    }


def resolve_http_api(config: Dict[str, Any]) -> Dict[str, Any]:
    """The `http_api` block: the v1 cost fence."""
    http_cfg = _require_mapping(config, "http_api")
    rate = _positive_int(http_cfg, "http_api", "throttling_rate_limit")
    burst = _positive_int(http_cfg, "http_api", "throttling_burst_limit")
    if burst < rate:
        raise ValueError(
            f"http_api.throttling_burst_limit ({burst}) is below throttling_rate_limit "
            f"({rate}). Burst is the token bucket's CAPACITY, so a burst under the "
            "steady-state rate throttles below the rate it is supposed to allow."
        )
    return {
        "throttling_rate_limit": rate,
        "throttling_burst_limit": burst,
        "chat_reserved_concurrency": _positive_int(
            http_cfg, "http_api", "chat_reserved_concurrency"
        ),
    }


def resolve_rate_limit(config: Dict[str, Any]) -> Optional[int]:
    """The `rate_limit` block: how many messages one user may send per UTC day."""
    rate_cfg = config.get("rate_limit")
    if rate_cfg is None:
        return None
    if not isinstance(rate_cfg, dict):
        raise ValueError("rate_limit must be a mapping.")

    limit = rate_cfg.get("daily_message_limit")
    if limit is None:
        return None
    # Booleans are ints, so `daily_message_limit: true` would be a limit of one a day.
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError(
            f"rate_limit.daily_message_limit must be an integer (got {limit!r}). Use 0 or "
            "remove the block to disable the per-user cap."
        )
    if limit < 0:
        raise ValueError(
            f"rate_limit.daily_message_limit must not be negative (got {limit}). A negative "
            "limit is not 'off' - it refuses every student their first message of the day, "
            "because the counter starts at zero and the check is 'count < limit'. Use 0 to "
            "disable the cap."
        )
    return limit or None


def resolve_cost_model(config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The `cost_model` block: rates x measured usage for the demo cost panel."""
    cost_cfg = config.get("cost_model")
    if not cost_cfg:
        return None
    if not isinstance(cost_cfg, dict):
        raise ValueError("cost_model must be a mapping.")
    if not cost_cfg.get("enabled", False):
        return None

    def _numbers(block_name: str, keys: tuple) -> Dict[str, float]:
        block = cost_cfg.get(block_name)
        if not isinstance(block, dict):
            raise ValueError(
                f"cost_model.{block_name} is missing or is not a mapping. The cost panel "
                "prices every line from these values, so there is no default to fall back on."
            )
        resolved: Dict[str, float] = {}
        for key in keys:
            value = block.get(key)
            # Booleans are ints, and `enabled: true` pasted into a rate is the typo to name.
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"cost_model.{block_name}.{key} must be a number (got {value!r}). "
                    "A missing value reaches the browser as undefined and renders as $NaN."
                )
            if value < 0:
                raise ValueError(
                    f"cost_model.{block_name}.{key} must not be negative (got {value})."
                )
            resolved[key] = float(value)
        return resolved

    rates = _numbers(
        "rates",
        (
            "generation_input_per_1m",
            "generation_output_per_1m",
            "title_input_per_1m",
            "title_output_per_1m",
            "embedding_per_1m",
            "guardrail_content_per_1k_units",
            "vector_query_per_1m",
            "vector_storage_per_gb_month",
            "vector_put_per_gb",
            "s3_storage_per_gb_month",
            "lambda_per_1m_requests",
            "lambda_per_gb_second",
            "api_requests_per_1m",
            "cloudfront_per_1m_requests",
            "logs_ingest_per_gb",
            "dynamodb_write_per_1m",
            "dynamodb_read_per_1m",
        ),
    )
    measured = _numbers(
        "measured",
        (
            "sample_questions",
            "model_calls_avg",
            "context_tokens_per_call_base",
            "context_tokens_per_call_per_prior_turn",
            "output_tokens_avg",
            "retrievals_avg",
            "guardrail_content_units_avg",
            "retrieval_query_tokens",
            "chat_lambda_gb_seconds",
            "chat_dynamodb_writes",
            "chat_dynamodb_reads",
        ),
    )
    baseline = _numbers(
        "baseline",
        (
            "s3_stored_bytes",
            "vector_count",
            "vector_bytes_each",
            "ingest_embedded_tokens",
            "scraper_seconds_per_run",
            "scraper_memory_gb",
            "scrapes_per_month",
            "reindexes_per_month",
            "deploys_per_month",
            "log_gb_per_month",
        ),
    )

    # A question that costs nothing means the block was never filled in, and a zero on the
    # panel reads as a measurement rather than a placeholder.
    if measured["model_calls_avg"] <= 0 or measured["context_tokens_per_call_base"] <= 0:
        raise ValueError(
            "cost_model.measured.model_calls_avg and .context_tokens_per_call_base must be "
            "greater than zero - a question cannot cost nothing. Run eval/measure_usage.py "
            "against the deployed stack and paste its output."
        )

    return {
        "asOf": _non_empty_str(cost_cfg, "cost_model", "as_of"),
        "region": _non_empty_str(cost_cfg, "cost_model", "region"),
        "currency": _non_empty_str(cost_cfg, "cost_model", "currency"),
        "measuredAt": _non_empty_str(
            _require_mapping(cost_cfg, "measured"), "cost_model.measured", "at"
        ),
        "rates": rates,
        "measured": measured,
        "baseline": baseline,
    }


def resolve_streaming(config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The `streaming` block, which nothing builds from any more.

    The WebSocket transport is gone and the app that replaced it pushes every delta,
    so `enabled`, the batching numbers and `output_guardrail` all configure a thing that
    is not built. Left here because removing it is a config-schema change of its own.
    """
    streaming_cfg = config.get("streaming")
    if not streaming_cfg:
        return None
    if not isinstance(streaming_cfg, dict):
        raise ValueError("streaming must be a mapping.")
    if not streaming_cfg.get("enabled", False):
        return None

    def _bounded_int(key: str, minimum: int, maximum: int) -> int:
        value = streaming_cfg.get(key)
        # Booleans are ints, so `delta_min_chars: true` would resolve to 1.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(
                f"streaming.{key} must be an integer (got {value!r})."
            )
        if not minimum <= value <= maximum:
            raise ValueError(
                f"streaming.{key} must be between {minimum} and {maximum} (got {value})."
            )
        return value

    output_guardrail = streaming_cfg.get("output_guardrail", False)
    if not isinstance(output_guardrail, bool):
        raise ValueError(
            f"streaming.output_guardrail must be true or false (got {output_guardrail!r})."
        )

    return {
        # A floor of 1 is one message per character; the ceiling keeps text flowing.
        "delta_min_chars": _bounded_int("delta_min_chars", 16, 2000),
        # Without it the tail of a reply, under the batch size, would never be sent at all.
        "delta_max_delay_ms": _bounded_int("delta_max_delay_ms", 50, 5000),
        "output_guardrail": output_guardrail,
    }


# Fixed rather than configurable: a deployment that could rename the kind could address a
# student's message to a crisis hotline's row.
_ESCALATION_CONTACT_KIND = "escalation"


def _escalation_recipient(contact_id: str) -> str:
    """The mailbox on one `escalation` row of data/contacts.csv, or a ValueError naming it."""
    path = data_file_path("contacts.csv")
    if not path.exists():
        raise ValueError(
            f"escalation.contact names a row in {path}, which does not exist. Every SJSU "
            "fact this app states lives in the repo-root data/ directory; see its README."
        )

    # Local, for the reason resolve_seed_pages does the same.
    import csv

    with open(path, newline="", encoding="utf-8") as fh:
        rows = [row for row in csv.DictReader(fh) if row.get("id", "").strip() == contact_id]
    if not rows:
        raise ValueError(
            f"escalation.contact is {contact_id!r}, and no row in {path.name} has that id. "
            "Point it at a row that exists, or leave it empty to turn the escalate-to-human "
            "path off."
        )
    if len(rows) > 1:
        raise ValueError(
            f"{path.name} has {len(rows)} rows with id {contact_id!r}. One id, one row - a "
            "second one would quietly win, and a student's message would go to whichever "
            "came last."
        )
    row = rows[0]
    kind = (row.get("kind") or "").strip()
    if kind != _ESCALATION_CONTACT_KIND:
        raise ValueError(
            f"escalation.contact is {contact_id!r}, whose kind is {kind!r} rather than "
            f"{_ESCALATION_CONTACT_KIND!r}. Only an escalation row is a place to send a "
            "student's own words; a safety row is a crisis line, and a cares row may be a "
            "link rather than a mailbox."
        )
    recipient = (row.get("detail") or "").strip()
    if not recipient:
        raise ValueError(
            f"{path.name} row {contact_id!r} has an empty `detail`, and that column is the "
            "mailbox an escalation draft is addressed to."
        )
    return recipient


def resolve_escalation(config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The `escalation` block: the email draft a turn can offer to send to a human."""
    escalation_cfg = config.get("escalation")
    if not escalation_cfg:
        return None
    if not isinstance(escalation_cfg, dict):
        raise ValueError("escalation must be a mapping.")

    contact_id = escalation_cfg.get("contact")
    if contact_id is None or (isinstance(contact_id, str) and not contact_id.strip()):
        return None
    if not isinstance(contact_id, str):
        raise ValueError(
            f"escalation.contact must name a row in data/contacts.csv (got {contact_id!r}). "
            "Leave it out or empty to turn the escalate-to-human path off."
        )
    contact_id = contact_id.strip()
    recipient = _escalation_recipient(contact_id)
    local, separator, domain = recipient.partition("@")
    if not separator or not local or not domain or any(c.isspace() for c in recipient):
        raise ValueError(
            f"data/contacts.csv row {contact_id!r} has `detail` = {recipient!r}, which is "
            "not one plain email address: a local part, an @, and a domain, with no display "
            "name and no second address. It is put straight into a mailto the student's "
            "mail client has to open."
        )
    if "," in recipient or ";" in recipient:
        raise ValueError(
            f"data/contacts.csv row {contact_id!r} must name ONE mailbox (got "
            f"{recipient!r}). A draft is addressed to one office; a list here would send "
            "every student's message to everybody on it."
        )

    subject = escalation_cfg.get("subject")
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError(
            "escalation.subject must be a non-empty string: it is the subject line on "
            "every draft, and staff triage on it before they read anything else."
        )

    max_chars = escalation_cfg.get("max_chars")
    if isinstance(max_chars, bool) or not isinstance(max_chars, int):
        raise ValueError(
            f"escalation.max_chars must be an integer (got {max_chars!r})."
        )
    if max_chars < _ESCALATION_MIN_CHARS:
        raise ValueError(
            f"escalation.max_chars ({max_chars}) is below {_ESCALATION_MIN_CHARS}, which "
            "is too small to hold a message to a person. Unlike the card caps, going over "
            "this one DROPS the offer rather than shortening it, so a dropped digit here "
            "turns the feature off silently instead of truncating anything."
        )

    return {
        "recipient": recipient,
        "subject": subject.strip(),
        "max_chars": max_chars,
    }


def resolve_okta(config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The `okta` block: the SAML identity provider federated into the chat user pool."""
    okta_cfg = config.get("okta")
    if not okta_cfg:
        return None
    if not isinstance(okta_cfg, dict):
        raise ValueError("okta must be a mapping.")

    metadata_url = okta_cfg.get("metadata_url")
    if metadata_url is None or (isinstance(metadata_url, str) and not metadata_url.strip()):
        return None
    if not isinstance(metadata_url, str):
        raise ValueError(
            f"okta.metadata_url must be a string URL (got {metadata_url!r}). Leave it out "
            "or empty to run on local accounts with no identity provider."
        )
    metadata_url = metadata_url.strip()
    # This document carries the signing certificate every assertion is verified against, so
    # over http the fetch is a forgeable trust anchor for the whole federation.
    if not metadata_url.startswith("https://"):
        raise ValueError(
            f"okta.metadata_url must be an https:// URL (got {metadata_url!r}). Cognito "
            "fetches this document itself and trusts the signing certificate in it, so "
            "plain http would be a forgeable trust anchor - and Cognito rejects it anyway."
        )

    # Absent means the default; empty means somebody half-looked, and an unmapped email is
    # a blank sidebar label rather than a failure.
    email_attribute = okta_cfg.get("email_attribute", _DEFAULT_OKTA_EMAIL_ATTRIBUTE)
    if not isinstance(email_attribute, str) or not email_attribute.strip():
        raise ValueError(
            f"okta.email_attribute must be a non-empty string (got {email_attribute!r}); "
            f"omit the key entirely to accept the default {_DEFAULT_OKTA_EMAIL_ATTRIBUTE!r}."
        )

    return {
        # Returned so the stack takes it from the same block as the rest of the provider.
        "provider_name": OKTA_PROVIDER_NAME,
        "metadata_url": metadata_url,
        "email_attribute": email_attribute.strip(),
    }


def validate_config(config: Dict[str, Any]) -> None:
    """Run every validator, discarding the results."""
    resolve_knowledge_base(config)
    resolve_vector_store(config)
    resolve_chunking(config)
    resolve_data_source_name(config)
    resolve_scraper(config)
    resolve_seed_pages(config)
    resolve_cors_allow_origins(config)
    resolve_http_api(config)
    resolve_guardrail(config)
    resolve_generation(config)
    resolve_retrieval(config)
    resolve_request(config)
    resolve_chat(config)
    resolve_chat_history(config)
    resolve_cards(config)
    # None when the cap is off, but a bad limit still fails here rather than deploying a
    # stack that refuses every student's first question.
    resolve_rate_limit(config)
    # None when the panel is off, but a bad rate still fails here rather than reaching a
    # browser as $NaN.
    resolve_cost_model(config)
    # None with no metadata URL, but a URL that is set is checked here rather than at
    # CreateIdentityProvider, which is a mid-update deploy failure.
    resolve_okta(config)
    # Still called although nothing builds from it, so a malformed block is a synth failure
    # rather than a silent one.
    resolve_streaming(config)
    # None with no recipient, but a malformed address still fails here rather than reaching
    # a student as a mail client that refuses to open.
    resolve_escalation(config)


def resolve_cors_allow_origins(config: Dict[str, Any]) -> List[str]:
    """The browser origin allowlist for the HTTP API, from config's `cors.allow_origins`."""
    origins = (config.get("cors") or {}).get("allow_origins")
    if not origins:
        raise ValueError(
            "config.yaml is missing cors.allow_origins - set the browser origin allowlist "
            "for the HTTP API (e.g. https://www.sjsu.edu)."
        )
    if not isinstance(origins, list) or not all(isinstance(o, str) for o in origins):
        raise ValueError("cors.allow_origins must be a list of origin strings.")
    if any(o.strip() == "*" for o in origins):
        raise ValueError(
            "cors.allow_origins must not contain '*' - list the exact origins allowed to "
            "call this endpoint from a browser."
        )
    return list(origins)
