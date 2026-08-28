"""The streaming app's token verification: app/token_auth.py.

Tested against real RS256 signatures, not a stubbed library, because a stub here would
pass every one of these tests while verifying nothing.
"""

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import token_auth
from token_auth import AUTH_HEADER_NAME, Identity, TokenVerifier, Unauthorized

REGION = "us-west-2"
USER_POOL_ID = "us-west-2_TESTPOOL"
ISSUER = f"https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}"

# Both must pass, which is the whole reason this is a list and not an audience.
WEB_CLIENT_ID = "web-client-id"
EVAL_CLIENT_ID = "eval-client-id"

SUB = "11111111-2222-3333-4444-555555555555"

# The Parameter Store name the stack builds out of its own stack name.
PARAMETER_NAME = "/SjsuNavigatorStack/streaming/allowed-client-ids"


@pytest.fixture(scope="module")
def keypair():
    """One RSA key for the pool, and one that is not the pool's."""
    return (
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
        rsa.generate_private_key(public_exponent=65537, key_size=2048),
    )


class _FakeJWK:
    def __init__(self, key):
        self.key = key


class _FakeJWKClient:
    """Stands in for PyJWKClient and records every lookup: the count is the assertion."""

    def __init__(self, key):
        self._jwk = _FakeJWK(key)
        self.lookups = 0

    def get_signing_key_from_jwt(self, token):
        self.lookups += 1
        return self._jwk


@pytest.fixture
def public_key(keypair):
    private, _ = keypair
    return private.public_key()


@pytest.fixture
def jwk_client(public_key):
    return _FakeJWKClient(public_key)


@pytest.fixture
def verifier(jwk_client):
    return TokenVerifier(
        region=REGION,
        user_pool_id=USER_POOL_ID,
        allowed_client_ids={WEB_CLIENT_ID, EVAL_CLIENT_ID},
        jwk_client=jwk_client,
    )


