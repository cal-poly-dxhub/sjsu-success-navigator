"""Assertions on the synth-time config validators.

Two things are being tested, and they fail for different reasons:

  1. The real config.yaml passes every validator, and resolves to the values
     docs/synthesis.md decided on. This catches a config edit that drifts from the
     decisions of record.
  2. Each validator actually rejects the bad value it exists to reject. A validator that
     silently accepts everything is worse than no validator, it reads like a guarantee.

Every rejection case here is a real deploy failure the validator moves to synth, not a
hypothetical. Where a bad value would cause a replacement of a resource holding data
(chunking, index dimension, non-filterable keys), the comment says so.

Run from infra/ with `python -m pytest`.
"""

import copy
from pathlib import Path

import pytest

from infra.config import (
    DEFAULT_CONFIG_PATH,
    load_config,
    resolve_cards,
    resolve_chat,
    resolve_chat_history,
    resolve_chunking,
    resolve_cors_allow_origins,
    resolve_data_source_name,
    resolve_generation,
    resolve_guardrail,
    resolve_knowledge_base,
    resolve_request,
    resolve_retrieval,
    resolve_scraper,
    resolve_seed_pages,
    resolve_vector_store,
    seed_list_path,
    validate_config,
)


# Federation ships OFF, so every test that needs a provider turns one on with this. Not a real
# org: a metadata URL is a trust anchor, and the repo should not carry somebody's tenant.
_EXAMPLE_METADATA_URL = "https://example.okta.com/app/exampleappid/sso/saml/metadata"


@pytest.fixture
def config():
    """A fresh deep copy of the real config.yaml per test, so mutations do not leak."""
    return copy.deepcopy(load_config())


def test_real_config_passes_every_validator(config):
    """The committed config.yaml survives the full synth-time gate."""
    validate_config(config)


def test_real_config_path_is_the_repo_root_file():
    """Resolved relative to __file__, so synth from infra/ finds the root config."""
    repo_root = Path(__file__).resolve().parents[3]

    assert DEFAULT_CONFIG_PATH.name == "config.yaml"
    assert DEFAULT_CONFIG_PATH.parent == repo_root
    assert DEFAULT_CONFIG_PATH.exists()
    # The root is the root because these live there too, not because of what it is called.
    assert (DEFAULT_CONFIG_PATH.parent / "data" / "urls.csv").exists()
    assert (DEFAULT_CONFIG_PATH.parent / "infra").is_dir()


def test_knowledge_base_resolves_to_the_decided_values(config):
    """Titan v2 at 1024 dimensions, gav's exact vector-store shape (docs/synthesis.md)."""
    kb = resolve_knowledge_base(config)
    assert kb["name"] == "sjsu-navigator-kb"
    assert kb["embedding_model_id"] == "amazon.titan-embed-text-v2:0"
    assert kb["vector_dimension"] == 1024


def test_vector_store_resolves_to_the_decided_shape(config):
    vs = resolve_vector_store(config)
    assert vs["vector_bucket_name"] == "sjsu-navigator-vectors"
    assert vs["index_name"] == "bedrock-knowledge-base-default-index"
    assert vs["data_type"] == "float32"
    assert vs["distance_metric"] == "cosine"
    # Bedrock's internal keys.
    assert "AMAZON_BEDROCK_TEXT" in vs["non_filterable_metadata_keys"]
    assert "AMAZON_BEDROCK_METADATA" in vs["non_filterable_metadata_keys"]


def test_chunking_resolves_to_the_gav_baseline(config):
    """A starting baseline, not a value tuned against this corpus."""
    chunking = resolve_chunking(config)
    assert chunking["strategy"] == "FIXED_SIZE"
    assert chunking["max_tokens"] == 600
    assert chunking["overlap_percentage"] == 20
    assert chunking["name_suffix"] == "fixedsize-600t20p"


def test_data_source_name_folds_in_the_chunk_config(config):
    """The chunk config rides in the name so a chunking change (a replacement, since chunking is immutable) gets a distinct name instead of colliding on 409 AlreadyExists when CloudFormation creates the replacement before deleting the original."""
    assert resolve_data_source_name(config) == "sjsu-navigator-kb-s3-fixedsize-600t20p"


def test_data_source_name_changes_when_chunking_changes(config):
    """The point of the fold: a different chunk config yields a different name."""
    before = resolve_data_source_name(config)
    config["chunking"]["max_tokens"] = 800
    assert resolve_data_source_name(config) != before
    assert "800t20p" in resolve_data_source_name(config)


def test_scraper_resolves_to_one_daily_schedule(config):
    """Single daily schedule, no tiers (docs/synthesis.md)."""
    scraper = resolve_scraper(config)
    assert scraper["schedule_cron"] == "cron(30 11 * * ? *)"
    assert scraper["url_list_file"] == "urls.csv"
    assert scraper["timeout_seconds"] == 20
    assert scraper["user_agent"].startswith("SJSUNavigatorScraper/")


def test_seed_pages_match_the_authoritative_crawl_list(config):
    """data/urls.csv is authoritative over the brief: 238 pages, three hosts."""
    pages = resolve_seed_pages(config)
    assert len(pages) == 238
    assert len({p["url"] for p in pages}) == 238
    assert all(p["url"].startswith("https://") for p in pages)
    # `section` is why this list is validated at all, it reaches the metadata sidecars.
    assert all(p["section"] for p in pages)
    assert all(p["title"] for p in pages)
    hosts = {p["url"].split("/")[2] for p in pages}
    assert hosts == {"www.sjsu.edu", "careercenter.sjsu.edu", "library.sjsu.edu"}


def test_seed_list_path_is_repo_root_relative(config):
    """Resolved against the repo root's data/ directory, not the cwd, synth runs from infra/."""
    path = seed_list_path(config)
    assert path.exists()
    assert path.parent == DEFAULT_CONFIG_PATH.parent / "data"


def test_cors_allow_origins_resolves_to_a_list(config):
    origins = resolve_cors_allow_origins(config)
    assert origins == ["http://localhost:4321"]


def test_guardrail_resolves_to_one_input_only_prompt_attack_filter(config):
    """One filter, PROMPT_ATTACK, input side only."""
    guardrail = resolve_guardrail(config)
    assert guardrail["name"] == "sjsu-navigator-input-guardrail"
    assert guardrail["content_filters"] == [
        {"type": "PROMPT_ATTACK", "input_strength": "HIGH", "output_strength": "NONE"}
    ]
    assert guardrail["blocked_input_messaging"].startswith("I can't help with that request.")


