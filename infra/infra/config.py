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
# The floor on chat.title_max_chars, and the same dropped-digit guard the card caps carry:
# 8 is a plausible typo for 80 and would fail silently, truncating every conversation name
# in the sidebar to a fragment while rejecting every title the model wrote for being too
# long. Twenty characters is roughly three words, which is the shortest a title can be and
# still name anything.
_CONVERSATION_TITLE_MIN_CHARS = 20

_CARD_TITLE_MIN_CHARS = 20
_CARD_DESC_MIN_CHARS = 100
_CARD_FOLLOWUP_MIN_CHARS = 20

# The floor on escalation.max_chars, and the same dropped-digit detector one size up: 120
# is a plausible typo for 1200 and would take the feature off the air rather than shorten
# anything, because this is the one cap whose violation drops the offer. Three hundred
# characters is about a paragraph, which is the shortest a message to a person can be and
# still say what happened. See resolve_escalation.
_ESCALATION_MIN_CHARS = 300

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

# THE SAML PROVIDER'S NAME, AND THE ONE VALUE IN THIS FILE THAT IS NOT A KNOB. It is named
# for the provider's ROLE in this pool, never for whose Okta org is behind it, so the same
# name serves the rehearsal org today and SJSU's tenant later with only the metadata URL
# changing.
#
# RENAMING IT IS NOT A RENAME, IT IS A MIGRATION. A federated user's Cognito username is
# `<providerName>_<nameid>`, so a different name mints a NEW user - new `sub`, which is the
# DynamoDB partition key (docs/accounts-and-storage.md, Storage) - and orphans every
# conversation the old identity wrote. There is no CloudFormation update path either:
# ProviderName is the identity provider's physical id, so an edit REPLACES the resource.
#
# It lives here rather than inline in the stack for the same reason every other name does
# (see the naming convention at the top of this file): one place it is spelled. It is a
# constant rather than a config key because it is the one thing that must NOT differ between
# the rehearsal org and SJSU's - a config key is an invitation to set it to "SJSU".
OKTA_PROVIDER_NAME = "Okta"

# The Okta-side attribute this pool maps to `email`. A DEFAULT rather than a required key:
# `email` is the usual name, and an org that spells it otherwise (a SAML namespace URI, or
# `emailAddress`) sets `okta.email_attribute` instead of editing code. Cognito takes the
# USERNAME from NameID, so username is deliberately not mappable and not mapped.
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
    title_model_id = _non_empty_str(generation_cfg, "generation", "title_model_id")

    head = model_id.split(".", 1)[0]
    is_inference_profile = "." in model_id and head in _INFERENCE_PROFILE_PREFIXES

    # The titling model gets the SAME resolution rather than a copy of the answer: it is
    # invoked the same way (Converse) so it needs the same IAM shape, and a second rule for
    # deciding profile-versus-bare-id would be a second thing to get wrong. Getting it wrong
    # here is quieter than for the generation model - a denied titling call is swallowed and
    # every conversation keeps its fallback title - which is exactly why it is resolved
    # here, where the branch is visible, rather than left to be discovered from a log.
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
        # The foundation-model id behind a profile is the id minus its geographic prefix.
        # None for a bare id, where the model id already IS the foundation model.
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

    # The two budgets are SEQUENTIAL inside one invocation - the loop runs, then the title
    # call - so they have to fit under the function's timeout together. Checked here rather
    # than left to runtime because the runtime degradation is invisible: the handler takes
    # the minimum with Lambda's remaining time, so an oversized pair does not fail, it just
    # means titling never gets a turn and every conversation quietly keeps its fallback
    # name. That reads as a bad titling model rather than a config error.
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
        # The read endpoints' caps. Validated the same way as the loop caps and for the
        # same reason - a zero or a negative here is a sidebar that lists nothing and a
        # conversation that opens blank, which looks like data loss rather than a typo in
        # config.yaml.
        "max_conversations_listed": _positive_int(
            chat_cfg, "chat", "max_conversations_listed"
        ),
        "max_conversation_messages": _positive_int(
            chat_cfg, "chat", "max_conversation_messages"
        ),
        "converse_deadline_seconds": deadline,
        # The conversation title's length cap and the titling call's own wall-clock budget.
        # Positive-checked for the same reason as everything above: neither can fail a
        # deploy, and a zero or negative in either is a feature that silently never works -
        # a cap of zero rejects every title the model writes AND truncates the fallback to
        # nothing, and a deadline of zero means the call is never attempted at all. Both
        # would look exactly like "the model is bad at titles".
        "title_max_chars": title_max_chars,
        "title_deadline_seconds": title_deadline,
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