def access_token(keypair, **overrides):
    """`client_id` and no `aud`, which is the quirk the whole verifier is shaped around."""
    private, _ = keypair
    key = overrides.pop("_signing_key", None) or private
    now = int(time.time())
    claims = {
        "sub": SUB,
        "iss": ISSUER,
        "client_id": WEB_CLIENT_ID,
        "token_use": "access",
        "scope": "aws.cognito.signin.user.admin",
        "auth_time": now,
        "iat": now,
        "exp": now + 3600,
        "jti": "00000000-0000-0000-0000-00000000000a",
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256")


def test_a_valid_access_token_from_the_browser_client_is_the_student_it_names(
    verifier, keypair
):
    """The whole point of the module: a real token becomes a real caller."""
    identity = verifier.identity(access_token(keypair))

    assert identity == Identity(sub=SUB, client_id=WEB_CLIENT_ID)


def test_the_eval_harnesses_machine_client_passes_the_same_door(verifier, keypair):
    """Pinning a single audience would pass every student and 401 every eval run."""
    identity = verifier.identity(access_token(keypair, client_id=EVAL_CLIENT_ID))

    assert identity == Identity(sub=SUB, client_id=EVAL_CLIENT_ID)


def test_the_token_is_read_off_the_apps_own_header_and_not_authorization(
    verifier, keypair
):
    """CloudFront's SigV4 signature owns `Authorization`, so a token there is overwritten."""
    token = access_token(keypair)

    assert verifier.identity_from_headers({AUTH_HEADER_NAME: token}).sub == SUB

    with pytest.raises(Unauthorized):
        verifier.identity_from_headers({"authorization": f"Bearer {token}"})


def test_a_bearer_prefix_is_accepted_and_so_is_a_bare_token(verifier, keypair):
    """The header is ours, so the scheme is decoration, but every client library writes it."""
    token = access_token(keypair)

    for value in (token, f"Bearer {token}", f"bearer {token}", f"  Bearer  {token} "):
        assert verifier.identity_from_headers({AUTH_HEADER_NAME: value}).sub == SUB


def test_identity_is_the_sub_and_the_client_id_and_nothing_else(verifier, keypair):
    """`sub` is the partition key, and nothing else a token carries is an identity here."""
    identity = verifier.identity(
        access_token(
            keypair,
            username="somebody",
            email="somebody@sjsu.edu",
            **{"custom:userId": "a-different-person"},
        )
    )

    assert identity == Identity(sub=SUB, client_id=WEB_CLIENT_ID)
    assert [f.name for f in identity.__dataclass_fields__.values()] == [
        "sub",
        "client_id",
    ]


def test_an_expired_token_is_refused(verifier, keypair):
    """The one rejection every legitimate caller eventually gets: the token lives an hour."""
    now = int(time.time())
    with pytest.raises(Unauthorized):
        verifier.identity(access_token(keypair, exp=now - 1, iat=now - 3600))


def test_a_token_from_another_pool_is_refused(verifier, keypair):
    """The signature is perfectly good: anyone can own a pool, so only the issuer says no."""
    with pytest.raises(Unauthorized):
        verifier.identity(
            access_token(
                keypair,
                iss="https://cognito-idp.us-west-2.amazonaws.com/us-west-2_ELSEWHERE",
            )
        )


def test_a_token_signed_by_the_wrong_key_is_refused(verifier, keypair):
    """A second real key rather than a mangled one, which could fail for other reasons."""
    _, other_private = keypair
    with pytest.raises(Unauthorized):
        verifier.identity(access_token(keypair, _signing_key=other_private))


def test_an_absent_header_is_refused(verifier):
    """No fallback identity, no anonymous mode, no header that asserts a `sub`."""
    with pytest.raises(Unauthorized):
        verifier.identity_from_headers({})


def test_a_blank_or_scheme_only_header_is_refused(verifier):
    """The shapes a broken client sends when its token is undefined."""
    for value in ("", "   ", "Bearer", "Bearer ", "bearer   "):
        with pytest.raises(Unauthorized):
            verifier.identity_from_headers({AUTH_HEADER_NAME: value})


def test_an_id_token_for_the_same_client_is_refused(verifier, keypair):
    """An ID token carries `aud` and no `client_id`, so without `token_use` it sails past."""
    private, _ = keypair
    now = int(time.time())
    id_token = jwt.encode(
        {
            "sub": SUB,
            "iss": ISSUER,
            "aud": WEB_CLIENT_ID,
            "token_use": "id",
            "client_id": WEB_CLIENT_ID,
            "iat": now,
            "exp": now + 3600,
        },
        private,
        algorithm="RS256",
    )

    with pytest.raises(Unauthorized):
        verifier.identity(id_token)


def test_a_token_from_a_client_that_is_not_ours_is_refused(verifier, keypair):
    """Adding a client to the pool is a console click, so the pool's signature is not enough."""
    with pytest.raises(Unauthorized):
        verifier.identity(access_token(keypair, client_id="some-other-app-client"))


def test_a_token_with_no_sub_is_refused(verifier, keypair):
    """Nothing to key storage on, so a None partition key never reaches DynamoDB."""
    private, _ = keypair
    now = int(time.time())
    token = jwt.encode(
        {
            "iss": ISSUER,
            "client_id": WEB_CLIENT_ID,
            "token_use": "access",
            "iat": now,
            "exp": now + 3600,
        },
        private,
        algorithm="RS256",
    )

    with pytest.raises(Unauthorized):
        verifier.identity(token)


def test_an_unsigned_token_is_refused(verifier):
    """`alg: none`, and the reason the algorithm list is pinned rather than read off it."""
    now = int(time.time())
    unsigned = jwt.encode(
        {
            "sub": SUB,
            "iss": ISSUER,
            "client_id": WEB_CLIENT_ID,
            "token_use": "access",
            "iat": now,
            "exp": now + 3600,
        },
        key=None,
        algorithm="none",
    )

    with pytest.raises(Unauthorized):
        verifier.identity(unsigned)


def test_a_symmetric_token_signed_with_the_public_key_is_refused(verifier, public_key):
    """Algorithm confusion: the public key as an HMAC secret verifies, unless HS256 is refused."""
    from cryptography.hazmat.primitives import serialization

    import base64
    import hashlib
    import hmac
    import json

    pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    now = int(time.time())

    # Assembled by hand, because pyjwt refuses to mint it. An attacker would not use pyjwt either.
    def _segment(payload):
        return base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).rstrip(b"=")

    signing_input = b".".join(
        [
            _segment({"alg": "HS256", "typ": "JWT"}),
            _segment(
                {
                    "sub": SUB,
                    "iss": ISSUER,
                    "client_id": WEB_CLIENT_ID,
                    "token_use": "access",
                    "iat": now,
                    "exp": now + 3600,
                }
            ),
        ]
    )
    signature = base64.urlsafe_b64encode(
        hmac.new(pem, signing_input, hashlib.sha256).digest()
    ).rstrip(b"=")
    forged = (signing_input + b"." + signature).decode()

    with pytest.raises(Unauthorized):
        verifier.identity(forged)


