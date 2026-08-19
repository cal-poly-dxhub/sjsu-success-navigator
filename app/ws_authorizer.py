"""The $connect authorizer for the WebSocket streaming API.

A WebSocket API accepts no native JWT authorizer, the token travels in the query string,
and nothing here may ever log it; see docs/chat-service.md, The $connect authorizer.
"""

import os

import jwt
from jwt import PyJWKClient

_REGION = os.environ["COGNITO_REGION"]
_USER_POOL_ID = os.environ["USER_POOL_ID"]

# The app clients whose tokens are accepted. Empty admits nobody.
_ALLOWED_CLIENT_IDS = frozenset(
    part.strip() for part in os.environ.get("ALLOWED_CLIENT_IDS", "").split(",") if part.strip()
)

_ISSUER = f"https://cognito-idp.{_REGION}.amazonaws.com/{_USER_POOL_ID}"

_JWK_CLIENT = PyJWKClient(f"{_ISSUER}/.well-known/jwks.json", cache_keys=True)


class Unauthorized(Exception):
    """Raised for every rejection. API Gateway turns it into a 401 on the handshake."""


def _token_from(event):
    """The bearer token out of the query string, or a raise."""
    params = event.get("queryStringParameters") or {}
    token = params.get("token")
    if not isinstance(token, str) or not token.strip():
        raise Unauthorized("no token")
    return token.strip()


def _claims(token):
    """The validated claims, or a raise. Everything the HTTP API's authorizer checks."""
    signing_key = _JWK_CLIENT.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=_ISSUER,
        # A Cognito access token carries no audience; client_id is checked by hand below.
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
    """Allow or 401 the handshake, and hand `sub` down to every route on the connection."""
    try:
        subject, client_id = _claims(_token_from(event))
    except Exception as exc:
        # Bare on purpose: nothing token-shaped may reach a log line.
        raise Unauthorized("unauthorized") from exc

    return {
        "principalId": subject,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": "Allow",
                    # Scoped to the route actually asked for, never "*".
                    "Resource": event["methodArn"],
                }
            ],
        },
        "context": {"sub": subject, "clientId": client_id},
    }