def test_guardrail_output_strength_is_forced_not_configured(config):
    """output_strength is not a knob."""
    config["guardrail"]["content_filters"][0]["output_strength"] = "HIGH"
    assert resolve_guardrail(config)["content_filters"][0]["output_strength"] == "NONE"


def test_generation_resolves_the_cross_region_inference_profile(config):
    """base_model_id and is_inference_profile drive the IAM shape in the stack, so they are pinned here rather than inferred from a string test at the call site."""
    generation = resolve_generation(config)
    assert generation["model_id"] == "us.anthropic.claude-sonnet-4-6"
    assert generation["is_inference_profile"] is True
    assert generation["base_model_id"] == "anthropic.claude-sonnet-4-6"
    assert generation["max_tokens"] == 1200
    assert generation["temperature"] == 0.2


def test_bare_model_id_is_not_treated_as_an_inference_profile(config):
    """A bare id needs one foundation-model ARN; the profile shape is AccessDenied."""
    config["generation"]["model_id"] = "anthropic.claude-sonnet-4-6"
    generation = resolve_generation(config)
    assert generation["is_inference_profile"] is False
    assert generation["base_model_id"] is None


def test_retrieval_and_request_resolve_to_camps_tuned_values(config):
    retrieval = resolve_retrieval(config)
    assert retrieval["number_of_results"] == 8
    # Tuned against a corpus that no longer exists; retune with the eval (docs/build-plan.md).
    assert retrieval["min_score"] == 0.35
    assert resolve_request(config)["max_query_chars"] == 2000


def test_cards_resolve_to_the_decided_caps(config):
    """The card contract's numbers, pinned so a later nudge has to be a deliberate edit to a test that says so."""
    cards = resolve_cards(config)
    assert cards["max_cards"] == 4
    assert cards["max_retrieval_results"] == 6
    assert cards["title_max_chars"] == 90
    assert cards["desc_max_chars"] == 600
    assert cards["followup_max_chars"] == 120


def test_card_ceiling_above_the_retrieval_count_is_rejected(config):
    """A ceiling the model cannot reach."""
    config["cards"]["max_retrieval_results"] = 3
    config["cards"]["max_cards"] = 4
    with pytest.raises(ValueError, match="must be at least cards.max_cards"):
        resolve_cards(config)


@pytest.mark.parametrize(
    "key,value",
    [
        ("desc_max_chars", 60),      # a dropped digit from 600
        ("title_max_chars", 9),      # from 90
        ("followup_max_chars", 12),  # from 120
    ],
)
def test_card_cap_with_a_dropped_digit_is_rejected(config, key, value):
    """The failure this catches is silent: nothing errors, every field is just truncated to a fragment forever, and the prompt faithfully instructs the model to write them that way."""
    config["cards"][key] = value
    with pytest.raises(ValueError, match=f"cards.{key}"):
        resolve_cards(config)


def test_missing_cards_block_is_rejected(config):
    del config["cards"]
    with pytest.raises(ValueError, match="missing the `cards` block"):
        resolve_cards(config)


def test_validate_config_covers_the_card_caps(config):
    """Same reason as the chat block: these are read at runtime by the parser and the prompt builder, so a bad value fails the build rather than every answer after the deploy."""
    config["cards"]["desc_max_chars"] = 60
    with pytest.raises(ValueError, match="desc_max_chars"):
        validate_config(config)


def test_chat_resolves_the_loop_caps(config):
    """Camp's values, but knobs rather than literals: the iteration cap is what stops a runaway tool-use loop inside a 29-second budget."""
    chat = resolve_chat(config)
    assert chat["max_converse_iterations"] == 6
    assert chat["max_history_messages"] == 24


def test_chat_resolves_the_read_endpoint_caps(config):
    """Separate numbers from max_history_messages above, and the difference is what they cost: that one is the window the model is shown, billed in tokens on every turn, while these bound one DynamoDB query of already-stored items for the browser."""
    chat = resolve_chat(config)
    assert chat["max_conversations_listed"] == 40
    assert chat["max_conversation_messages"] == 60


@pytest.mark.parametrize(
    "key", ["max_conversations_listed", "max_conversation_messages"]
)
@pytest.mark.parametrize("value", [0, -1, 2.5, "40"])
def test_a_non_positive_read_cap_is_rejected(config, key, value):
    """A zero here is a sidebar that lists nothing and a conversation that opens blank, which looks like data loss rather than a typo in config.yaml."""
    config["chat"][key] = value
    with pytest.raises(ValueError, match=key):
        resolve_chat(config)


@pytest.mark.parametrize(
    "name",
    [
        "sjsu navigator guardrail",   # space: outside Bedrock's [0-9a-zA-Z-_]+
        "sjsu.navigator.guardrail",   # dot: legal in S3 Vectors names, not here
        "a" * 51,                     # over the 50-char cap
        "",
    ],
)
def test_invalid_guardrail_name_is_rejected(config, name):
    """Every one of these synthesizes fine on the L1 CfnGuardrail and fails at deploy."""
    config["guardrail"]["name"] = name
    with pytest.raises(ValueError, match="guardrail.name"):
        resolve_guardrail(config)


def test_over_long_blocked_input_messaging_is_rejected(config):
    """Bedrock caps this at 500 characters."""
    config["guardrail"]["blocked_input_messaging"] = "x" * 501
    with pytest.raises(ValueError, match="at most 500"):
        resolve_guardrail(config)


def test_empty_content_filters_is_rejected(config):
    """A guardrail with no policy is billed on every request and screens nothing."""
    config["guardrail"]["content_filters"] = []
    with pytest.raises(ValueError, match="non-empty list"):
        resolve_guardrail(config)


def test_unknown_filter_type_is_rejected(config):
    """Bedrock's category is PROMPT_ATTACK, and the wrong name is a deploy-time failure."""
    config["guardrail"]["content_filters"] = [
        {"type": "PROMPT_INJECTION", "input_strength": "HIGH"}
    ]
    with pytest.raises(ValueError, match="content_filters\\[0\\].type"):
        resolve_guardrail(config)