def test_garbage_is_refused_rather_than_raising_something_else(verifier):
    """The route catches Unauthorized and nothing else, so anything escaping it is a 500."""
    for value in ("not-a-token", "a.b.c", "..", "eyJhbGciOiJSUzI1NiJ9"):
        with pytest.raises(Unauthorized):
            verifier.identity(value)


def test_an_empty_client_allowlist_admits_nobody(keypair, jwk_client):
    """Asserted, not assumed: the other reading of an empty list is "no restriction"."""
    verifier = TokenVerifier(
        region=REGION,
        user_pool_id=USER_POOL_ID,
        allowed_client_ids=set(),
        jwk_client=jwk_client,
    )

    with pytest.raises(Unauthorized):
        verifier.identity(access_token(keypair))


def test_no_rejection_message_carries_any_part_of_the_token(verifier, keypair):
    """The route logs the exception's own message, so the vocabulary has to be fixed."""
    _, other_private = keypair
    token = access_token(keypair, _signing_key=other_private)

    with pytest.raises(Unauthorized) as refused:
        verifier.identity(token)

    message = str(refused.value)
    assert token not in message
    for segment in token.split("."):
        assert segment not in message, message


def test_verification_makes_no_network_call_per_request(verifier, jwk_client, keypair):
    """Latency, not correctness: a JWKS fetch per request spends a student's time."""
    for _ in range(25):
        verifier.identity(access_token(keypair))

    # One lookup per verification is fine; a new client, and so a new fetch, is not.
    assert jwk_client.lookups == 25
    assert verifier._jwk_client is jwk_client


class _FakeSSM:
    """`values` is what each successive get_parameter does: a string, or an exception to raise."""

    def __init__(self, *values):
        self._values = list(values)
        self.calls = []

    def get_parameter(self, Name):  # noqa: N803 (botocore's own parameter name)
        self.calls.append(Name)
        value = self._values.pop(0) if len(self._values) > 1 else self._values[0]
        if isinstance(value, Exception):
            raise value
        return {"Parameter": {"Value": value}}


@pytest.fixture
def unbuilt(monkeypatch):
    """A module with no verifier built yet, and no way to reach AWS or the network."""
    monkeypatch.setattr(token_auth, "_VERIFIER", None)
    monkeypatch.setattr(token_auth, "_VERIFIER_ERROR", None)
    monkeypatch.setenv("COGNITO_REGION", REGION)
    monkeypatch.setenv("USER_POOL_ID", USER_POOL_ID)
    monkeypatch.setenv("ALLOWED_CLIENT_IDS_PARAMETER", PARAMETER_NAME)


def test_the_real_key_client_is_built_once_and_caches(monkeypatch, unbuilt):
    """`cache_keys=True` turns a per-request fetch into a per-container one, silently."""
    built = []

    class _Recorder:
        def __init__(self, uri, **kwargs):
            built.append((uri, kwargs))

    monkeypatch.setattr(token_auth, "PyJWKClient", _Recorder)
    monkeypatch.setattr(
        token_auth, "_ssm_client", lambda: _FakeSSM(f"{WEB_CLIENT_ID},{EVAL_CLIENT_ID}")
    )

    first = token_auth.verifier()
    second = token_auth.verifier()

    assert first is second, "a verifier per call is a JWKS fetch per call"
    assert len(built) == 1, built
    uri, kwargs = built[0]
    assert uri == f"{ISSUER}/.well-known/jwks.json"
    assert kwargs["cache_keys"] is True
    # And the fetch is bounded, because it sits in front of a student.
    assert kwargs["timeout"] == token_auth._JWKS_TIMEOUT_SECONDS


