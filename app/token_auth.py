"""Who the caller is on the streaming app, verified in this process.

WHY THE APP VERIFIES ITS OWN TOKEN. The other transport in this repo is handed an
identity: `POST /chat` reads claims API Gateway's native JWT authorizer already checked.
The streaming app has nothing. It is a Lambda Function URL with AuthType AWS_IAM, reached through the
site's CloudFront distribution with origin access control, and the request that arrives
carries the EDGE's IAM identity - `requestContext.authorizer.iam`, a CloudFront service
principal - not a student's claims. A Function URL takes no authorizer, so there is
nowhere else to put this: the choice is verify here or serve nobody.

THE TOKEN RIDES ITS OWN HEADER, and that is forced rather than preferred. OAC signs each
origin request with SigV4 and the signature lives in `Authorization`, so a token in that
header is a token CloudFront overwrites on its way past. The behaviour forwards every
other viewer header (AllViewerExceptHostHeader), so a header of our own arrives intact.
AUTH_HEADER_NAME below is the only place its name is written - the same treatment
`EDGE_PATH_PREFIX` gets in app/streaming_app.py, and for the same reason: a second
spelling is a 401 that synthesizes clean, deploys clean and is nobody's fault. The browser
is the one place the name is written in another language, and an infra test reads the two
off disk and compares them.

THE ACCESS TOKEN, NOT THE ID TOKEN, and `client_id` rather than `aud`. A Cognito access
token carries no `aud` claim at all - it carries `client_id` - so audience verification
would reject every token this pool issues, which is why `verify_aud` is off here and why
the HTTP API's authorizer uses an audience
ALLOWLIST. The quirk cuts both ways: an ID token DOES carry `aud` and no `client_id`, so
without the `token_use` check an ID token would sail past a `client_id` check that never
ran on it. Both directions are checked.

TWO CLIENTS PASS, NOT ONE. The pool has a browser client and the eval harness's machine
client, and both send real turns at this endpoint. Pinning a single audience would pass the
students and fail every eval run, so the check is membership in a list the stack builds
from both client ids.

IDENTITY IS `sub` AND NOTHING ELSE. It is the DynamoDB partition key
(docs/accounts-and-storage.md), which is the whole isolation story: a caller cannot address
another caller's data because the key is not something a caller says. `client_id` comes
back beside it for the rate limit's exemption list and is never an identity.

FAIL CLOSED, WITH NO WAY ROUND IT. There is no bypass flag, no header that asserts a `sub`
without a token behind it, and no development mode. A missing variable, an unparseable
token, a wrong signature and an absent header are one outcome: Unauthorized, which the
route answers 401. An empty client list admits nobody - `client_id not in frozenset()` is
false for every value - which is the safe direction for a half-configured deploy.

NO NETWORK CALL PER REQUEST. Two things are fetched, both once per container and neither
in the request path after that. PyJWKClient is built ONCE and caches signing keys by
`kid`, so the first verification on a cold container fetches the pool's JWKS and every one
after it verifies from memory. The client allowlist is read once from Parameter Store on
the same cold start and held beside it. That matters more here than on `$connect`: this
sits in front of a turn whose loop budget is already 22 seconds.

WHY THE ALLOWLIST COMES FROM PARAMETER STORE AND NOT AN ENVIRONMENT VARIABLE. It is the
one value in this module that cannot be a Lambda environment variable, and the reason is
in the stack rather than in here: the browser's app client carries the CloudFront domain
as its OAuth callback, the distribution serves this function's URL on /api/*, and putting
the client id in this function's environment closes that loop into a CloudFormation
dependency cycle CDK refuses to synth (infra_stack.py, THE CLIENT ALLOWLIST). The ids are
the only value in the loop read by CODE rather than by CloudFormation, so they are the
only one that can be deferred past deploy. The function is handed the parameter's NAME -
a string assembled from pseudo-parameters, which references nothing - and reads it here.

A FAILED READ IS NOT CACHED, and that is deliberate: a verifier held back by one transient
Parameter Store fault would answer 401 to every caller for the life of the container,
which on a warm Lambda is hours. The failure refuses THIS request and the next one tries
again. The success is cached forever, because the allowlist changes only when the stack is
redeployed and a redeploy is a new container.

THIS USED TO BE THE THIRD IMPLEMENTATION OF ONE DECISION and is now the second. The
socket's `$connect` authorizer ran the same five checks at the handshake; it existed
because a WebSocket API accepts no native JWT authorizer, and it went with the socket.
What is left is this and API Gateway's own, which shares this pool and this pair of
clients because a stack with two answers to "whose tokens?" is a stack where one of them
is wrong.

NOTHING TOKEN-SHAPED IS LOGGED. Every rejection raises Unauthorized carrying one of the
fixed strings below, and callers log that string rather than the exception chain. The
token arrives on a header rather than in a URL, which makes this a weaker constraint than
the one the socket's authorizer kept - it is kept anyway, because the difference is only
which log a token would land in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import boto3
import jwt
from jwt import PyJWKClient

# THE HEADER, SPELLED ONCE - see the module docstring. Lower case because HTTP header names
# are case-insensitive and every layer between the browser and this process lower-cases
# them anyway (a Function URL event's `headers`, and Starlette's own case-insensitive
# mapping); one canonical spelling means a lookup that never has to guess.
AUTH_HEADER_NAME = "x-sjsu-authorization"

# Accepted with or without it. The header is ours, so the scheme is decoration rather than
# contract - but every client library that has ever sent a bearer token writes it, and a
# 401 caused by six characters of politeness is the kind of thing that costs an afternoon.
_BEARER_PREFIX = "bearer "

# RS256 AND NOTHING ELSE. It is what Cognito signs with, and pinning it is what stops the
# two classic confusions: `alg: none`, and an HS256 token whose "signature" is a MAC over
# the public key the verifier just fetched.
_ALGORITHMS = ["RS256"]

# The claims a token must actually carry. Absence is a rejection at decode time rather than
# a None that has to be noticed further down.
_REQUIRED_CLAIMS = ["exp", "iss", "sub", "token_use", "client_id"]

# The environment the stack sets. The first two are the same names the $connect authorizer
# reads; the third names a Parameter Store parameter rather than carrying the ids, for the
# dependency-cycle reason in the module docstring.
_REGION_VAR = "COGNITO_REGION"
_USER_POOL_VAR = "USER_POOL_ID"
_CLIENT_IDS_PARAMETER_VAR = "ALLOWED_CLIENT_IDS_PARAMETER"

# A bound on the ONE network call this makes, and it is not PyJWKClient's default of 30
# seconds. The $connect authorizer can leave that alone because its function times out at
# 10; this one has a 60-second function and a 22-second turn budget behind it, so a JWKS
# endpoint that hangs would hold a student's request for half a minute before answering
# 401. Five seconds is far outside any healthy fetch of a two-key document.
_JWKS_TIMEOUT_SECONDS = 5


class Unauthorized(Exception):
    """Every rejection, whatever caused it. The route answers all of them 401.

    ONE EXCEPTION AND NO SUBCLASSES on purpose: the caller is told "no" and not which of
    the checks said so. A 401 that distinguishes "expired" from "wrong signature" from "not
    one of our clients" is an oracle for anyone probing the endpoint, and the distinction
    is worth nothing to a browser that has to do the same thing either way - go and get a
    fresh token. The message is for our logs.
    """


@dataclass(frozen=True)
class Identity:
    """The two things a verified token is allowed to say about its caller.

    `sub` is the identity and the partition key. `client_id` is which app client issued the
    token, whose ONE use is the daily cap's exemption list (app/turn.py); nothing keys
    storage on it.
    """

    sub: str
    client_id: str


def token_from_headers(headers) -> str:
    """The bearer token out of AUTH_HEADER_NAME, or a raise.

    `headers` is anything with a `.get`; Starlette's mapping is case-insensitive, and a
    plain dict is looked up under the canonical lower-case name.
    """
    raw = headers.get(AUTH_HEADER_NAME)
    if not isinstance(raw, str) or not raw.strip():
        raise Unauthorized("no token")
    value = raw.strip()
    if value.lower().startswith(_BEARER_PREFIX):
        value = value[len(_BEARER_PREFIX) :].strip()
    if not value:
        raise Unauthorized("no token")
    return value


class TokenVerifier:
    """One pool, one issuer, one client allowlist, and a JWKS client that caches.

    Built from the environment by `verifier()` below and held for the life of the
    container. It takes its `jwk_client` as an argument so the suite can verify against a
    key it generated rather than against Cognito, which is the only way these checks can be
    tested at all without a network and an account.
    """

    def __init__(self, *, region: str, user_pool_id: str, allowed_client_ids, jwk_client=None):
        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        # A frozenset, and empty is a legitimate value that admits nobody.
        self.allowed_client_ids = frozenset(allowed_client_ids)
        self._jwk_client = jwk_client or PyJWKClient(
            f"{self.issuer}/.well-known/jwks.json",
            cache_keys=True,
            timeout=_JWKS_TIMEOUT_SECONDS,
        )

    def identity_from_headers(self, headers) -> Identity:
        """The caller, out of the request's own header. Raises Unauthorized."""
        return self.identity(token_from_headers(headers))

    def identity(self, token: str) -> Identity:
        """The caller, out of one access token. Raises Unauthorized.

        The signature, the issuer and the expiry are PyJWT's; `token_use` and `client_id`
        are checked by hand for the Cognito reasons in the module docstring. Every failure
        below the first line is folded into one Unauthorized: pyjwt's own exception types
        are precise, and passing that precision on would be building the oracle Unauthorized
        exists not to be.
        """
        try:
            signing_key = self._jwk_client.get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=_ALGORITHMS,
                issuer=self.issuer,
                # A Cognito access token carries no `aud`; `client_id` is checked below.
                options={
                    "verify_aud": False,
                    "verify_exp": True,
                    "verify_iss": True,
                    "verify_signature": True,
                    "require": _REQUIRED_CLAIMS,
                },
            )
        except Exception as exc:
            # Bare message, no chain: see the module docstring on logging.
            raise Unauthorized("the token did not verify") from exc

        if claims.get("token_use") != "access":
            raise Unauthorized("not an access token")

        client_id = claims.get("client_id")
        if client_id not in self.allowed_client_ids:
            raise Unauthorized("client_id is not in the allowlist")

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.strip():
            raise Unauthorized("no sub claim")

        return Identity(sub=subject.strip(), client_id=client_id)