def test_unknown_filter_strength_is_rejected(config):
    """Strengths are an enum (NONE/LOW/MEDIUM/HIGH), not a number."""
    config["guardrail"]["content_filters"] = [
        {"type": "PROMPT_ATTACK", "input_strength": "MAXIMUM"}
    ]
    with pytest.raises(ValueError, match="input_strength"):
        resolve_guardrail(config)


def test_duplicate_filter_category_is_rejected(config):
    """Bedrock allows one filter per category; a duplicate is rejected at deploy."""
    config["guardrail"]["content_filters"] = [
        {"type": "PROMPT_ATTACK", "input_strength": "HIGH"},
        {"type": "PROMPT_ATTACK", "input_strength": "LOW"},
    ]
    with pytest.raises(ValueError, match="more than once"):
        resolve_guardrail(config)


def test_other_bedrock_filter_categories_are_accepted(config):
    """The resolver must not hardcode PROMPT_ATTACK."""
    config["guardrail"]["content_filters"] = [
        {"type": "VIOLENCE", "input_strength": "MEDIUM"}
    ]
    assert resolve_guardrail(config)["content_filters"][0]["type"] == "VIOLENCE"


@pytest.mark.parametrize("temperature", [-0.1, 1.5, "0.2", True])
def test_out_of_range_temperature_is_rejected(config, temperature):
    config["generation"]["temperature"] = temperature
    with pytest.raises(ValueError, match="generation.temperature"):
        resolve_generation(config)


@pytest.mark.parametrize("number_of_results", [0, 101, -1])
def test_number_of_results_outside_bedrocks_range_is_rejected(config, number_of_results):
    """1-100 is Bedrock's own range for Retrieve."""
    config["retrieval"]["number_of_results"] = number_of_results
    with pytest.raises(ValueError, match="number_of_results"):
        resolve_retrieval(config)


def test_hundred_results_is_accepted(config):
    """Boundary: 100 is the limit, not one under it."""
    config["retrieval"]["number_of_results"] = 100
    assert resolve_retrieval(config)["number_of_results"] == 100


@pytest.mark.parametrize("min_score", [-0.1, 1.1, 35, "0.35"])
def test_min_score_outside_the_normalized_range_is_rejected(config, min_score):
    """The nastiest of these is 35, a plausible "percent" reading of 0.35."""
    config["retrieval"]["min_score"] = min_score
    with pytest.raises(ValueError, match="min_score"):
        resolve_retrieval(config)


def test_missing_chat_block_is_rejected(config):
    """The loop caps have no safe default: without a cap a tool-use loop can spend the whole 29-second budget on Converse calls."""
    del config["chat"]
    with pytest.raises(ValueError, match="missing the `chat` block"):
        resolve_chat(config)


@pytest.mark.parametrize("iterations", [0, -1, 2.5, "6"])
def test_non_positive_iteration_cap_is_rejected(config, iterations):
    config["chat"]["max_converse_iterations"] = iterations
    with pytest.raises(ValueError, match="max_converse_iterations"):
        resolve_chat(config)


def test_non_positive_query_char_cap_is_rejected(config):
    config["request"]["max_query_chars"] = 0
    with pytest.raises(ValueError, match="max_query_chars"):
        resolve_request(config)


def test_missing_block_is_rejected(config):
    """Named explicitly, or it is an AttributeError that says nothing about config.yaml."""
    del config["knowledge_base"]
    with pytest.raises(ValueError, match="missing the `knowledge_base` block"):
        resolve_knowledge_base(config)


def test_block_that_is_not_a_mapping_is_rejected(config):
    """A commented-out block parses as None, not as a missing key."""
    config["chunking"] = None
    with pytest.raises(ValueError, match="missing the `chunking` block"):
        resolve_chunking(config)


def test_dimension_the_embedding_model_does_not_emit_is_rejected(config):
    """768 is a plausible-looking embedding size that Titan v2 never returns."""
    config["knowledge_base"]["vector_dimension"] = 768
    with pytest.raises(ValueError, match="not an output size of"):
        resolve_knowledge_base(config)


def test_other_titan_v2_dimensions_are_accepted(config):
    """512 and 256 are real Titan v2 output sizes; the validator must not hardcode 1024."""
    for dimension in (512, 256):
        config["knowledge_base"]["vector_dimension"] = dimension
        assert resolve_knowledge_base(config)["vector_dimension"] == dimension


def test_unknown_embedding_model_falls_back_to_the_s3_vectors_range(config):
    """For a model with no dimension table, don't pretend to know its output sizes, just enforce what S3 Vectors itself enforces."""
    config["knowledge_base"]["embedding_model_id"] = "some.future-embed-model:0"
    config["knowledge_base"]["vector_dimension"] = 768
    assert resolve_knowledge_base(config)["vector_dimension"] == 768

    config["knowledge_base"]["vector_dimension"] = 8192
    with pytest.raises(ValueError, match="S3 Vectors rejects"):
        resolve_knowledge_base(config)


def test_non_integer_dimension_is_rejected(config):
    config["knowledge_base"]["vector_dimension"] = "1024"
    with pytest.raises(ValueError, match="must be a positive integer"):
        resolve_knowledge_base(config)


def test_boolean_is_not_accepted_as_an_integer(config):
    """bool subclasses int in Python, so `True` would otherwise sail through as 1."""
    config["chunking"]["max_tokens"] = True
    with pytest.raises(ValueError, match="must be a positive integer"):
        resolve_chunking(config)


@pytest.mark.parametrize(
    "name",
    [
        "-leading-hyphen",   # Bedrock's pattern requires an alphanumeric first char
        "double--hyphen",    # at most one separator per alphanumeric group
        "has space",
        "has.dot",           # legal in S3 Vectors names, not in Bedrock names
        "a" * 101,           # over the 100-group cap
    ],
)
def test_kb_name_violating_bedrock_pattern_is_rejected(config, name):
    """Every one of these synthesizes fine on an L1 construct and fails at deploy."""
    config["knowledge_base"]["name"] = name
    with pytest.raises(ValueError, match="not a valid Bedrock resource name"):
        resolve_knowledge_base(config)


def test_trailing_separator_is_allowed_by_bedrocks_actual_pattern(config):
    """A trailing hyphen is legal where a leading one is not, and the asymmetry reads as a bug."""
    config["knowledge_base"]["name"] = "sjsu-navigator-kb-"
    assert resolve_knowledge_base(config)["name"] == "sjsu-navigator-kb-"


