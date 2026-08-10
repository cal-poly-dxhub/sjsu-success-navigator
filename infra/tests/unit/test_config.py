"""Assertions on the synth-time config validators.

Two things are being tested, and they fail for different reasons:

  1. The REAL config.yaml passes every validator, and resolves to the values
     docs/synthesis.md decided on. This catches a config edit that drifts from the
     decisions of record.
  2. Each validator actually rejects the bad value it exists to reject. A validator that
     silently accepts everything is worse than no validator - it reads like a guarantee.

Every rejection case here is a real deploy failure the validator moves to synth, not a
hypothetical. Where a bad value would cause a REPLACEMENT of a resource holding data
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


@pytest.fixture
def config():
    """A fresh deep copy of the real config.yaml per test, so mutations do not leak."""
    return copy.deepcopy(load_config())


# --- The real config.yaml ----------------------------------------------------------------


def test_real_config_passes_every_validator(config):
    """The committed config.yaml survives the full synth-time gate."""
    validate_config(config)


def test_real_config_path_is_the_repo_root_file():
    """Resolved relative to __file__, so synth from infra/ finds the root config.

    The repo root is located the same way config.py locates it - by walking up from a known
    file - rather than by NAME. Asserting the directory was called
    "sjsu-student-success-navagator" made this test a property of the checkout rather than of
    the resolution logic: it failed in every git worktree, where the directory is named after
    the branch, and it would have failed just as hard on a clone into any other folder. What
    the resolution actually promises is that the config sits at the repo root, beside infra/
    and the crawl list, and that is what is checked.
    """
    repo_root = Path(__file__).resolve().parents[3]

    assert DEFAULT_CONFIG_PATH.name == "config.yaml"
    assert DEFAULT_CONFIG_PATH.parent == repo_root
    assert DEFAULT_CONFIG_PATH.exists()
    # The root is the root because these live there too, not because of what it is called.
    assert (DEFAULT_CONFIG_PATH.parent / "url-list.csv").exists()
    assert (DEFAULT_CONFIG_PATH.parent / "infra").is_dir()


def test_knowledge_base_resolves_to_the_decided_values(config):
    """Titan v2 at 1024 dimensions - gav's exact vector-store shape (docs/synthesis.md)."""
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
    # Bedrock's internal keys. Non-filterable, or every ingestion fails on the
    # filterable-metadata limit - and the setting is immutable after index creation.
    assert "AMAZON_BEDROCK_TEXT" in vs["non_filterable_metadata_keys"]
    assert "AMAZON_BEDROCK_METADATA" in vs["non_filterable_metadata_keys"]


def test_chunking_resolves_to_the_gav_baseline(config):
    """FIXED_SIZE 600t/20% is inherited from gav, not tuned against this corpus."""
    chunking = resolve_chunking(config)
    assert chunking["strategy"] == "FIXED_SIZE"
    assert chunking["max_tokens"] == 600
    assert chunking["overlap_percentage"] == 20
    assert chunking["name_suffix"] == "fixedsize-600t20p"


def test_data_source_name_folds_in_the_chunk_config(config):
    """The chunk config rides in the name so a chunking change (a REPLACEMENT, since
    chunking is immutable) gets a distinct name instead of colliding on 409 AlreadyExists
    when CloudFormation creates the replacement before deleting the original."""
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
    assert scraper["url_list_file"] == "url-list.csv"
    assert scraper["timeout_seconds"] == 20
    assert scraper["user_agent"].startswith("SJSUNavigatorScraper/")


def test_seed_pages_match_the_authoritative_crawl_list(config):
    """url-list.csv is authoritative over the brief: 228 pages, three hosts."""
    pages = resolve_seed_pages(config)
    assert len(pages) == 228
    assert len({p["url"] for p in pages}) == 228
    assert all(p["url"].startswith("https://") for p in pages)
    # `section` is why this list is validated at all - it reaches the metadata sidecars.
    assert all(p["section"] for p in pages)
    assert all(p["title"] for p in pages)
    hosts = {p["url"].split("/")[2] for p in pages}
    assert hosts == {"www.sjsu.edu", "careercenter.sjsu.edu", "library.sjsu.edu"}