_SSM_CLIENT = None
_VERIFIER = None
_VERIFIER_ERROR = None


def _ssm_client():
    """The Parameter Store client, built once per container.

    Its own client rather than one shared with the turn's boto3 clients: those are built
    with a read timeout sized for a model call, and this is one 200-byte read that must not
    be allowed to sit in front of a student for twenty-five seconds. The default timeouts
    are already tighter than that, and the call happens once.
    """
    global _SSM_CLIENT
    if _SSM_CLIENT is None:
        _SSM_CLIENT = boto3.client("ssm")
    return _SSM_CLIENT


def _client_ids_from(parameter_name):
    """The allowlist out of Parameter Store, as a set. Raises on anything unusable."""
    value = _ssm_client().get_parameter(Name=parameter_name)["Parameter"]["Value"]
    return frozenset(part.strip() for part in value.split(",") if part.strip())


def verifier():
    """The process's verifier, built once. None when it cannot be built.

    LAZY, FOR THE REASON app/streaming_app.py's settings are lazy. A raise at module scope
    would take the whole ASGI app down with it - the adapter's readiness poll would fail,
    the Function URL would answer an opaque 502, and a misconfigured pool would look
    exactly like a broken deploy. Held as an error instead, so the transport probes keep
    answering and the chat route refuses every caller and says why in ONE log line.

    Refusing every caller IS the failure mode here, and it is the right one: this returns
    None only when the function has not been told which pool to trust or cannot read which
    clients count, and a function in either state must not be deciding who anybody is.

    THE SUCCESS IS CACHED AND THE FAILURE IS NOT. A verifier built once is a JWKS fetch and
    a Parameter Store read once; a FAILURE cached the same way would turn one transient
    fault into hours of 401s on a warm container, so the error is reported and the next
    request tries again.
    """
    global _VERIFIER, _VERIFIER_ERROR
    if _VERIFIER is not None:
        return _VERIFIER

    region = (os.environ.get(_REGION_VAR) or "").strip()
    user_pool_id = (os.environ.get(_USER_POOL_VAR) or "").strip()
    parameter_name = (os.environ.get(_CLIENT_IDS_PARAMETER_VAR) or "").strip()

    missing = [
        name
        for name, value in (
            (_REGION_VAR, region),
            (_USER_POOL_VAR, user_pool_id),
            (_CLIENT_IDS_PARAMETER_VAR, parameter_name),
        )
        if not value
    ]
    if missing:
        _VERIFIER_ERROR = (
            f"{', '.join(missing)} is not set, so no token can be verified and every "
            "caller is refused. The CDK stack sets all three from the user pool it "
            "creates; an unset value means the function was deployed outside the stack "
            "or the stack's environment wiring changed without updating app/token_auth.py."
        )
        return None

    try:
        client_ids = _client_ids_from(parameter_name)
    except Exception as exc:
        # The NAME and the exception TYPE, never the exception's own text: a botocore
        # message can quote the request. Not cached - see the docstring.
        _VERIFIER_ERROR = (
            f"{parameter_name} could not be read ({type(exc).__name__}), so no token can "
            "be verified and every caller is refused until it can."
        )
        return None

    if not client_ids:
        _VERIFIER_ERROR = (
            f"{parameter_name} is empty, so no app client's tokens are accepted. The "
            "stack writes both client ids into it at deploy."
        )
        return None

    _VERIFIER = TokenVerifier(
        region=region, user_pool_id=user_pool_id, allowed_client_ids=client_ids
    )
    _VERIFIER_ERROR = None
    return _VERIFIER


def verifier_error():
    """Why `verifier()` returned None, for the one log line that reports it."""
    return _VERIFIER_ERROR