def test_kb_name_that_only_breaks_once_the_chunk_suffix_is_folded_in(config):
    """The dangerous case, and the reason the pattern is re-checked after the fold rather than only at the source: a KB name that passes on its own but whose derived data source name is over Bedrock's limit."""
    config["knowledge_base"]["name"] = "a" * 90
    resolve_knowledge_base(config)  # fine by itself
    with pytest.raises(ValueError, match="derived data source name"):
        resolve_data_source_name(config)


@pytest.mark.parametrize(
    "name",
    [
        "ab",                          # under the 3-char minimum
        "a" * 64,                      # over the 63-char maximum
        "SJSU-Navigator-Vectors",      # uppercase: rejected by S3 Vectors
        "sjsu_navigator_vectors",      # underscore: legal in Bedrock names, not here
        "-leading-hyphen",
        "trailing-hyphen-",
    ],
)
def test_invalid_vector_bucket_name_is_rejected(config, name):
    config["vector_store"]["vector_bucket_name"] = name
    with pytest.raises(ValueError, match="vector_store.vector_bucket_name"):
        resolve_vector_store(config)


def test_invalid_index_name_is_rejected(config):
    config["vector_store"]["index_name"] = "Bedrock_Default_Index"
    with pytest.raises(ValueError, match="vector_store.index_name"):
        resolve_vector_store(config)


def test_dots_are_legal_in_s3_vectors_names(config):
    """The charset difference from Bedrock cuts both ways; don't over-reject."""
    config["vector_store"]["vector_bucket_name"] = "sjsu.navigator.vectors"
    assert resolve_vector_store(config)["vector_bucket_name"] == "sjsu.navigator.vectors"


def test_non_float32_data_type_is_rejected(config):
    """float32 is the only type S3 Vectors supports."""
    config["vector_store"]["data_type"] = "float16"
    with pytest.raises(ValueError, match="must be 'float32'"):
        resolve_vector_store(config)


def test_unsupported_distance_metric_is_rejected(config):
    """S3 Vectors offers cosine and euclidean only, no dotproduct."""
    config["vector_store"]["distance_metric"] = "dotproduct"
    with pytest.raises(ValueError, match="cosine' or 'euclidean"):
        resolve_vector_store(config)


def test_euclidean_is_accepted(config):
    config["vector_store"]["distance_metric"] = "euclidean"
    assert resolve_vector_store(config)["distance_metric"] == "euclidean"


def test_empty_non_filterable_metadata_keys_is_rejected(config):
    """Dropping these is the trap gav already hit: Bedrock's internal keys are filterable by default and blow the filterable-metadata limit, failing every ingestion."""
    config["vector_store"]["non_filterable_metadata_keys"] = []
    with pytest.raises(ValueError, match="non_filterable_metadata_keys must be"):
        resolve_vector_store(config)


def test_more_than_ten_non_filterable_keys_is_rejected(config):
    """S3 Vectors caps this at 10 per index, and the setting is immutable, exceeding it means replacing the index (which takes the KB with it), not editing a property."""
    config["vector_store"]["non_filterable_metadata_keys"] = [f"key-{i}" for i in range(11)]
    with pytest.raises(ValueError, match="at most 10"):
        resolve_vector_store(config)


def test_ten_non_filterable_keys_is_accepted(config):
    """Boundary: 10 is the limit, not one under it."""
    config["vector_store"]["non_filterable_metadata_keys"] = [f"key-{i}" for i in range(10)]
    assert len(resolve_vector_store(config)["non_filterable_metadata_keys"]) == 10


def test_duplicate_non_filterable_keys_are_rejected(config):
    """A duplicate spends one of only 10 immutable slots on nothing."""
    keys = config["vector_store"]["non_filterable_metadata_keys"]
    config["vector_store"]["non_filterable_metadata_keys"] = keys + [keys[0]]
    with pytest.raises(ValueError, match="contains duplicates"):
        resolve_vector_store(config)


def test_unknown_chunking_strategy_is_rejected(config):
    config["chunking"]["strategy"] = "SLIDING_WINDOW"
    with pytest.raises(ValueError, match="not a Bedrock chunking strategy"):
        resolve_chunking(config)


def test_real_strategy_the_stack_does_not_wire_is_rejected(config):
    """SEMANTIC is a real Bedrock strategy, but the stack only builds FIXED_SIZE. So it is rejected here."""
    config["chunking"]["strategy"] = "SEMANTIC"
    with pytest.raises(ValueError, match="only wires FIXED_SIZE"):
        resolve_chunking(config)


@pytest.mark.parametrize("overlap", [0, 100, -5, 20.5, "20"])
def test_out_of_range_overlap_percentage_is_rejected(config, overlap):
    config["chunking"]["overlap_percentage"] = overlap
    with pytest.raises(ValueError, match="overlap_percentage must be an integer 1-99"):
        resolve_chunking(config)


def test_missing_schedule_cron_is_rejected(config):
    """Failure mode without this: a Lambda nothing ever invokes, so the corpus quietly stops refreshing and only shows up later as stale answers."""
    del config["scraper"]["schedule_cron"]
    with pytest.raises(ValueError, match="scraper.schedule_cron"):
        resolve_scraper(config)


def test_unix_style_five_field_cron_is_rejected(config):
    """EventBridge cron takes six fields with a '?' in day-of-month or day-of-week; a bare 5-field UNIX expression is a deploy-time rejection otherwise."""
    config["scraper"]["schedule_cron"] = "30 11 * * *"
    with pytest.raises(ValueError, match=r"cron\(\.\.\.\)"):
        resolve_scraper(config)


def test_missing_crawl_list_file_is_rejected(config):
    """The scraper's only source of URLs."""
    config["scraper"]["url_list_file"] = "does-not-exist.csv"
    with pytest.raises(ValueError, match="does not exist"):
        resolve_seed_pages(config)


def test_crawl_list_missing_the_section_column_is_rejected(config, tmp_path):
    """`section` reaches the metadata sidecars and drives card deprioritization and follow-up buttons."""
    csv_path = tmp_path / "no-section.csv"
    csv_path.write_text("url,title\nhttps://www.sjsu.edu/a,A page\n")
    config["scraper"]["url_list_file"] = str(csv_path)
    with pytest.raises(ValueError, match="missing required column"):
        resolve_seed_pages(_repointed(config, csv_path))