def test_seed_list_path_is_repo_root_relative(config):
    """Resolved against the repo root, not the cwd - synth runs from infra/."""
    path = seed_list_path(config)
    assert path.exists()
    assert path.parent == DEFAULT_CONFIG_PATH.parent


def test_cors_allow_origins_resolves_to_a_list(config):
    origins = resolve_cors_allow_origins(config)
    assert origins == ["http://localhost:4321"]


def test_guardrail_resolves_to_one_input_only_prompt_attack_filter(config):
    """The decided shape: ONE filter, PROMPT_ATTACK, input side only. Anything else in this
    list would screen the student's message before the system prompt saw it, pre-empting the
    crisis handling the prompt owns (docs/synthesis.md)."""
    guardrail = resolve_guardrail(config)
    assert guardrail["name"] == "sjsu-navigator-input-guardrail"
    assert guardrail["content_filters"] == [
        {"type": "PROMPT_ATTACK", "input_strength": "HIGH", "output_strength": "NONE"}
    ]
    assert guardrail["blocked_input_messaging"].startswith("I can't help with that request.")


def test_guardrail_output_strength_is_forced_not_configured(config):
    """output_strength is not a knob. This guardrail is only ever applied with source=INPUT,
    so a config value here would be a claim the deployment does not make - the resolver
    overwrites whatever is in the file."""
    config["guardrail"]["content_filters"][0]["output_strength"] = "HIGH"
    assert resolve_guardrail(config)["content_filters"][0]["output_strength"] == "NONE"


def test_generation_resolves_the_cross_region_inference_profile(config):
    """base_model_id and is_inference_profile drive the IAM shape in the stack, so they are
    pinned here rather than inferred from a string test at the call site."""
    generation = resolve_generation(config)
    assert generation["model_id"] == "us.anthropic.claude-sonnet-4-6"
    assert generation["is_inference_profile"] is True
    assert generation["base_model_id"] == "anthropic.claude-sonnet-4-6"
    assert generation["max_tokens"] == 1200
    assert generation["temperature"] == 0.2


def test_bare_model_id_is_not_treated_as_an_inference_profile(config):
    """The other branch: a bare on-demand id needs ONE foundation-model ARN, and granting it
    the profile shape instead would be AccessDenied on every generation."""
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
    """The card contract's numbers, pinned so a later nudge has to be a deliberate edit to a
    test that says so. The length caps are GUARDS against a runaway response, sitting far
    above the length the prompt steers toward: desc was 180, sized to the two-sentence
    editorial target itself, and against a real model that put an ellipsis on nearly every
    card. Length steering lives in the prompt; the caps exist so a runaway response cannot
    ship an essay into a card, and cards.py logs a WARNING when one is hit."""
    cards = resolve_cards(config)
    assert cards["max_cards"] == 4
    assert cards["max_retrieval_results"] == 6
    assert cards["title_max_chars"] == 90
    assert cards["desc_max_chars"] == 600
    assert cards["followup_max_chars"] == 120


def test_card_ceiling_above_the_retrieval_count_is_rejected(config):
    """A ceiling the model cannot reach. It cites one retrieved id per card, so allowing
    more cards than results is arithmetic that reads like a decision."""
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
    """The failure this catches is silent: nothing errors, every field is just truncated to
    a fragment forever, and the prompt faithfully instructs the model to write them that
    way."""
    config["cards"][key] = value
    with pytest.raises(ValueError, match=f"cards.{key}"):
        resolve_cards(config)


def test_missing_cards_block_is_rejected(config):
    del config["cards"]
    with pytest.raises(ValueError, match="missing the `cards` block"):
        resolve_cards(config)


def test_validate_config_covers_the_card_caps(config):
    """Same reason as the chat block: these are read at RUNTIME by the parser and the prompt
    builder, so a bad value fails the build rather than every answer after the deploy."""
    config["cards"]["desc_max_chars"] = 60
    with pytest.raises(ValueError, match="desc_max_chars"):
        validate_config(config)


def test_chat_resolves_the_loop_caps(config):
    """Camp's values, but knobs rather than literals: the iteration cap is what stops a
    runaway tool-use loop inside a 29-second budget."""
    chat = resolve_chat(config)
    assert chat["max_converse_iterations"] == 6
    assert chat["max_history_messages"] == 12


