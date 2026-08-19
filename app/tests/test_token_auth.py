"""The streaming app's token verification: app/token_auth.py.

THIS IS THE ONE MODULE IN app/ WHOSE CORRECTNESS IS A SECURITY PROPERTY. Every other
transport is handed an identity by something in front of it - API Gateway's native JWT
authorizer, or app/ws_authorizer.py at the socket's handshake. The streaming app is a
Lambda Function URL, which takes no authorizer, so what this file asserts is the whole of
what stands between a stranger and a student's conversation history.

SO IT IS TESTED AGAINST REAL SIGNATURES, not a stubbed library. The suite generates an RSA
keypair, mints tokens with it, and hands the verifier a JWKS client that returns the
matching public key - which is what makes "wrong signature is refused" an assertion about
RS256 rather than about a mock. It is also the reason pyjwt is in requirements-dev.txt: a
stub here would pass every one of these tests while verifying nothing.

WHAT IS NOT TESTED HERE, and where it is instead. The FastAPI route that turns an
Unauthorized into a 401 lives in app/stream_probe.py, which this suite cannot import -
fastapi is in the streaming app's own layer and in no environment CI builds for app/. Its
shape is pinned by the infra suite, which reads the module off disk
(test_the_streaming_app_takes_its_caller_from_the_verified_token_and_never_from_the_body).
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

# The two clients a real deployment has: the browser's, and the eval runner's machine
# client. Both must pass; that is the whole reason this is a list and not an audience.
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
    """Stands in for PyJWKClient, and counts.

    The real client fetches the pool's JWKS over HTTPS and caches by `kid`. What is
    interesting to a test is not the fetch - it is that the verifier asks ONCE per process
    and not once per request, so this records every lookup.
    """

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
    """A Cognito-shaped ACCESS token, signed with the pool's key unless told otherwise.

    The claim set is the one Cognito actually issues: `client_id` and no `aud`, which is
    the quirk the whole verifier is shaped around.
    """
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


# --- what is accepted ---------------------------------------------------------------------


def test_a_valid_access_token_from_the_browser_client_is_the_student_it_names(
    verifier, keypair
):
    """The whole point of the module: a real token becomes a real caller."""
    identity = verifier.identity(access_token(keypair))

    assert identity == Identity(sub=SUB, client_id=WEB_CLIENT_ID)


def test_the_eval_harnesses_machine_client_passes_the_same_door(verifier, keypair):
    """TWO CLIENTS, NOT ONE, and this is the assertion that would fail if somebody
    "tightened" the allowlist to the browser.

    The pool has a browser client and the eval runner's machine client, and both send real
    turns at this route. Pinning a single audience - the obvious-looking version of this
    check - would pass every student and 401 every eval run, which is a failure that only
    shows up on the night somebody runs the harness."""
    identity = verifier.identity(access_token(keypair, client_id=EVAL_CLIENT_ID))

    assert identity == Identity(sub=SUB, client_id=EVAL_CLIENT_ID)


def test_the_token_is_read_off_the_apps_own_header_and_not_authorization(
    verifier, keypair
):
    """ORIGIN ACCESS CONTROL OWNS `Authorization`. CloudFront signs each origin request
    with SigV4 and the signature goes in that header, so a token put there is a token the
    edge overwrites. The app's own header is what survives the hop - and a token in
    `Authorization` must not be a second way in, or the two would disagree the day the edge
    changes."""
    token = access_token(keypair)

    assert verifier.identity_from_headers({AUTH_HEADER_NAME: token}).sub == SUB

    with pytest.raises(Unauthorized):
        verifier.identity_from_headers({"authorization": f"Bearer {token}"})


def test_a_bearer_prefix_is_accepted_and_so_is_a_bare_token(verifier, keypair):
    """The header is ours, so the scheme is decoration - but every client library that has
    ever sent a token writes it, and a 401 caused by six characters of politeness is an
    afternoon nobody gets back."""
    token = access_token(keypair)

    for value in (token, f"Bearer {token}", f"bearer {token}", f"  Bearer  {token} "):
        assert verifier.identity_from_headers({AUTH_HEADER_NAME: value}).sub == SUB


def test_identity_is_the_sub_and_the_client_id_and_nothing_else(verifier, keypair):
    """`sub` IS THE PARTITION KEY (docs/accounts-and-storage.md), so what the verifier
    hands back is what the whole isolation story rests on. A token can carry a username, an
    email, a custom attribute somebody added to the pool last week; none of them come out
    of here, because none of them are an identity this app has ever keyed on."""
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


# --- what is refused ----------------------------------------------------------------------


def test_an_expired_token_is_refused(verifier, keypair):
    """The one rejection that happens to every legitimate caller eventually. Cognito access
    tokens live an hour; a client that does not refresh gets a 401 and goes and gets a new
    one, which is exactly what should happen."""
    now = int(time.time())
    with pytest.raises(Unauthorized):
        verifier.identity(access_token(keypair, exp=now - 1, iat=now - 3600))


def test_a_token_from_another_pool_is_refused(verifier, keypair):
    """WRONG ISSUER, AND THE SIGNATURE IS PERFECTLY GOOD. This is the check that stops a
    token minted by any other Cognito pool in any other AWS account from being a student
    here - the attacker in this scenario owns a pool, which costs nothing, and signs
    whatever `sub` they like with a key they control. Only the issuer says no."""
    with pytest.raises(Unauthorized):
        verifier.identity(
            access_token(
                keypair,
                iss="https://cognito-idp.us-west-2.amazonaws.com/us-west-2_ELSEWHERE",
            )
        )


def test_a_token_signed_by_the_wrong_key_is_refused(verifier, keypair):
    """The forgery this module exists to refuse: every claim is right, the signature is not
    the pool's. It is checked against a SECOND real RSA key rather than a corrupted
    signature, because a mangled byte string can fail for reasons that have nothing to do
    with verification."""
    _, other_private = keypair
    with pytest.raises(Unauthorized):
        verifier.identity(access_token(keypair, _signing_key=other_private))


def test_an_absent_header_is_refused(verifier):
    """No token, no caller. There is no fallback identity, no anonymous mode and no header
    that asserts a `sub` without a token behind it."""
    with pytest.raises(Unauthorized):
        verifier.identity_from_headers({})


def test_a_blank_or_scheme_only_header_is_refused(verifier):
    """The shapes a broken client sends when its token is undefined."""
    for value in ("", "   ", "Bearer", "Bearer ", "bearer   "):
        with pytest.raises(Unauthorized):
            verifier.identity_from_headers({AUTH_HEADER_NAME: value})


def test_an_id_token_for_the_same_client_is_refused(verifier, keypair):
    """THE COGNITO QUIRK, IN THE DIRECTION THAT BITES. An access token carries `client_id`
    and no `aud`, which is why audience verification is off. An ID token carries `aud` and
    no `client_id` - so without the `token_use` check it would sail past a `client_id`
    check that never ran, and an ID token is a different thing with a different lifetime
    that this app has never agreed to accept. The HTTP API's authorizer rejects them by the
    same mechanism."""
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
    """A valid token from this very pool, issued to an app client nobody configured. The
    pool is shared infrastructure - adding a client to it is a console click - so "signed
    by our pool" is not the same claim as "issued to something this app serves"."""
    with pytest.raises(Unauthorized):
        verifier.identity(access_token(keypair, client_id="some-other-app-client"))