def test_crawl_list_with_a_blank_section_is_rejected(config, tmp_path):
    csv_path = tmp_path / "blank-section.csv"
    csv_path.write_text(
        "url,section,title\n"
        "https://www.sjsu.edu/a,advising,A page\n"
        "https://www.sjsu.edu/b,,B page\n"
    )
    with pytest.raises(ValueError, match="line 3: `section` is empty"):
        resolve_seed_pages(_repointed(config, csv_path))


def test_crawl_list_with_a_duplicate_url_is_rejected(config, tmp_path):
    """A duplicate costs an extra fetch every run and writes the same S3 object twice."""
    csv_path = tmp_path / "dupe.csv"
    csv_path.write_text(
        "url,section,title\n"
        "https://www.sjsu.edu/a,advising,A page\n"
        "https://www.sjsu.edu/a,advising,A page again\n"
    )
    with pytest.raises(ValueError, match="listed more than once"):
        resolve_seed_pages(_repointed(config, csv_path))


def test_crawl_list_with_a_non_http_url_is_rejected(config, tmp_path):
    csv_path = tmp_path / "not-http.csv"
    csv_path.write_text("url,section,title\nwww.sjsu.edu/a,advising,A page\n")
    with pytest.raises(ValueError, match="is not an http"):
        resolve_seed_pages(_repointed(config, csv_path))


def test_header_only_crawl_list_is_rejected(config, tmp_path):
    """An empty list is the prune's worst input: nothing expected, so everything stale."""
    csv_path = tmp_path / "header-only.csv"
    csv_path.write_text("url,section,title\n")
    with pytest.raises(ValueError, match="no pages"):
        resolve_seed_pages(_repointed(config, csv_path))


def _repointed(config, csv_path):
    """Point `scraper.url_list_file` at a tmp_path csv."""
    config["scraper"]["url_list_file"] = str(csv_path)
    return config


def test_chat_history_resolves_to_the_decided_table_name(config):
    """One table for headers and messages both (docs/accounts-and-storage.md, Storage)."""
    assert resolve_chat_history(config)["table_name"] == "sjsu-navigator-chat-history"


@pytest.mark.parametrize(
    "name",
    [
        "chat history",   # spaces are not in DynamoDB's charset
        "chat/history",   # nor is a slash, which reads plausible from the ARN format
        "chat:history",
        "chat#history",   # the item-key separator, easy to carry over by habit
    ],
)
def test_invalid_table_name_is_rejected(config, name):
    """DynamoDB rejects these at CreateTable, which fails the deploy partway through a stack update rather than failing the synth."""
    config["chat_history"]["table_name"] = name
    with pytest.raises(ValueError, match="not a valid DynamoDB table name"):
        resolve_chat_history(config)


def test_too_short_table_name_is_rejected(config):
    """3 characters is DynamoDB's floor."""
    config["chat_history"]["table_name"] = "ch"
    with pytest.raises(ValueError, match="3-255 characters"):
        resolve_chat_history(config)


def test_missing_chat_history_block_is_rejected(config):
    """Not defaulted: a generated name deploys, works, and stops being reproducible."""
    del config["chat_history"]
    with pytest.raises(ValueError, match="chat_history"):
        resolve_chat_history(config)


def test_validate_config_covers_the_history_table(config):
    """The table holds student data, so a name error must fail the build rather than the deploy: CreateTable is rejected mid-update, after other resources have already changed."""
    config["chat_history"]["table_name"] = "chat history"
    with pytest.raises(ValueError, match="not a valid DynamoDB table name"):
        validate_config(config)


def test_cors_wildcard_is_rejected(config):
    """The endpoint fans out to paid Bedrock calls; '*' lets any site drive it from its visitors' browsers."""
    config["cors"]["allow_origins"] = ["*"]
    with pytest.raises(ValueError, match="must not contain"):
        resolve_cors_allow_origins(config)


def test_cors_wildcard_hidden_by_whitespace_is_rejected(config):
    config["cors"]["allow_origins"] = ["https://www.sjsu.edu", " * "]
    with pytest.raises(ValueError, match="must not contain"):
        resolve_cors_allow_origins(config)


def test_empty_cors_allow_origins_is_rejected(config):
    """No safe default exists, so this is an error rather than a fallback."""
    config["cors"]["allow_origins"] = []
    with pytest.raises(ValueError, match="missing cors.allow_origins"):
        resolve_cors_allow_origins(config)


def test_cors_allow_origins_as_a_bare_string_is_rejected(config):
    """A single origin written without a list marker; iterating it would yield characters."""
    config["cors"]["allow_origins"] = "https://www.sjsu.edu"
    with pytest.raises(ValueError, match="must be a list"):
        resolve_cors_allow_origins(config)


def test_validate_config_catches_an_error_in_an_unbuilt_section(config):
    """The reason validate_config exists: a CORS wildcard is caught now, at synth, even though the API section that consumes cors.allow_origins has not been written yet."""
    config["cors"]["allow_origins"] = ["*"]
    with pytest.raises(ValueError, match="must not contain"):
        validate_config(config)


def test_validate_config_covers_the_chat_path_blocks(config):
    """Same reason, one section further on: the values the chat Lambda reads at runtime (min_score, the iteration cap) are validated at synth, so a bad one fails the build rather than every request after the deploy."""
    config["retrieval"]["min_score"] = 35
    with pytest.raises(ValueError, match="min_score"):
        validate_config(config)


def test_the_converse_deadline_must_sit_under_the_lambda_timeout():
    """The wall-clock cap only works if it fires before the function is killed."""
    from infra.config import CHAT_LAMBDA_TIMEOUT_SECONDS

    config = copy.deepcopy(load_config())
    config["chat"]["converse_deadline_seconds"] = CHAT_LAMBDA_TIMEOUT_SECONDS
    with pytest.raises(ValueError, match="must be less than"):
        resolve_chat(config)


def test_the_configured_converse_deadline_leaves_room_after_the_loop():
    """Not just under the timeout, far enough under that card shaping and serialisation, which run after the loop returns, still fit."""
    from infra.config import CHAT_LAMBDA_TIMEOUT_SECONDS

    deadline = resolve_chat(load_config())["converse_deadline_seconds"]
    assert deadline <= CHAT_LAMBDA_TIMEOUT_SECONDS - 5


