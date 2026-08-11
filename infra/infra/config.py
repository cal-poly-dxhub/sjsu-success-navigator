"""Load and VALIDATE the repo-root config.yaml for the CDK app.

config.yaml is the single source of truth for changeable knobs. This module resolves it
relative to __file__ so it works no matter what the current working directory is.

Layout: this file is <repo>/infra/infra/config.py, so the repo root is parents[2] and
config.yaml sits directly under it.

WHY VALIDATORS EXIST AT ALL, given the stack synthesizes fine without them: the stack is
L1 `Cfn*` all the way down, and L1 constructs do NOT enforce CloudFormation's property
constraints at synth. A name that violates a service's pattern, a dimension the embedding
model does not emit, a wildcard CORS origin - every one of those synthesizes clean and
first fails at deploy, some of them AFTER creating half the stack. These functions move
that enforcement to synth, where it costs nothing to be wrong.

NAMING CONVENTION, which every later stack section inherits: no global name is written in
the stack. Bucket, knowledge base, index, function and schedule names all derive from this
file, and the derivation lives HERE rather than inline in the stack, so there is exactly
one place a name is spelled. The consequence, accepted deliberately (gav does the same):
config.yaml carries literal names, so standing the stack up a SECOND time in one account
means editing config.yaml first. Two deploys of an unedited config collide on the vector
bucket name and the KB name. That is a config edit, not a code change.
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = _REPO_ROOT / "config.yaml"

# Bedrock's name pattern for AWS::Bedrock::KnowledgeBase.Name and
# AWS::Bedrock::DataSource.Name, verbatim from the CloudFormation resource reference
# (verified 2026-08-05). Read it as up to 100 groups of "one alphanumeric, then AT MOST ONE
# - or _". So it rejects a LEADING separator and any doubled separator ("--"), it ALLOWS a
# trailing one, and the group count caps the name at 200 characters. The data source name is
# BUILT by folding the chunk config into the KB name (see resolve_data_source_name), so it is
# the likelier of the two to trip this - which is why it is checked here, at synth, rather
# than discovered when the deploy fails.
_BEDROCK_NAME_RE = re.compile(r"^([0-9a-zA-Z][_-]?){1,100}$")

# S3 Vectors bucket + index names: 3-63 chars, and the charset is the one the service's
# own ARN pattern admits - `[a-z0-9][a-z0-9-.]{1,61}[a-z0-9]` (verified against the
# CreateVectorBucket / CreateIndex API reference, 2026-08-05). Lowercase only; no
# underscores, which is the difference from the Bedrock pattern above and an easy way to
# author a config that half-validates.
_S3_VECTORS_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_S3_VECTORS_NAME_MIN = 3
_S3_VECTORS_NAME_MAX = 63

# S3 Vectors caps non-filterable metadata keys at 10 PER INDEX, and the setting is
# IMMUTABLE after the index is created - so exceeding it is not a deploy that fails and
# gets fixed, it is an index that has to be replaced (taking the KB with it).
_MAX_NON_FILTERABLE_KEYS = 10

# Vector dimensions the embedding model actually emits. Titan Text Embeddings v2 supports
# 1024 (default), 512 and 256 (verified against the Bedrock model reference, 2026-08-05).
# The index dimension MUST equal the model's output or every ingestion fails, and the
# index dimension is immutable, so a mismatch is another replacement rather than a fix.
_EMBEDDING_MODEL_DIMENSIONS = {
    "amazon.titan-embed-text-v2:0": (1024, 512, 256),
}
# S3 Vectors' own hard range, for an embedding model not in the table above.
_DIMENSION_MIN = 1
_DIMENSION_MAX = 4096

# Bedrock's chunking strategies. v1 uses FIXED_SIZE (gav's baseline); the others are
# listed so an unsupported value is named as unsupported rather than silently passed to a
# CfnDataSource that will reject it at deploy.
_CHUNKING_STRATEGIES = ("FIXED_SIZE", "NONE", "HIERARCHICAL", "SEMANTIC")

# Columns url-list.csv must carry. `section` is load-bearing beyond provenance: it reaches
# the metadata sidecars, and the card builder keys its deprioritization and its follow-up
# buttons off it. A blank section degrades SILENTLY (a card just stops being deprioritized
# and loses its tailored follow-up), which is why it is required here rather than defaulted.
_SEED_COLUMNS = ("url", "section", "title")

# The chat Lambda's own timeout, in seconds. NOT a config knob, and not arbitrary: an HTTP
# API integration's timeoutInMillis maxes at 30,000 ms and cannot be raised by a quota
# request, so 29 is the ceiling minus one - the function's own timeout wins, and the failure
# is diagnosable in its logs rather than only as a gateway 504. It lives here, beside the
# validator that checks chat.converse_deadline_seconds against it, so the deadline and the
# timeout it must sit under cannot drift apart in two files.
CHAT_LAMBDA_TIMEOUT_SECONDS = 29

# Bedrock guardrail constraints, verified against the CreateGuardrail /
# GuardrailContentFilterConfig API reference (2026-08-05). Every one of these is enforced by
# the service and NOT by the L1 CfnGuardrail, so without these checks a bad value first
# surfaces as a failed change set:
#   - name:                  1-50 chars matching [0-9a-zA-Z-_]+ (note: no dots, unlike the
#                            S3 Vectors pattern, and shorter than the Bedrock KB pattern)
#   - blockedInputMessaging: 1-500 chars, and it is REQUIRED
#   - filter type:           one of the six harmful categories
#   - filter strength:       NONE | LOW | MEDIUM | HIGH
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

# Bedrock's own range for Retrieve's numberOfResults (verified against
# KnowledgeBaseVectorSearchConfiguration, 2026-08-05). Out of range is a runtime
# ValidationException on every single query - not a deploy failure, which makes it worse:
# the stack comes up clean and every request 502s.
_NUMBER_OF_RESULTS_MIN = 1
_NUMBER_OF_RESULTS_MAX = 100

# Floors for the card field caps. Not design minimums - they are dropped-digit detectors,
# set low enough that any deliberate value clears them and high enough that 60-for-600,
# 9-for-90 or 12-for-120 does not. See resolve_cards.
_CARD_TITLE_MIN_CHARS = 20
_CARD_DESC_MIN_CHARS = 100
_CARD_FOLLOWUP_MIN_CHARS = 20

# DynamoDB table names: 3-255 characters of letters, digits, '_', '-' and '.' (verified
# against the CreateTable API reference, 2026-08-11). Wider than the S3 Vectors charset -
# uppercase and underscores are both legal here - which is exactly why it gets its own
# pattern rather than borrowing one of the two above.
#
# A bad name is a deploy failure, not a synth failure, and the table it fails on is the one
# holding conversation history: CloudFormation REPLACES a table to change its name, so a
# rename discovered after the first deploy takes the history with it.
_DYNAMODB_TABLE_NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")
_DYNAMODB_TABLE_NAME_MIN = 3
_DYNAMODB_TABLE_NAME_MAX = 255

# Geographic prefixes that mark a model id as a CROSS-REGION INFERENCE PROFILE rather than a
# bare on-demand foundation model. The two need different IAM shapes (see the chat Lambda's
# role in infra_stack.py), so the distinction is resolved here, once.
_INFERENCE_PROFILE_PREFIXES = ("us", "eu", "apac", "us-gov")


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
    """A positive integer knob, or a synth-time error. `bool` is rejected explicitly
    because it is an int subclass in Python, so `True` would otherwise pass as 1."""
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
    """The `knowledge_base` block: KB name, embedding model, vector dimension.

    The embedding model and its 1024 dimensions are INHERITED FROM GAV, not chosen here
    (docs/synthesis.md, "Decisions (2026-08-05)") - gav's exact vector-store shape, which
    is what makes its KB section a copy rather than a re-derivation.

    The dimension is cross-checked against what the embedding model actually emits: they
    must be equal or every ingestion fails with a ValidationException, and since the index
    dimension is immutable the fix is an index replacement (which takes the KB with it).
    """
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
        # An embedding model this file has no table for: fall back to S3 Vectors' own range
        # rather than pretending to know the model's output sizes.
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
    """The `vector_store` block: S3 Vectors bucket/index names and index shape.

    `non_filterable_metadata_keys` is the trap worth validating: Bedrock's internal
    metadata keys are filterable by default and blow S3 Vectors' filterable-metadata limit,
    failing EVERY ingestion, and the setting is immutable once the index exists. Capped at
    10 keys per index by the service.
    """
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
    """The `chunking` block, plus the name suffix that encodes it.

    FIXED_SIZE 600 tokens / 20% overlap is INHERITED FROM GAV as the starting baseline
    (docs/synthesis.md), to be retuned with the eval once an account exists - not a value
    chosen against this corpus.

    CHANGING CHUNKING IS A DATA-SOURCE REPLACEMENT, NOT A CONFIG TWEAK. Bedrock chunking is
    immutable, so any edit here makes CloudFormation replace the data source - and it
    replaces by creating the new one BEFORE deleting the old, which collides on a fixed name
    and kills the deploy mid-update with `409 AlreadyExists`. `name_suffix` (gav's trick) is
    what keeps the replacement name distinct; resolve_data_source_name folds it in. Two
    further consequences of a chunking edit, neither visible at synth: the replacement data
    source starts EMPTY, and `cdk deploy` does not refill it (the install trigger only
    re-fires on scraper change), so ingestion needs a manual kick afterward.
    """
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
        # e.g. "fixedsize-600t20p". Underscores are dropped rather than kept because this
        # rides inside a Bedrock name, and the pattern allows at most one separator between
        # alphanumeric groups.
        "name_suffix": f"{strategy.lower().replace('_', '')}-{max_tokens}t{overlap}p",
    }


def resolve_data_source_name(config: Dict[str, Any]) -> str:
    """The KB's S3 data source name, with the chunking configuration folded in.

    THE NAME CARRIES THE CHUNKING CONFIG ON PURPOSE - see resolve_chunking for why a fixed
    name turns a chunking edit into a failed deploy. Built here rather than in the stack so
    the fold and the pattern check live in one place: the fold is what makes the name long
    and separator-heavy enough to violate Bedrock's name pattern, so the two belong together.
    """
    kb_name = resolve_knowledge_base(config)["name"]
    suffix = resolve_chunking(config)["name_suffix"]
    return _check_bedrock_name(f"{kb_name}-s3-{suffix}", "the derived data source name")


def resolve_scraper(config: Dict[str, Any]) -> Dict[str, Any]:
    """The `scraper` block: single daily schedule, HTTP knobs, and the crawl-list filename.

    ONE schedule, no tiers (docs/synthesis.md): 203 curated pages are cheap enough to sweep
    daily, and change gating means an unchanged day pays only the Lambda run. A missing cron
    is validated rather than defaulted because its failure mode is a Lambda that nothing ever
    invokes - a corpus that silently stops refreshing, visible only as stale answers later.
    """
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


def seed_list_path(config: Dict[str, Any]) -> Path:
    """Absolute path to the curated crawl list named by `scraper.url_list_file`.

    Resolved against the REPO ROOT, not the current working directory: synth runs from
    infra/ and the list lives at the root (a decision - it is content, not infra).
    """
    return _REPO_ROOT / resolve_scraper(config)["url_list_file"]


def resolve_seed_pages(config: Dict[str, Any]) -> List[Dict[str, str]]:
    """The curated crawl list as `[{"url", "section", "title"}]`, in file order.

    This is the corpus as CONFIGURATION defines it - what a run fetches, and what the
    stale-object prune keeps in the KB source bucket. Read and checked at synth so a broken
    list fails the build rather than deploying a scraper with nothing to do.

    Every check here guards a failure that is otherwise SILENT:
      - a missing or empty file  -> a scraper that fetches nothing and prunes the entire
        knowledge base on its first run
      - a missing `section`      -> cards lose their deprioritization and their tailored
        follow-up button, with nothing in the logs to say why
      - a duplicate URL          -> the page is fetched twice per run for one S3 object
    """
    path = seed_list_path(config)
    if not path.exists():
        raise ValueError(
            f"the crawl list named by scraper.url_list_file does not exist: {path}. It is "
            "the scraper's only source of URLs, so a missing file means a run that fetches "
            "nothing - and prunes everything."
        )

    # Imported here rather than at module scope: csv is needed by this one function, and
    # keeping the import local mirrors how narrowly the crawl list is read.
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
    """The `guardrail` block: ONE guardrail, PROMPT_ATTACK on input only.

    The shape is deliberately narrow (docs/synthesis.md): the deterministic safety intercept
    runs FIRST, then this screens the bare query, then the loop starts. A content filter for
    anything but prompt injection would pre-empt the intercept and the system prompt's crisis
    handling, so the config is allowed to name any of Bedrock's six categories but the
    project only configures the one that is an attack on the prompt itself.

    Output strength is NOT a config knob and is forced to NONE below: this guardrail is only
    ever applied with source=INPUT, so a non-zero output strength would be a claim the
    deployment does not make.
    """
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
        # outputStrength is REQUIRED by the API but meaningless here (input-only screen), so
        # it is set rather than read. NONE is also what Bedrock requires for PROMPT_ATTACK.
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
    """The `generation` block: the Converse model and its inference knobs.

    `is_inference_profile` is resolved HERE rather than in the stack because it decides the
    IAM shape: a geographic-prefixed id ("us.anthropic...") is a cross-region inference
    profile, which needs InvokeModel on the profile ARN PLUS the underlying foundation-model
    ARNs, while a bare id needs one foundation-model ARN. Getting it wrong is an
    AccessDeniedException on every generation - a stack that deploys clean and never answers.
    """
    generation_cfg = _require_mapping(config, "generation")
    model_id = _non_empty_str(generation_cfg, "generation", "model_id")

    head = model_id.split(".", 1)[0]
    is_inference_profile = "." in model_id and head in _INFERENCE_PROFILE_PREFIXES

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
        # The foundation-model id behind a profile is the id minus its geographic prefix.
        # None for a bare id, where the model id already IS the foundation model.
        "base_model_id": model_id.split(".", 1)[1] if is_inference_profile else None,
        "is_inference_profile": is_inference_profile,
        "max_tokens": _positive_int(generation_cfg, "generation", "max_tokens"),
        "temperature": float(temperature),
    }


def resolve_retrieval(config: Dict[str, Any]) -> Dict[str, Any]:
    """The `retrieval` block: how many chunks to ask the KB for, and the relevance floor.

    Both are runtime knobs rather than deploy-time ones, which is exactly why they are
    validated here: a numberOfResults out of Bedrock's 1-100 range does not fail the deploy,
    it fails every query afterwards.
    """
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
    """The `request` block: the server-side query length cap.

    The client's maxlength is advisory UX; this is the real control. The platform limits
    (API Gateway 10 MB, Lambda 6 MB) are far too high to protect a paid Bedrock call.
    """
    request_cfg = _require_mapping(config, "request")
    return {
        "max_query_chars": _positive_int(request_cfg, "request", "max_query_chars"),
    }


def resolve_chat(config: Dict[str, Any]) -> Dict[str, Any]:
    """The `chat` block: the agent loop's own limits.

    `max_converse_iterations` is the loop's safety cap. Camp capped at 6 with a literal
    default and no log line when it was reached, so a request that burned six Converse calls
    and fell through to the non-agentic fallback was indistinguishable from a normal answer.
    It is config here, and the handler logs when it hits (docs/build-plan.md).

    `converse_deadline_seconds` is the OTHER half of that cap, and the one that actually
    bounds a request: iterations bound how many model calls happen, not how long they take.
    It must leave room under the function's own timeout for the work that happens after the
    loop returns, so it is validated against CHAT_LAMBDA_TIMEOUT_SECONDS rather than merely
    being positive - a deadline at or past the timeout is the same as having none.
    """
    chat_cfg = _require_mapping(config, "chat")
    deadline = _positive_int(chat_cfg, "chat", "converse_deadline_seconds")
    if deadline >= CHAT_LAMBDA_TIMEOUT_SECONDS:
        raise ValueError(
            f"chat.converse_deadline_seconds ({deadline}) must be less than the chat "
            f"Lambda's own timeout ({CHAT_LAMBDA_TIMEOUT_SECONDS}s). A deadline at or past "
            "the timeout never fires: the function is killed mid-Converse instead, which "
            "bills the invocation and returns a gateway 504 with no answer at all. Leave "
            "room for the card shaping and serialisation that run after the loop."
        )
    return {
        "max_converse_iterations": _positive_int(
            chat_cfg, "chat", "max_converse_iterations"
        ),
        "max_history_messages": _positive_int(chat_cfg, "chat", "max_history_messages"),
        "converse_deadline_seconds": deadline,
    }


def resolve_chat_history(config: Dict[str, Any]) -> Dict[str, Any]:
    """The `chat_history` block: the name of the one conversation-history table.

    ONE table for everything - conversation headers and messages both, partitioned on the
    Cognito `sub` and separated by a `CONV#`/`MSG#` sort-key prefix (docs/accounts-and-storage.md,
    Storage). Partitioning on the user is a SECURITY property rather than a modelling
    convenience: the Lambda derives the partition key from the JWT, so there is no filter
    that can be forgotten.

    The name is the only value that comes from config. The key schema, the billing mode and
    the TTL attribute are properties the application code must agree with byte for byte, and
    none of them can be changed on a live table without either a replacement (key schema) or
    a disable/enable cycle (TTL) - so they are stated once in the stack, where the comment
    explaining each of them can sit beside it, rather than being knobs that read as tunable.

    Validated here rather than left to the deploy for the usual reason: this is an L1-shaped
    property that CloudFormation checks on CreateTable, and a rejected CreateTable is a
    failed deploy partway through a stack update.
    """
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
    """The `cards` block: the model-emitted card contract's length and count caps.

    These are validated at synth for the same reason the retrieval knobs are - none of them
    can fail a deploy, and all of them change what a student sees on every answer afterwards.

    The one relationship worth checking is `max_retrieval_results` against `max_cards`. The
    model cites a source by the integer id it was handed, so it cannot produce more distinct
    cards than it was shown sources: a ceiling above the number of results is a ceiling that
    can never be reached, which reads like a decision and is really an arithmetic mistake.

    The character floors exist to catch a dropped digit. `desc_max_chars: 60` is a plausible
    typo for 600 and would not fail anything - it would just truncate every description to a
    fragment, on every answer, and the prompt would faithfully instruct the model to write
    them that way.
    """
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
    """The `http_api` block: the v1 cost fence.

    With no billing alarm until v2, these three numbers plus the Cognito gate are the only
    thing between a public endpoint and an unbounded Bedrock bill. They are validated
    rather than defaulted for that reason - a missing throttle is not "unlimited by
    choice", it is an unfenced paid endpoint.

    They bound DIFFERENT things, which is why all three exist:
      - rate/burst bound how many invocations START per second.
      - reserved concurrency bounds how many run AT ONCE, which rate alone does not: at
        10 rps against a 29-second budget, ~290 invocations can be in flight.
    Neither bounds a single runaway invocation; the loop's deadline and iteration cap do.
    """
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


def validate_config(config: Dict[str, Any]) -> None:
    """Run every validator, discarding the results.

    The stack calls this ONCE at the top of __init__, before any construct exists. Without
    it a validator only fires when the section that happens to consume it has been written,
    so a config error in a not-yet-built section (a bad guardrail name, a CORS wildcard)
    would sit undetected until that section lands. Calling everything up front makes `cdk
    synth` the gate for the WHOLE file from this commit forward, not just the built part.

    Cheap enough to be unconditional: YAML already parsed, plus one pass over a 203-row CSV.
    """
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


def resolve_cors_allow_origins(config: Dict[str, Any]) -> List[str]:
    """The browser origin allowlist for the HTTP API, from config's `cors.allow_origins`.

    There is no safe default here, so a missing/empty list is a synth-time error rather than
    a silent fallback. A wildcard is rejected outright: the endpoint fans out to paid Bedrock
    calls, and "*" would let any site drive it from its visitors' browsers.

    CORS is browser-enforced only and is NOT a security boundary (curl ignores it) - stage
    throttling and the Cognito gate are the actual cost caps. Entries are matched as EXACT
    full origins, so each is scheme + host with no trailing slash and no path.
    """
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
