"""Who the caller is on the streaming app, verified in this process.

A Function URL takes no authorizer, so the choice is verify here or serve nobody.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import boto3
import jwt
from jwt import PyJWKClient

# Spelled once here; the browser writes it too, and an infra test compares the two.
AUTH_HEADER_NAME = "x-sjsu-authorization"

# Accepted with or without it: the header is ours, so the scheme is decoration.
_BEARER_PREFIX = "bearer "

# Pinned, which stops `alg: none` and an HS256 MAC over the public key just fetched.
_ALGORITHMS = ["RS256"]

# Absence is a rejection at decode time, not a None to be noticed further down.
_REQUIRED_CLAIMS = ["exp", "iss", "sub", "token_use", "client_id"]

# The third names a Parameter Store parameter rather than carrying the ids themselves.
_REGION_VAR = "COGNITO_REGION"
_USER_POOL_VAR = "USER_POOL_ID"
_CLIENT_IDS_PARAMETER_VAR = "ALLOWED_CLIENT_IDS_PARAMETER"

# Not PyJWKClient's 30-second default: a hung JWKS fetch would sit in front of a student.
_JWKS_TIMEOUT_SECONDS = 5


class Unauthorized(Exception):
    """Every rejection, whatever caused it. No subclasses: a specific 401 is an oracle."""


@dataclass(frozen=True)
class Identity:
    """`sub` is the identity and the partition key; `client_id` only feeds the cap's exemptions."""

    sub: str
    client_id: str


def token_from_headers(headers) -> str:
    """The bearer token out of AUTH_HEADER_NAME, or a raise."""
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
    """One pool, one issuer, one client allowlist, and a JWKS client that caches."""

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
        """The caller, out of one access token. Every failure folds into one Unauthorized."""
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
            # Bare message, no chain: nothing token-shaped reaches a log.
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
    """Its own client: the turn's carry read timeouts sized for a model call, not a 200-byte read."""
    global _SSM_CLIENT
    if _SSM_CLIENT is None:
        _SSM_CLIENT = boto3.client("ssm")
    return _SSM_CLIENT


def _client_ids_from(parameter_name):
    """The allowlist out of Parameter Store, as a set. Raises on anything unusable."""
    value = _ssm_client().get_parameter(Name=parameter_name)["Parameter"]["Value"]
    return frozenset(part.strip() for part in value.split(",") if part.strip())


def verifier():
    """Built once, lazily. The success is cached; a failure is not, so the next request retries."""
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
        # The name and the type, never the exception's text: botocore can quote the request.
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