def test_the_cost_panel_is_off_by_a_config_flag_and_that_is_not_an_error():
    """`enabled: false` resolves to None so the stack omits the key from config.json."""
    from infra.config import resolve_cost_model

    config = copy.deepcopy(load_config())
    config["cost_model"]["enabled"] = False
    assert resolve_cost_model(config) is None

    config["cost_model"]["rates"] = {"nonsense": "not a number"}
    assert resolve_cost_model(config) is None, "off must not validate the rest"


def test_an_absent_cost_model_block_is_also_off():
    """A config.yaml with no cost_model at all is a valid config, not a missing section."""
    from infra.config import resolve_cost_model

    config = copy.deepcopy(load_config())
    del config["cost_model"]
    assert resolve_cost_model(config) is None
    validate_config(config)


def test_an_enabled_cost_model_rejects_a_missing_rate():
    """Every rate is required when the panel is on, with no defaults anywhere."""
    from infra.config import resolve_cost_model

    config = copy.deepcopy(load_config())
    del config["cost_model"]["rates"]["generation_input_per_1m"]
    with pytest.raises(ValueError, match="generation_input_per_1m"):
        resolve_cost_model(config)


def test_an_enabled_cost_model_rejects_a_free_question():
    """A zero here reads as a measurement rather than as an unfilled placeholder, and the panel would confidently show $0.00 for a system that bills real Bedrock tokens."""
    from infra.config import resolve_cost_model

    config = copy.deepcopy(load_config())
    config["cost_model"]["measured"]["context_tokens_per_call_base"] = 0
    with pytest.raises(ValueError, match="greater than zero"):
        resolve_cost_model(config)


def test_the_committed_cost_model_prices_a_question_in_a_plausible_range():
    """The real config.yaml, priced end to end."""
    from infra.config import resolve_cost_model

    model = resolve_cost_model(load_config())
    assert model is not None
    rates, measured = model["rates"], model["measured"]
    per_question = (
        measured["model_calls_avg"]
        * measured["context_tokens_per_call_base"]
        / 1e6
        * rates["generation_input_per_1m"]
        + measured["output_tokens_avg"] / 1e6 * rates["generation_output_per_1m"]
    )
    assert 0.005 < per_question < 0.25, f"a question priced at ${per_question:.4f}"


def test_the_titling_model_gets_the_same_profile_resolution(config):
    """One rule for deciding profile-versus-bare-id, not two."""
    generation = resolve_generation(config)
    assert generation["title_model_id"] == "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    assert generation["title_is_inference_profile"] is True
    assert (
        generation["title_base_model_id"] == "anthropic.claude-haiku-4-5-20251001-v1:0"
    )


def test_a_bare_titling_model_id_is_not_treated_as_a_profile(config):
    config["generation"]["title_model_id"] = "anthropic.claude-haiku-4-5-20251001-v1:0"
    generation = resolve_generation(config)
    assert generation["title_is_inference_profile"] is False
    assert generation["title_base_model_id"] is None


def test_a_missing_titling_model_fails_at_synth(config):
    """Identity, like every other model id here: a default would mean a misconfigured deploy quietly billing a model nobody chose."""
    del config["generation"]["title_model_id"]
    with pytest.raises(ValueError, match="title_model_id"):
        resolve_generation(config)


def test_the_title_cap_and_budget_resolve_from_config(config):
    chat = resolve_chat(config)
    assert chat["title_max_chars"] == 80
    assert chat["title_deadline_seconds"] == 3


def test_a_dropped_digit_in_the_title_cap_fails_at_synth(config):
    """8 is a plausible typo for 80 and would fail silently in the worst way: every sidebar row truncated to a fragment and every generated title rejected for running past the cap, which together look exactly like a model that cannot write titles."""
    config["chat"]["title_max_chars"] = 8
    with pytest.raises(ValueError, match="title_max_chars"):
        resolve_chat(config)


def test_the_two_deadlines_must_fit_under_the_lambda_timeout_together(config):
    """They run one after the other in a single invocation."""
    from infra.config import CHAT_LAMBDA_TIMEOUT_SECONDS

    config["chat"]["converse_deadline_seconds"] = CHAT_LAMBDA_TIMEOUT_SECONDS - 2
    config["chat"]["title_deadline_seconds"] = 2
    with pytest.raises(ValueError, match="title_deadline_seconds"):
        resolve_chat(config)


def test_the_configured_pair_leaves_room_after_both(config):
    from infra.config import CHAT_LAMBDA_TIMEOUT_SECONDS

    chat = resolve_chat(config)
    assert (
        chat["converse_deadline_seconds"] + chat["title_deadline_seconds"]
        <= CHAT_LAMBDA_TIMEOUT_SECONDS - 2
    )


def test_the_committed_config_caps_a_user_at_a_defensible_daily_spend():
    """The real config.yaml's per-user cap, priced against the real cost model."""
    from infra.config import resolve_cost_model, resolve_rate_limit

    config = load_config()
    limit = resolve_rate_limit(config)
    assert limit == 60

    cost = resolve_cost_model(config)
    measured, rates = cost["measured"], cost["rates"]
    # Input tokens dominate: the retrieved passages ride in them and the loop resends the whole context on a second call.
    per_question = (
        measured["model_calls_avg"]
        * measured["context_tokens_per_call_base"]
        * rates["generation_input_per_1m"]
        / 1_000_000
        + measured["output_tokens_avg"] * rates["generation_output_per_1m"] / 1_000_000
    )
    assert 0.5 < limit * per_question < 5.0, (
        f"one user can now spend ${limit * per_question:.2f} a day on generation alone"
    )


def test_a_zero_limit_is_off_rather_than_a_cap_of_zero():
    """The gate: 0 resolves to None, the stack omits the variable, and the function reads an absent variable as disabled."""
    from infra.config import resolve_rate_limit

    config = copy.deepcopy(load_config())
    config["rate_limit"]["daily_message_limit"] = 0
    assert resolve_rate_limit(config) is None
    validate_config(config)


def test_an_absent_rate_limit_block_is_also_off():
    """A config.yaml with no rate_limit at all is a valid config, a closed pilot where every account is known is a decision, not a missing section."""
    from infra.config import resolve_rate_limit

    config = copy.deepcopy(load_config())
    del config["rate_limit"]
    assert resolve_rate_limit(config) is None
    validate_config(config)