def test_a_token_with_no_sub_is_refused(verifier, keypair):
    """There is nothing to key storage on, so there is nothing to serve. A None partition
    key is the failure this refuses to turn into a DynamoDB call."""
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
    """`alg: none`. The classic, and the reason the algorithm list is pinned rather than
    read off the token's own header."""
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
    """ALGORITHM CONFUSION, which is the attack that makes pinning RS256 load-bearing. The
    pool's public key is public by definition; an attacker who signs an HS256 token with it
    as a shared secret gets a token that verifies against the same bytes the verifier
    fetched - unless the verifier refuses to consider HS256 at all."""
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

    # ASSEMBLED BY HAND, because pyjwt refuses to MINT this ("The specified key is an
    # asymmetric key ... and should not be used as an HMAC secret"). An attacker is not
    # using pyjwt to build it, so neither is this test - three base64url segments and an
    # HMAC is the whole of the forgery.
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
    """Whatever a scanner sends. Every failure has to arrive as Unauthorized, because the
    route catches that and nothing else - anything that escapes it is a 500 where a 401
    belongs, and a stack trace where a log line belongs."""
    for value in ("not-a-token", "a.b.c", "..", "eyJhbGciOiJSUzI1NiJ9"):
        with pytest.raises(Unauthorized):
            verifier.identity(value)