# --- Rejections: guardrail ---------------------------------------------------------------


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
    """Bedrock caps this at 500 characters. It is the text a student actually sees when the
    screen blocks, so a truncation is a broken message, not a cosmetic one."""
    config["guardrail"]["blocked_input_messaging"] = "x" * 501
    with pytest.raises(ValueError, match="at most 500"):
        resolve_guardrail(config)


def test_empty_content_filters_is_rejected(config):
    """A guardrail with no policy is billed on every request and screens nothing."""
    config["guardrail"]["content_filters"] = []
    with pytest.raises(ValueError, match="non-empty list"):
        resolve_guardrail(config)


def test_unknown_filter_type_is_rejected(config):
    """PROMPT_INJECTION is the name everyone reaches for; Bedrock's category is
    PROMPT_ATTACK, and the wrong one is a deploy-time ValidationException."""
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
    """The resolver must not hardcode PROMPT_ATTACK. The project only configures that one,
    but rejecting the rest would be a validator lying about what Bedrock supports."""
    config["guardrail"]["content_filters"] = [
        {"type": "VIOLENCE", "input_strength": "MEDIUM"}
    ]
    assert resolve_guardrail(config)["content_filters"][0]["type"] == "VIOLENCE"


# --- Rejections: generation, retrieval, request, chat ------------------------------------


@pytest.mark.parametrize("temperature", [-0.1, 1.5, "0.2", True])
def test_out_of_range_temperature_is_rejected(config, temperature):
    config["generation"]["temperature"] = temperature
    with pytest.raises(ValueError, match="generation.temperature"):
        resolve_generation(config)


@pytest.mark.parametrize("number_of_results", [0, 101, -1])
def test_number_of_results_outside_bedrocks_range_is_rejected(config, number_of_results):
    """1-100 is Bedrock's own range for Retrieve. Out of range does not fail the deploy - it
    fails every query afterwards, which is the whole reason it is checked at synth."""
    config["retrieval"]["number_of_results"] = number_of_results
    with pytest.raises(ValueError, match="number_of_results"):
        resolve_retrieval(config)


def test_hundred_results_is_accepted(config):
    """Boundary: 100 is the limit, not one under it."""
    config["retrieval"]["number_of_results"] = 100
    assert resolve_retrieval(config)["number_of_results"] == 100


@pytest.mark.parametrize("min_score", [-0.1, 1.1, 35, "0.35"])
def test_min_score_outside_the_normalized_range_is_rejected(config, min_score):
    """The nastiest of these is 35 - a plausible "percent" reading of 0.35. Bedrock scores
    are normalized to 0-1, so it silently discards EVERY chunk and the bot answers from
    nothing, with no error anywhere."""
    config["retrieval"]["min_score"] = min_score
    with pytest.raises(ValueError, match="min_score"):
        resolve_retrieval(config)


def test_missing_chat_block_is_rejected(config):
    """The loop caps have no safe default: without a cap a tool-use loop can spend the whole
    29-second budget on Converse calls."""
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


# --- Rejections: knowledge_base ----------------------------------------------------------


def test_missing_block_is_rejected(config):
    """A whole block gone. Named explicitly, because the alternative is an AttributeError
    somewhere in the stack that says nothing about config.yaml."""
    del config["knowledge_base"]
    with pytest.raises(ValueError, match="missing the `knowledge_base` block"):
        resolve_knowledge_base(config)


def test_block_that_is_not_a_mapping_is_rejected(config):
    """A commented-out block parses as None, not as a missing key."""
    config["chunking"] = None
    with pytest.raises(ValueError, match="missing the `chunking` block"):
        resolve_chunking(config)


def test_dimension_the_embedding_model_does_not_emit_is_rejected(config):
    """768 is a plausible-looking embedding size that Titan v2 never returns. Mismatched
    dimensions fail EVERY ingestion, and the index dimension is immutable - so catching it
    here is the difference between a synth error and replacing an index."""
    config["knowledge_base"]["vector_dimension"] = 768
    with pytest.raises(ValueError, match="not an output size of"):
        resolve_knowledge_base(config)