def test_a_negative_limit_is_rejected_rather_than_read_as_off():
    """A negative limit is not another spelling of disabled."""
    from infra.config import resolve_rate_limit

    config = copy.deepcopy(load_config())
    config["rate_limit"]["daily_message_limit"] = -1
    with pytest.raises(ValueError, match="must not be negative"):
        resolve_rate_limit(config)


@pytest.mark.parametrize("bad", ["60", 60.5, True, None])
def test_a_non_integer_limit_is_rejected(bad):
    """`true` is the one worth naming: booleans are ints in Python, so a flag pasted here would sail through an isinstance check and resolve to a cap of exactly one message a day."""
    from infra.config import resolve_rate_limit

    config = copy.deepcopy(load_config())
    config["rate_limit"]["daily_message_limit"] = bad
    if bad is None:
        assert resolve_rate_limit(config) is None
    else:
        with pytest.raises(ValueError, match="must be an integer"):
            resolve_rate_limit(config)


def test_the_committed_config_ships_no_identity_provider(config):
    """Off by default. A metadata URL is a trust anchor Cognito fetches and believes, so a shipped one would have every install federating against an org its operator does not own."""
    from infra.config import resolve_okta

    assert config["okta"]["metadata_url"] == "", (
        "config.yaml must ship an empty metadata_url - filling it in is the deployer's "
        "deliberate act, never a default they inherit"
    )
    assert resolve_okta(config) is None
    # The key stays, because an empty slot with the default claim name beside it is what
    # tells a deployer what to fill in; a missing block is the same stack but no instruction.
    assert config["okta"]["email_attribute"] == "email"


def test_a_metadata_url_federates_under_the_role_name(config):
    """Turned on, it resolves to the role name, never an org's."""
    from infra.config import OKTA_PROVIDER_NAME, resolve_okta

    config["okta"]["metadata_url"] = _EXAMPLE_METADATA_URL
    okta = resolve_okta(config)
    assert okta is not None
    assert okta["provider_name"] == "Okta" == OKTA_PROVIDER_NAME
    assert okta["metadata_url"] == _EXAMPLE_METADATA_URL
    assert okta["metadata_url"].startswith("https://")
    assert okta["email_attribute"] == "email"


def test_the_provider_name_is_not_reachable_from_config(config):
    """Renaming mints new `sub` values, which are partition keys, and orphans every conversation."""
    from infra.config import resolve_okta

    config["okta"]["metadata_url"] = _EXAMPLE_METADATA_URL
    config["okta"]["provider_name"] = "SJSU"
    config["okta"]["name"] = "SJSU"
    assert resolve_okta(config)["provider_name"] == "Okta"


def test_no_metadata_url_means_no_identity_provider(config):
    """The absence is the gate, in the shape resolve_cost_model already uses."""
    from infra.config import resolve_okta

    for mutate in (
        lambda c: c.pop("okta"),
        lambda c: c["okta"].pop("metadata_url"),
        lambda c: c["okta"].update(metadata_url=""),
        lambda c: c["okta"].update(metadata_url="   "),
        lambda c: c["okta"].update(metadata_url=None),
    ):
        candidate = copy.deepcopy(config)
        mutate(candidate)
        assert resolve_okta(candidate) is None, candidate.get("okta")
        # And the whole synth-time gate still passes, off is not a half-configured stack.
        validate_config(candidate)


def test_a_plain_http_metadata_url_fails_at_synth(config):
    """Cognito fetches this document itself and trusts the signing certificate inside it, so http would be a forgeable trust anchor for every assertion."""
    from infra.config import resolve_okta

    config["okta"]["metadata_url"] = _EXAMPLE_METADATA_URL.replace("https://", "http://")
    with pytest.raises(ValueError, match="https"):
        resolve_okta(config)


def test_the_okta_side_attribute_name_is_configurable_but_never_blank(config):
    """Orgs spell the email claim differently, a SAML namespace URI, `emailAddress`, so the name is config."""
    from infra.config import resolve_okta

    config["okta"]["metadata_url"] = _EXAMPLE_METADATA_URL
    config["okta"]["email_attribute"] = (
        "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"
    )
    assert resolve_okta(config)["email_attribute"].endswith("emailaddress")

    del config["okta"]["email_attribute"]
    assert resolve_okta(config)["email_attribute"] == "email", "omitted means the default"

    config["okta"]["email_attribute"] = "  "
    with pytest.raises(ValueError, match="email_attribute"):
        resolve_okta(config)


def test_streaming_is_off_unless_the_block_says_otherwise(config):
    """Off unless the block says otherwise."""
    from infra.config import resolve_streaming

    assert load_config()["streaming"]["enabled"] is True, (
        "sanity: this file's fixtures are the real config.yaml, which commits streaming on"
    )

    for mutate in (
        lambda c: c.pop("streaming"),
        lambda c: c.update(streaming={}),
        lambda c: c["streaming"].update(enabled=False),
    ):
        candidate = copy.deepcopy(config)
        mutate(candidate)
        assert resolve_streaming(candidate) is None
        # Off is not a half-configured stack: the whole synth-time gate still passes.
        validate_config(candidate)


def test_an_enabled_block_resolves_its_batching_numbers(config):
    from infra.config import resolve_streaming

    config["streaming"]["enabled"] = True
    resolved = resolve_streaming(config)
    assert resolved["delta_min_chars"] == config["streaming"]["delta_min_chars"]
    assert resolved["delta_max_delay_ms"] == config["streaming"]["delta_max_delay_ms"]
    assert resolved["output_guardrail"] is False


def test_a_batch_size_of_one_fails_at_synth(config):
    """A batch size of 1 was one billable message per character, and looked identical on screen."""
    from infra.config import resolve_streaming

    config["streaming"]["enabled"] = True
    for value in (1, 0, -5, True, "160", None, 99999):
        config["streaming"]["delta_min_chars"] = value
        with pytest.raises(ValueError, match="delta_min_chars"):
            resolve_streaming(config)


def test_the_flush_delay_is_bounded_at_both_ends(config):
    """Too small is a message per token by another route; too large and the tail of a reply, whatever is left under the batch size, sits unsent while the reader waits."""
    from infra.config import resolve_streaming

    config["streaming"]["enabled"] = True
    for value in (10, 0, -1, 60000, False, "250"):
        config["streaming"]["delta_max_delay_ms"] = value
        with pytest.raises(ValueError, match="delta_max_delay_ms"):
            resolve_streaming(config)