def resolve_rate_limit(config: Dict[str, Any]) -> Optional[int]:
    """The `rate_limit` block: how many messages one user may send per UTC day.

    Returns None when the block is absent or the limit is 0, and the stack then omits the
    environment variable entirely - the same gate `resolve_cost_model` below uses, for the
    same reason: "off" should be one state with one spelling, not a zero the application
    has to be trusted to interpret. app/settings.py reads an unset variable as disabled, so
    the two layers agree without either of them carrying a second default.

    OFF IS NOT AN ERROR. A stack that wants no per-user cap is a decision (a closed pilot
    where every account is known), so a missing block does not fail synth.

    WHAT IT BOUNDS, stated because the other three numbers in this file sound like they
    already cover it: http_api's throttle bounds invocations STARTED per second and its
    reserved concurrency bounds invocations running AT ONCE, both across everybody. Neither
    can tell two students apart, so neither bounds what one account spends. This is the
    only number here that does.

    NEGATIVE IS AN ERROR rather than another spelling of off. A negative limit would make
    the condition `count < :limit` false on the very first message of the day, so every
    student would be refused their first question - and it would read as a disabled feature
    in config.yaml rather than as the total outage it is.
    """
    rate_cfg = config.get("rate_limit")
    if rate_cfg is None:
        return None
    if not isinstance(rate_cfg, dict):
        raise ValueError("rate_limit must be a mapping.")

    limit = rate_cfg.get("daily_message_limit")
    if limit is None:
        return None
    # Booleans are ints in Python, so `daily_message_limit: true` would otherwise resolve to
    # a limit of exactly one message per day.
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
    """The `cost_model` block: rates x measured usage for the demo cost panel.

    Returns None when the block is absent or `enabled` is false, and the stack then omits
    `costModel` from config.json entirely - which is the whole gate. The frontend renders
    the cost section inside its settings panel only when the key is present, so turning the
    breakdown off is a config edit rather than a code change. The settings panel around it
    is not gated by this and never was meant to be - it holds the language picker, which is
    for the student. That matters for the Okta federation landing next: it
    provisions any SJSU student just in time, and this surface must not show them what the
    system costs to run.

    OFF IS NOT AN ERROR, so a disabled block is not validated past `enabled`. A half-filled
    block somebody is still measuring should not fail `cdk synth` while the panel is off.

    EVERY NUMBER IS REQUIRED WHEN IT IS ON, with no defaults anywhere. A missing rate would
    otherwise reach the browser as `undefined`, and the arithmetic there would render "$NaN"
    on a page whose entire purpose is being checkable. Failing at synth names the key.
    """
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
            # Booleans are ints in Python and would sail through an isinstance check, which
            # is exactly the kind of typo (`enabled: true` pasted into a rate) worth naming.
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

    # A question that costs nothing means the measured block was never filled in, and the
    # panel would confidently show $0.00 for a system that bills real Bedrock tokens. That
    # is the one wrong number worth failing synth over: a zero reads as a measurement, not
    # as a placeholder.
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
    """The `streaming` block: the WebSocket API that streams a reply as it is written.

    Returns None when the block is absent or `enabled` is false, and the stack then
    synthesizes NO WebSocket API, no connect authorizer, no streaming functions and no
    `streamingApiUrl` in config.json - so the frontend never opens a socket and every
    student stays on POST /chat. Same gate as resolve_cost_model, for the same reason: off
    should be one state with one spelling, and the absence of the URL in config.json is
    what makes the browser's choice of transport a config decision rather than a code one.

    OFF IS NOT AN ERROR, and off is the shipped default: this lands dark so it can be
    turned on deliberately rather than by merging.

    THE BATCHING NUMBERS ARE HERE BECAUSE THEY ARE A COST CONTROL, not a feel knob. Every
    push down a WebSocket is a billable API Gateway message, so a naive one-message-per-
    token stream multiplies the message count by roughly the token count for no visible
    benefit - the frontend already animates arriving text at ~108 characters a second, and
    the model outruns that. Batching to a few hundred characters puts a turn in the low
    tens of messages.

    OUTPUT GUARDRAIL DEFAULTS OFF, AND ASYNC IS UNREPRESENTABLE. When it is on the stack
    attaches this stack's guardrail to ConverseStream in `sync` mode, which is the only
    mode this code can emit: `async` releases chunks to the student BEFORE they are
    scanned, which is not a screen, and it does not support PII masking. The default is
    off because it is measured (2026-08-12, us-west-2, claude-sonnet-4-6, n=4 real
    questions): sync mode moved the model's time to first token from a median of 1.12s to
    6.75s while total stream time barely moved, because sync mode holds the response back
    and scans it in large chunks. That is most of this feature's benefit spent on a screen
    that, with today's guardrail, cannot fire - the one filter is PROMPT_ATTACK with
    outputStrength forced to NONE and there is no PII policy, so it scans output and can
    never intervene on it. Turn it on the day a policy is added that can.
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
        # Booleans are ints in Python, so `delta_min_chars: true` would resolve to 1 - one
        # gateway message per character, which is the exact bill this block exists to bound.
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
        # Floor of 1 would be one message per character; the ceiling keeps a batch under a
        # size where the reader can see the text arrive in blocks rather than flow.
        "delta_min_chars": _bounded_int("delta_min_chars", 16, 2000),
        # How long a partial batch may wait before it is pushed anyway. Without it the tail
        # of a reply - the last few characters, under the batch size - would never be sent
        # as a delta and the preview would stall short of the final payload.
        "delta_max_delay_ms": _bounded_int("delta_max_delay_ms", 50, 5000),
        "output_guardrail": output_guardrail,
    }


def resolve_escalation(config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The `escalation` block: the email draft a turn can offer to send to a human.

    Returns None when the block is absent or `recipient` is blank, and the stack then sets
    no ESCALATION_* variables on the chat function and stamps no `escalationRecipient` into
    config.json. THE ABSENCE IS THE GATE, the shape resolve_cost_model and resolve_okta
    already use, and here it reaches further than either: with no address the system prompt
    never mentions the tag (app/prompts.py), so the model is not taught a contract whose
    output the server would drop, and the browser has no recipient, so the component is not
    in the page at all.

    OFF IS NOT AN ERROR. A deployment with nowhere to route a student is the honest state of
    an install that has not agreed a mailbox with the campus yet.

    THE ADDRESS IS VALIDATED SHALLOWLY - one `@`, no whitespace - and deliberately not with
    a full RFC 5322 pattern. What is being caught is a config mistake that would otherwise
    surface as a mail client refusing to open in front of a student: an empty local part, a
    display name pasted in with the address, a stray comma making it two recipients. One
    recipient, because the draft is addressed to one office.

    `max_chars` is the draft prose's guard, and it is the one cap in this file whose
    violation DROPS rather than truncates (app/escalation.py), so its floor is generous: a
    dropped digit here would silently stop the feature working rather than shorten anything.
    """
    escalation_cfg = config.get("escalation")
    if not escalation_cfg:
        return None
    if not isinstance(escalation_cfg, dict):
        raise ValueError("escalation must be a mapping.")

    recipient = escalation_cfg.get("recipient")
    if recipient is None or (isinstance(recipient, str) and not recipient.strip()):
        return None
    if not isinstance(recipient, str):
        raise ValueError(
            f"escalation.recipient must be an email address (got {recipient!r}). Leave it "
            "out or empty to turn the escalate-to-human path off."
        )
    recipient = recipient.strip()
    local, separator, domain = recipient.partition("@")
    if not separator or not local or not domain or any(c.isspace() for c in recipient):
        raise ValueError(
            f"escalation.recipient must be one plain email address (got {recipient!r}): a "
            "local part, an @, and a domain, with no display name and no second address. "
            "It is put straight into a mailto the student's mail client has to open."
        )
    if "," in recipient or ";" in recipient:
        raise ValueError(
            f"escalation.recipient must name ONE mailbox (got {recipient!r}). A draft is "
            "addressed to one office; a list here would send every student's message to "
            "everybody on it."
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
    """The `okta` block: the SAML identity provider federated into the chat user pool.

    Returns None when the block is absent or `metadata_url` is absent/empty, and the stack
    then creates no identity provider at all and leaves the human app client on COGNITO
    alone - which is the whole gate, the same shape resolve_cost_model uses. THE ABSENCE IS
    THE GATE, not a flag beside the value: a URL is the only thing a provider cannot be
    built without, so there is no state where the key is filled in and the provider is off.

    OFF IS NOT AN ERROR. A deployment with no metadata URL is the local-accounts-only stack
    that exists today, so an absent block must not fail `cdk synth`. THE SAME KEY SET THREE
    WAYS is the point: empty for local-only, one org's URL for a federation rehearsal,
    SJSU's later - all without a code change.

    A METADATA URL RATHER THAN AN UPLOADED FILE, deliberately. Cognito re-fetches a URL on
    its own, so the IdP's signing certificate rotating on the Okta side is not an outage
    waiting on somebody to notice and re-upload a file.

    The Okta-side attribute NAME is config because it genuinely differs between orgs; what
    it maps TO is not, because that is this pool's own `email` attribute.
    """
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
    # HTTPS ONLY, and not merely on principle: Cognito fetches this document itself, and it
    # carries the signing certificate every assertion is verified against. Over http that
    # fetch is a trivially forgeable trust anchor for the whole federation. Cognito rejects
    # a non-https URL at CreateIdentityProvider, so this moves the failure to synth.
    if not metadata_url.startswith("https://"):
        raise ValueError(
            f"okta.metadata_url must be an https:// URL (got {metadata_url!r}). Cognito "
            "fetches this document itself and trusts the signing certificate in it, so "
            "plain http would be a forgeable trust anchor - and Cognito rejects it anyway."
        )

    # Absent means `email`, which is what an Okta org usually calls it. Present means the
    # deployer looked; an empty string means they half-looked, and that is an error rather
    # than a silent fall back to the default - an unmapped email is a federated account with
    # no address on it, which shows up as a blank sidebar label rather than as a failure.
    email_attribute = okta_cfg.get("email_attribute", _DEFAULT_OKTA_EMAIL_ATTRIBUTE)
    if not isinstance(email_attribute, str) or not email_attribute.strip():
        raise ValueError(
            f"okta.email_attribute must be a non-empty string (got {email_attribute!r}); "
            f"omit the key entirely to accept the default {_DEFAULT_OKTA_EMAIL_ATTRIBUTE!r}."
        )

    return {
        # Not read from config - see OKTA_PROVIDER_NAME. Returned here so the stack takes
        # the name from the same resolved block as everything else about the provider.
        "provider_name": OKTA_PROVIDER_NAME,
        "metadata_url": metadata_url,
        "email_attribute": email_attribute.strip(),
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
    # Returns None when the per-user cap is off, which is a decision rather than an error -
    # but a non-integer or negative limit still fails synth here, where the message can name
    # the key, rather than deploying a stack that refuses every student's first question.
    resolve_rate_limit(config)
    # Returns None when the panel is off, which is a valid config rather than an error -
    # but an ENABLED block with a bad rate still fails synth here, before any construct
    # exists, rather than reaching a browser as $NaN.
    resolve_cost_model(config)
    # Same shape: None when there is no metadata URL, which is the local-accounts-only
    # deployment and not an error. A URL that IS set gets checked here rather than at
    # CreateIdentityProvider, which is a mid-update deploy failure.
    resolve_okta(config)
    # And again: None when the WebSocket streaming path is off, which is the shipped
    # default and not an error - but an ENABLED block with a batch size of 1 still fails
    # synth here, where the message can name the key, rather than deploying an endpoint
    # that bills one API Gateway message per token.
    resolve_streaming(config)
    # Once more: None when no recipient is configured, which is the honest state of an
    # install that has not agreed a mailbox with the campus yet. A CONFIGURED block with a
    # malformed address still fails here rather than reaching a student as a mail client
    # that refuses to open.
    resolve_escalation(config)


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