def test_other_titan_v2_dimensions_are_accepted(config):
    """512 and 256 are real Titan v2 output sizes; the validator must not hardcode 1024."""
    for dimension in (512, 256):
        config["knowledge_base"]["vector_dimension"] = dimension
        assert resolve_knowledge_base(config)["vector_dimension"] == dimension


def test_unknown_embedding_model_falls_back_to_the_s3_vectors_range(config):
    """For a model with no dimension table, don't pretend to know its output sizes - just
    enforce what S3 Vectors itself enforces."""
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
        "double--hyphen",    # at most ONE separator per alphanumeric group
        "has space",
        "has.dot",           # legal in S3 Vectors names, NOT in Bedrock names
        "a" * 101,           # over the 100-group cap
    ],
)
def test_kb_name_violating_bedrock_pattern_is_rejected(config, name):
    """Every one of these synthesizes fine on an L1 construct and fails at deploy."""
    config["knowledge_base"]["name"] = name
    with pytest.raises(ValueError, match="not a valid Bedrock resource name"):
        resolve_knowledge_base(config)


def test_trailing_separator_is_allowed_by_bedrocks_actual_pattern(config):
    """Not a typo. Bedrock's pattern is groups of "alnum + at most one separator", so a
    TRAILING hyphen is legal even though a leading one is not. Pinned because the asymmetry
    reads like a bug and is the kind of thing a later edit would "fix" into over-rejection."""
    config["knowledge_base"]["name"] = "sjsu-navigator-kb-"
    assert resolve_knowledge_base(config)["name"] == "sjsu-navigator-kb-"


def test_kb_name_that_only_breaks_once_the_chunk_suffix_is_folded_in(config):
    """The dangerous case, and the reason the pattern is re-checked after the fold rather
    than only at the source: a KB name that passes on its own but whose DERIVED data source
    name is over Bedrock's limit. 90 chars is fine; +"-s3-fixedsize-600t20p" is 111, past
    the 100-group cap."""
    config["knowledge_base"]["name"] = "a" * 90
    resolve_knowledge_base(config)  # fine by itself
    with pytest.raises(ValueError, match="derived data source name"):
        resolve_data_source_name(config)


# --- Rejections: vector_store ------------------------------------------------------------


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
    """S3 Vectors offers cosine and euclidean only - no dotproduct."""
    config["vector_store"]["distance_metric"] = "dotproduct"
    with pytest.raises(ValueError, match="cosine' or 'euclidean"):
        resolve_vector_store(config)


def test_euclidean_is_accepted(config):
    config["vector_store"]["distance_metric"] = "euclidean"
    assert resolve_vector_store(config)["distance_metric"] == "euclidean"


def test_empty_non_filterable_metadata_keys_is_rejected(config):
    """Dropping these is the trap gav already hit: Bedrock's internal keys are filterable
    by default and blow the filterable-metadata limit, failing every ingestion."""
    config["vector_store"]["non_filterable_metadata_keys"] = []
    with pytest.raises(ValueError, match="non_filterable_metadata_keys must be"):
        resolve_vector_store(config)


def test_more_than_ten_non_filterable_keys_is_rejected(config):
    """S3 Vectors caps this at 10 per index, and the setting is immutable - exceeding it
    means replacing the index (which takes the KB with it), not editing a property."""
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


# --- Rejections: chunking ----------------------------------------------------------------


def test_unknown_chunking_strategy_is_rejected(config):
    config["chunking"]["strategy"] = "SLIDING_WINDOW"
    with pytest.raises(ValueError, match="not a Bedrock chunking strategy"):
        resolve_chunking(config)


def test_real_strategy_the_stack_does_not_wire_is_rejected(config):
    """SEMANTIC is a real Bedrock strategy, but the stack only builds FIXED_SIZE. Accepting
    it would produce a data source whose name says semantic and whose chunking is fixed."""
    config["chunking"]["strategy"] = "SEMANTIC"
    with pytest.raises(ValueError, match="only wires FIXED_SIZE"):
        resolve_chunking(config)