def test_the_output_guardrail_switch_is_a_boolean_and_defaults_off(config):
    """It defaults off because it is measured (2026-08-12, us-west-2, claude-sonnet-4-6): attaching the guardrail to ConverseStream in its only safe mode moved time-to-first-token from a median of 1.12s to 6.75s, because sync mode holds the response back and scans it in large chunks."""
    from infra.config import resolve_streaming

    config["streaming"]["enabled"] = True
    del config["streaming"]["output_guardrail"]
    assert resolve_streaming(config)["output_guardrail"] is False

    config["streaming"]["output_guardrail"] = "yes"
    with pytest.raises(ValueError, match="output_guardrail"):
        resolve_streaming(config)


def test_the_committed_config_carries_an_escalation_recipient(config):
    """The feature is on in the committed file, and it resolves to one plain address."""
    from infra.config import resolve_escalation

    escalation = resolve_escalation(config)
    assert escalation is not None
    assert escalation["recipient"].count("@") == 1
    assert escalation["subject"]
    assert escalation["max_chars"] >= 300


def test_no_contact_means_no_escalation_path(config):
    """The absence is the gate, and here it reaches the function, config.json and the prompt."""
    from infra.config import resolve_escalation

    for mutate in (
        lambda c: c.pop("escalation"),
        lambda c: c["escalation"].pop("contact"),
        lambda c: c["escalation"].update(contact=""),
        lambda c: c["escalation"].update(contact="   "),
        lambda c: c["escalation"].update(contact=None),
    ):
        candidate = copy.deepcopy(config)
        mutate(candidate)
        assert resolve_escalation(candidate) is None, candidate.get("escalation")
        # And the whole synth-time gate still passes: off is not a half-configured stack.
        validate_config(candidate)


def test_the_committed_config_names_a_row_rather_than_spelling_an_address(config):
    """The same mailbox is the SJSU Cares panel's email link, so it is spelled in data/ once."""
    from infra.config import resolve_escalation

    assert config["escalation"]["contact"] == "sjsu-cares"
    assert "@" not in config["escalation"]["contact"]
    assert resolve_escalation(config)["recipient"] == "sjsucares@sjsu.edu"


def _contacts_dir(tmp_path, rows, monkeypatch):
    """A stand-in data/ directory holding one contacts.csv, pointed at by infra.config."""
    import infra.config as config_module

    lines = ["kind,id,label,detail,href,when,in_default_panel,note", *rows]
    (tmp_path / "contacts.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(config_module, "_DATA_DIR", tmp_path)
    return tmp_path


@pytest.mark.parametrize(
    "recipient",
    [
        "sjsucares",  # no domain
        "@sjsu.edu",  # no local part
        "SJSU Cares <sjsucares@sjsu.edu>",  # a display name pasted in with the address
        "sjsucares@sjsu.edu, dean@sjsu.edu",  # two mailboxes
        "sjsucares@sjsu.edu;dean@sjsu.edu",
    ],
)
def test_an_address_a_mail_client_could_not_open_fails_at_synth(
    config, tmp_path, monkeypatch, recipient
):
    """This value goes straight into a mailto the student's mail client has to open, so a malformed one does not fail a deploy, it fails silently in front of a student, on the one turn that was meant to reach a person."""
    from infra.config import resolve_escalation

    _contacts_dir(
        tmp_path,
        [f'escalation,sjsu-cares,SJSU Cares,"{recipient}",,,no,'],
        monkeypatch,
    )
    with pytest.raises(ValueError, match=r"contacts\.csv row 'sjsu-cares'"):
        resolve_escalation(config)


def test_a_contact_id_that_names_no_row_fails_at_synth(config, tmp_path, monkeypatch):
    """Loud at synth rather than an escalation offer addressed to nothing."""
    from infra.config import resolve_escalation

    _contacts_dir(tmp_path, ["escalation,someone-else,Someone,x@sjsu.edu,,,no,"], monkeypatch)
    with pytest.raises(ValueError, match="no row in contacts.csv has that id"):
        resolve_escalation(config)


def test_a_contact_of_the_wrong_kind_fails_at_synth(config, tmp_path, monkeypatch):
    """The `kind` column is what says an address is a place to send a student's own words."""
    from infra.config import resolve_escalation

    _contacts_dir(tmp_path, ["safety,sjsu-cares,Crisis,x@sjsu.edu,,,no,"], monkeypatch)
    with pytest.raises(ValueError, match="rather than 'escalation'"):
        resolve_escalation(config)


def test_two_rows_with_one_id_fail_at_synth(config, tmp_path, monkeypatch):
    """A duplicated row is a copy somebody never finished editing, and the second one would quietly win, so a student's message would go wherever the later line said."""
    from infra.config import resolve_escalation

    _contacts_dir(
        tmp_path,
        [
            "escalation,sjsu-cares,SJSU Cares,a@sjsu.edu,,,no,",
            "escalation,sjsu-cares,SJSU Cares,b@sjsu.edu,,,no,",
        ],
        monkeypatch,
    )
    with pytest.raises(ValueError, match="2 rows with id"):
        resolve_escalation(config)


def test_a_row_with_no_mailbox_fails_at_synth(config, tmp_path, monkeypatch):
    """`detail` is the address on an escalation row."""
    from infra.config import resolve_escalation

    _contacts_dir(tmp_path, ["escalation,sjsu-cares,SJSU Cares,,,,no,"], monkeypatch)
    with pytest.raises(ValueError, match="empty `detail`"):
        resolve_escalation(config)


def test_a_blank_subject_fails_at_synth(config):
    """Staff triage on the subject before they read anything else, and it is the same line on every draft, so an empty one is a deploy-time error rather than a blank in an inbox."""
    from infra.config import resolve_escalation

    config["escalation"]["subject"] = "   "
    with pytest.raises(ValueError, match="escalation.subject"):
        resolve_escalation(config)


def test_a_dropped_digit_in_the_cap_fails_rather_than_silencing_the_feature(config):
    """The floor is a dropped-digit detector, like the card caps', but this cap drops the offer instead of shortening it, so 120-for-1200 would take the feature off the air with nothing on screen to say why."""
    from infra.config import resolve_escalation

    config["escalation"]["max_chars"] = 120
    with pytest.raises(ValueError, match="max_chars"):
        resolve_escalation(config)

    config["escalation"]["max_chars"] = True
    with pytest.raises(ValueError, match="must be an integer"):
        resolve_escalation(config)