def test_an_empty_client_allowlist_admits_nobody(keypair, jwk_client):
    """WHERE A HALF-CONFIGURED DEPLOY LANDS. `client_id not in frozenset()` is false for
    every value, so a verifier built with no clients refuses a token that is otherwise
    perfect. It is the safe direction, and it is asserted rather than assumed because the
    alternative reading - an empty list means "no restriction" - is one somebody could
    write on a tired afternoon."""
    verifier = TokenVerifier(
        region=REGION,
        user_pool_id=USER_POOL_ID,
        allowed_client_ids=set(),
        jwk_client=jwk_client,
    )

    with pytest.raises(Unauthorized):
        verifier.identity(access_token(keypair))


def test_no_rejection_message_carries_any_part_of_the_token(verifier, keypair):
    """NOTHING TOKEN-SHAPED REACHES A LOG LINE. The route logs the exception's own message,
    so the messages are a fixed vocabulary rather than anything derived from the input -
    the same discipline app/ws_authorizer.py keeps for the same reason, one layer down."""
    _, other_private = keypair
    token = access_token(keypair, _signing_key=other_private)

    with pytest.raises(Unauthorized) as refused:
        verifier.identity(token)

    message = str(refused.value)
    assert token not in message
    for segment in token.split("."):
        assert segment not in message, message


# --- the properties that are not about one token ------------------------------------------


def test_verification_makes_no_network_call_per_request(verifier, jwk_client, keypair):
    """THE REQUIREMENT THAT IS ABOUT LATENCY, NOT CORRECTNESS. This sits in front of a turn
    whose loop budget is already 22 seconds, so a JWKS fetch per request would spend a
    student's time on a document that changes about once a year.

    Two halves. The verifier holds ONE key client for the life of the process - asserted
    here by driving many tokens through one verifier and counting - and that client is a
    PyJWKClient built with `cache_keys=True`, which is what makes the second lookup for a
    `kid` free. The second half is asserted next door, where the real client is built."""
    for _ in range(25):
        verifier.identity(access_token(keypair))

    # One lookup per verification is the interface; what must not happen is a NEW client,
    # and therefore a new fetch, per request.
    assert jwk_client.lookups == 25
    assert verifier._jwk_client is jwk_client


class _FakeSSM:
    """Parameter Store, with a script and a counter.

    `values` is a list of what each successive get_parameter should do: a string to
    return, or an exception instance to raise.
    """

    def __init__(self, *values):
        self._values = list(values)
        self.calls = []

    def get_parameter(self, Name):  # noqa: N803 - botocore's own parameter name
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
    """The other half of the sentence above, at the seam where PyJWKClient is constructed.

    `cache_keys=True` is what turns a per-request fetch into a per-container one, and it is
    a keyword argument - the kind of thing that survives a refactor by accident or does not
    survive at all, with no visible difference until the endpoint is under load."""
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
    # And the fetch is bounded, because this one sits in front of a student rather than
    # behind a 10-second authorizer timeout.
    assert kwargs["timeout"] == token_auth._JWKS_TIMEOUT_SECONDS