def test_the_allowlist_is_read_once_per_container_and_not_per_request(
    monkeypatch, unbuilt
):
    """A redeploy is a new container, which is what makes caching this forever correct."""
    monkeypatch.setattr(token_auth, "PyJWKClient", lambda *a, **k: None)
    ssm = _FakeSSM(f"{WEB_CLIENT_ID},{EVAL_CLIENT_ID}")
    monkeypatch.setattr(token_auth, "_ssm_client", lambda: ssm)

    for _ in range(50):
        assert token_auth.verifier() is not None

    assert ssm.calls == [PARAMETER_NAME]


def test_the_verifier_is_built_from_the_pool_and_clients_the_stack_names(
    monkeypatch, unbuilt
):
    """Names from the stack, never a literal: a fresh install trusts its own pool."""
    monkeypatch.setattr(token_auth, "PyJWKClient", lambda *a, **k: None)
    monkeypatch.setattr(
        token_auth,
        "_ssm_client",
        lambda: _FakeSSM(f" {WEB_CLIENT_ID} , {EVAL_CLIENT_ID} ,"),
    )

    built = token_auth.verifier()

    assert built.issuer == ISSUER
    # What Fn::Join and a hand-edited parameter leave behind.
    assert built.allowed_client_ids == frozenset({WEB_CLIENT_ID, EVAL_CLIENT_ID})


def test_a_transient_parameter_store_failure_is_not_cached(monkeypatch, unbuilt):
    """A cached failure would answer 401 for the life of a warm container, over one throttle."""
    monkeypatch.setattr(token_auth, "PyJWKClient", lambda *a, **k: None)
    ssm = _FakeSSM(RuntimeError("throttled"), f"{WEB_CLIENT_ID},{EVAL_CLIENT_ID}")
    monkeypatch.setattr(token_auth, "_ssm_client", lambda: ssm)

    assert token_auth.verifier() is None
    assert PARAMETER_NAME in token_auth.verifier_error()

    recovered = token_auth.verifier()

    assert recovered is not None
    assert recovered.allowed_client_ids == frozenset({WEB_CLIENT_ID, EVAL_CLIENT_ID})
    assert len(ssm.calls) == 2


def test_the_failure_message_never_quotes_what_parameter_store_said(monkeypatch, unbuilt):
    """A botocore message can quote the request, so only the name and the type are logged."""
    monkeypatch.setattr(token_auth, "PyJWKClient", lambda *a, **k: None)
    monkeypatch.setattr(
        token_auth, "_ssm_client", lambda: _FakeSSM(RuntimeError("a secret detail"))
    )

    assert token_auth.verifier() is None
    assert "a secret detail" not in token_auth.verifier_error()
    assert "RuntimeError" in token_auth.verifier_error()


def test_an_empty_parameter_refuses_every_caller(monkeypatch, unbuilt):
    """No clients means no tokens, and it is reported rather than silently refusing everyone."""
    monkeypatch.setattr(token_auth, "PyJWKClient", lambda *a, **k: None)
    monkeypatch.setattr(token_auth, "_ssm_client", lambda: _FakeSSM("  ,  ,"))

    assert token_auth.verifier() is None
    assert PARAMETER_NAME in token_auth.verifier_error()


@pytest.mark.parametrize(
    "missing", ["COGNITO_REGION", "USER_POOL_ID", "ALLOWED_CLIENT_IDS_PARAMETER"]
)
def test_a_missing_variable_refuses_every_caller_and_names_itself(
    monkeypatch, unbuilt, missing
):
    """Fail closed and name the variable. Held rather than raised, so the probes keep answering."""
    monkeypatch.setattr(token_auth, "PyJWKClient", lambda *a, **k: None)
    monkeypatch.setattr(
        token_auth, "_ssm_client", lambda: _FakeSSM(f"{WEB_CLIENT_ID},{EVAL_CLIENT_ID}")
    )
    monkeypatch.setenv(missing, "")

    assert token_auth.verifier() is None
    assert missing in token_auth.verifier_error()