@pytest.mark.parametrize("overlap", [0, 100, -5, 20.5, "20"])
def test_out_of_range_overlap_percentage_is_rejected(config, overlap):
    config["chunking"]["overlap_percentage"] = overlap
    with pytest.raises(ValueError, match="overlap_percentage must be an integer 1-99"):
        resolve_chunking(config)


# --- Rejections: scraper and the crawl list ----------------------------------------------


def test_missing_schedule_cron_is_rejected(config):
    """Failure mode without this: a Lambda nothing ever invokes, so the corpus quietly
    stops refreshing and only shows up later as stale answers."""
    del config["scraper"]["schedule_cron"]
    with pytest.raises(ValueError, match="scraper.schedule_cron"):
        resolve_scraper(config)


def test_unix_style_five_field_cron_is_rejected(config):
    """EventBridge cron takes SIX fields with a '?' in day-of-month or day-of-week; a bare
    5-field UNIX expression is a deploy-time rejection otherwise."""
    config["scraper"]["schedule_cron"] = "30 11 * * *"
    with pytest.raises(ValueError, match=r"cron\(\.\.\.\)"):
        resolve_scraper(config)


def test_missing_crawl_list_file_is_rejected(config):
    """The scraper's only source of URLs. Missing means a run that fetches nothing - and
    whose prune then deletes the entire knowledge base."""
    config["scraper"]["url_list_file"] = "does-not-exist.csv"
    with pytest.raises(ValueError, match="does not exist"):
        resolve_seed_pages(config)


def test_crawl_list_missing_the_section_column_is_rejected(config, tmp_path):
    """`section` reaches the metadata sidecars and drives card deprioritization and
    follow-up buttons. Losing it degrades silently, which is why it is required."""
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
    """Point `scraper.url_list_file` at a tmp_path CSV.

    seed_list_path() joins the value to the REPO ROOT, and joining an absolute path onto a
    root yields the absolute path - so an absolute tmp_path works without loosening the
    resolution rule that keeps the real list at the repo root.
    """
    config["scraper"]["url_list_file"] = str(csv_path)
    return config


# --- Rejections: cors --------------------------------------------------------------------


def test_cors_wildcard_is_rejected(config):
    """The endpoint fans out to paid Bedrock calls; '*' lets any site drive it from its
    visitors' browsers. (CORS is browser-enforced only and is not a security boundary -
    throttling and the Cognito gate are the real caps.)"""
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


# --- validate_config runs the whole file, not just the built sections --------------------


def test_validate_config_catches_an_error_in_an_unbuilt_section(config):
    """The reason validate_config exists: a CORS wildcard is caught now, at synth, even
    though the API section that consumes cors.allow_origins has not been written yet."""
    config["cors"]["allow_origins"] = ["*"]
    with pytest.raises(ValueError, match="must not contain"):
        validate_config(config)


def test_validate_config_covers_the_chat_path_blocks(config):
    """Same reason, one section further on: the values the chat Lambda reads at RUNTIME
    (min_score, the iteration cap) are validated at synth, so a bad one fails the build
    rather than every request after the deploy."""
    config["retrieval"]["min_score"] = 35
    with pytest.raises(ValueError, match="min_score"):
        validate_config(config)


def test_the_converse_deadline_must_sit_under_the_lambda_timeout():
    """The wall-clock cap only works if it fires BEFORE the function is killed. A deadline
    at or past the timeout never fires: the invocation is billed and the student gets a
    gateway 504 carrying no answer at all."""
    from infra.config import CHAT_LAMBDA_TIMEOUT_SECONDS

    config = copy.deepcopy(load_config())
    config["chat"]["converse_deadline_seconds"] = CHAT_LAMBDA_TIMEOUT_SECONDS
    with pytest.raises(ValueError, match="must be less than"):
        resolve_chat(config)


def test_the_configured_converse_deadline_leaves_room_after_the_loop():
    """Not just under the timeout - far enough under that card shaping and serialisation,
    which run after the loop returns, still fit."""
    from infra.config import CHAT_LAMBDA_TIMEOUT_SECONDS

    deadline = resolve_chat(load_config())["converse_deadline_seconds"]
    assert deadline <= CHAT_LAMBDA_TIMEOUT_SECONDS - 5