def test_the_allowlist_is_read_once_per_container_and_not_per_request(
    monkeypatch, unbuilt
):
    """PARAMETER STORE IS A COLD-START READ, NOT A REQUEST-PATH ONE. This sits in front of a
    turn whose loop budget is already 22 seconds; a GetParameter per request would spend a
    student's time on a value that changes only when the stack is redeployed - and a
    redeploy is a new container, which is what makes caching it forever correct."""
    monkeypatch.setattr(token_auth, "PyJWKClient", lambda *a, **k: None)
    ssm = _FakeSSM(f"{WEB_CLIENT_ID},{EVAL_CLIENT_ID}")
    monkeypatch.setattr(token_auth, "_ssm_client", lambda: ssm)

    for _ in range(50):
        assert token_auth.verifier() is not None

    assert ssm.calls == [PARAMETER_NAME]


def test_the_verifier_is_built_from_the_pool_and_clients_the_stack_names(
    monkeypatch, unbuilt
):
    """NAMES FROM THE STACK, NEVER A LITERAL. The pool, its region and both client ids are
    created by the CDK stack; the first two arrive as environment variables and the third
    as the name of the parameter the stack writes them into (which is a dependency cycle
    away from being a variable too - see app/token_auth.py's docstring). A fresh install in
    another account therefore trusts its own pool with nobody editing a file."""
    monkeypatch.setattr(token_auth, "PyJWKClient", lambda *a, **k: None)
    monkeypatch.setattr(
        token_auth,
        "_ssm_client",
        lambda: _FakeSSM(f" {WEB_CLIENT_ID} , {EVAL_CLIENT_ID} ,"),
    )

    built = token_auth.verifier()

    assert built.issuer == ISSUER
    # Whitespace and a trailing comma are what Fn::Join and a hand-edited parameter make.
    assert built.allowed_client_ids == frozenset({WEB_CLIENT_ID, EVAL_CLIENT_ID})


def test_a_transient_parameter_store_failure_is_not_cached(monkeypatch, unbuilt):
    """THE ONE THING A LAZY SINGLETON GETS WRONG BY DEFAULT. A warm Lambda container lives
    for hours, so a verifier that cached "I could not read the parameter" would answer 401
    to every student for the rest of that container's life over one throttled call - and it
    would look exactly like a bad deploy, from a stack that is fine.

    So the failure refuses THIS request, names itself, and the next request tries again."""
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
    """A botocore message can quote the request, so the log line carries the parameter name
    and the exception TYPE and nothing else."""
    monkeypatch.setattr(token_auth, "PyJWKClient", lambda *a, **k: None)
    monkeypatch.setattr(
        token_auth, "_ssm_client", lambda: _FakeSSM(RuntimeError("a secret detail"))
    )

    assert token_auth.verifier() is None
    assert "a secret detail" not in token_auth.verifier_error()
    assert "RuntimeError" in token_auth.verifier_error()


def test_an_empty_parameter_refuses_every_caller(monkeypatch, unbuilt):
    """WHERE A HALF-CONFIGURED DEPLOY LANDS, one layer up from the empty-allowlist test
    above. No clients means no tokens, and it is reported rather than silently building a
    verifier that says no to everything for a reason nobody can see."""
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
    """FAIL CLOSED, AND SAY WHICH VARIABLE. A function that has not been told which pool to
    trust must not be deciding who anybody is, so `verifier()` returns None and the route
    answers 401 to everyone - the opposite of the default that would let a misconfigured
    deploy serve strangers.

    It does not RAISE, and that is deliberate: a module-scope raise would take the whole
    ASGI app down with it, the adapter's readiness poll would fail, and a misconfigured
    pool would be indistinguishable from a broken deploy. The error is held so the
    transport probes keep answering and one log line names the variable to fix."""
    monkeypatch.setattr(token_auth, "PyJWKClient", lambda *a, **k: None)
    monkeypatch.setattr(
        token_auth, "_ssm_client", lambda: _FakeSSM(f"{WEB_CLIENT_ID},{EVAL_CLIENT_ID}")
    )
    monkeypatch.setenv(missing, "")

    assert token_auth.verifier() is None
    assert missing in token_auth.verifier_error()
