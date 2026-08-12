"""The $connect authorizer for the WebSocket streaming API.

WHY THIS FILE EXISTS AT ALL. The HTTP API gates its routes with API Gateway's NATIVE JWT
authorizer - no code, no cold start, none of our logic in the auth decision. A WebSocket
API has no such thing: the only authorizer type it accepts is a REQUEST (Lambda)
authorizer, and it may only be attached to `$connect`. So this module re-implements, as
faithfully as it can, exactly what the HTTP API's authorizer already does for POST /chat:

    signature (against the pool's JWKS), issuer, expiry, and client_id against an
    allowlist of app clients.

Attached to `$connect` ALONE, which is not a limitation to work around - it is the model.
A WebSocket connection is authorized once, at the handshake, and every frame after that
rides the connection that handshake opened. API Gateway carries this function's `context`
onto every later route invocation ($default and $disconnect included, verified against a
deployed probe), which is what lets the message route read `sub` from a validated token
rather than from anything the client typed.

THE TOKEN ARRIVES IN THE QUERY STRING, and that is a measured decision rather than a
preference (docs/build-plan.md). The better-looking option is to carry it in
`Sec-WebSocket-Protocol`, keeping it out of URLs entirely - and API Gateway does accept
that header as an identity source. It just never echoes the subprotocol back in its 101
response, and RFC 6455 says a client whose requested subprotocol is not echoed must fail
the connection. Probed against a real deployed WebSocket API: the 101 came back with no
`Sec-WebSocket-Protocol` header, and both Chrome 151 and Node's WHATWG WebSocket fired
`error` and never opened. The query string is what is left.

WHAT THAT COSTS, AND WHAT PAYS IT BACK. A token in a URL is a token that can end up in a
log. This function is the first place it could: the authorizer event carries
`queryStringParameters.token` in full, so NOTHING HERE MAY EVER LOG THE EVENT, the token,
or anything derived from it but the claims. That is a rule about this file, it is why
there is no debug logging in it at all, and test_the_authorizer_never_logs_the_token pins
it. The stack configures no access logging on the WebSocket API, so there is no access log
for it to land in either.

A FAILURE IS A RAISE, NOT A DENY POLICY. Raising makes API Gateway answer the handshake
401; returning an explicit Deny makes it 403. 401 is the honest answer to a bad token, and
it is the same status the HTTP API's authorizer returns for one.
"""

import os

import jwt
from jwt import PyJWKClient

# Identity, from the stack. No defaults, for the same reason app/settings.py has none on
# its wiring: an authorizer that cannot name its user pool would be an authorizer
# validating tokens against whatever a typo pointed at.
_REGION = os.environ["COGNITO_REGION"]
_USER_POOL_ID = os.environ["USER_POOL_ID"]

# The app clients whose tokens are accepted, comma separated - the SAME allowlist the HTTP
# API's authorizer carries as its `jwt_audience`. Empty admits nobody, which is the safe
# direction for a misconfigured deploy.
_ALLOWED_CLIENT_IDS = frozenset(
    part.strip() for part in os.environ.get("ALLOWED_CLIENT_IDS", "").split(",") if part.strip()
)

_ISSUER = f"https://cognito-idp.{_REGION}.amazonaws.com/{_USER_POOL_ID}"

# Built once per container. PyJWKClient fetches the pool's JWKS over https and caches the
# signing keys, so a warm container validates without a network call - which matters on
# $connect, where this sits directly in front of a student waiting for a socket.
_JWK_CLIENT = PyJWKClient(f"{_ISSUER}/.well-known/jwks.json", cache_keys=True)


class Unauthorized(Exception):
    """Raised for every rejection. API Gateway turns it into a 401 on the handshake."""


def _token_from(event):
    """The bearer token out of the query string, or a raise.

    This has to agree letter for letter with the authorizer's declared identity source
    (`route.request.querystring.token`). If it did not, the mismatch would not read as a
    bug: an identity source that is ABSENT from the request means API Gateway never
    invokes this function at all, so the failure arrives as a handshake rejection with
    nothing in these logs to explain it.
    """
    params = event.get("queryStringParameters") or {}
    token = params.get("token")
    if not isinstance(token, str) or not token.strip():
        raise Unauthorized("no token")
    return token.strip()


def _claims(token):
    """The validated claims, or a raise. Everything the HTTP API's authorizer checks.

    `verify_aud` is OFF and `client_id` is checked by hand, which is not a shortcut - it
    is the same documented Cognito quirk the HTTP API authorizer's audience list relies
    on. A Cognito ACCESS token carries no `aud` claim; it carries `client_id`. Turning
    audience verification on would therefore reject every token this app issues.

    `token_use` is checked because that quirk cuts both ways: an ID token DOES carry
    `aud`, so without this line an ID token for the same client would sail through a
    client_id check that never ran. The HTTP API rejects ID tokens by the same mechanism
    (their `aud` is validated against the audience list and their `client_id` is absent),
    and this path has to reach the same answer.
    """
    signing_key = _JWK_CLIENT.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=_ISSUER,
        # Signature, expiry and issuer are all verified; only the audience check is off,
        # and only because a Cognito access token has no audience to check.
        options={"verify_aud": False, "verify_exp": True, "verify_signature": True},
    )

    if claims.get("token_use") != "access":
        raise Unauthorized("not an access token")

    client_id = claims.get("client_id")
    if client_id not in _ALLOWED_CLIENT_IDS:
        raise Unauthorized("client_id is not in the audience allowlist")

    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise Unauthorized("no sub claim")

    return subject.strip(), client_id


def lambda_handler(event, context):
    """Allow or 401 the handshake, and hand `sub` down to every route on the connection.

    THE CONTEXT IS THE POINT. `sub` and `client_id` go out in the authorizer's `context`
    map, and API Gateway attaches that to the event of every subsequent route on this
    connection. That is the only reason app/streaming.py can key a DynamoDB partition on
    the caller: the value came out of a token this function verified, not out of a frame
    the client sent.

    Values in `context` must be strings - API Gateway silently drops other types - so the
    two claims travel as the strings they already are.
    """
    try:
        subject, client_id = _claims(_token_from(event))
    except Exception as exc:
        # Deliberately bare. The exception may carry a fragment of the token (PyJWT's
        # messages do not, but a future library's might), and this function's whole
        # discipline is that nothing token-shaped reaches a log line.
        raise Unauthorized("unauthorized") from exc

    return {
        "principalId": subject,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": "Allow",
                    # Scoped to the route that was actually asked for, never "*". This is
                    # the $connect method ARN; a wildcard here would be a policy that
                    # outlives the question it was asked.
                    "Resource": event["methodArn"],
                }
            ],
        },
        "context": {"sub": subject, "clientId": client_id},
    }
