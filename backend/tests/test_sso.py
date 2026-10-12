import asyncio
import base64
import hashlib
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services import sso

ISSUER = "https://idp.test/realms/jarn"
CLIENT = "jarn-client"
PROVIDER = {"id": "kc", "name": "Keycloak", "issuer": ISSUER, "client_id": CLIENT, "client_secret": "s",
            "username_claim": "preferred_username", "groups_claim": "groups",
            "role_map": {"jarn-admins": "admin", "jarn-operators": "operator"}, "default_role": None}
DISCOVERY = {"issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/auth", "token_endpoint": f"{ISSUER}/token",
             "jwks_uri": f"{ISSUER}/certs"}


@pytest.fixture(scope="module")
def signer():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    from jwt.algorithms import RSAAlgorithm
    public = RSAAlgorithm.from_jwk(RSAAlgorithm.to_jwk(key.public_key(), as_dict=True))
    keyset = jwt.PyJWKSet.from_dict({"keys": [{**RSAAlgorithm.to_jwk(key.public_key(), as_dict=True), "kid": "k1", "alg": "RS256", "use": "sig"}]})
    return key, keyset


def token(key, **overrides):
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": CLIENT, "sub": "u-1", "iat": now, "exp": now + 300, "nonce": "n-1",
              "preferred_username": "alice", "groups": ["jarn-operators"]}
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


def test_valid_token_is_accepted(signer):
    key, keyset = signer
    claims = sso.validate_id_token(token(key), PROVIDER, DISCOVERY, "n-1", keyset)
    assert claims["preferred_username"] == "alice"


def test_wrong_audience_is_refused(signer):
    key, keyset = signer
    with pytest.raises(sso.SignInDenied):
        sso.validate_id_token(token(key, aud="another-app"), PROVIDER, DISCOVERY, "n-1", keyset)


def test_wrong_issuer_is_refused(signer):
    key, keyset = signer
    with pytest.raises(sso.SignInDenied):
        sso.validate_id_token(token(key, iss="https://evil.test"), PROVIDER, DISCOVERY, "n-1", keyset)


def test_replayed_nonce_is_refused(signer):
    key, keyset = signer
    with pytest.raises(sso.SignInDenied, match="nonce"):
        sso.validate_id_token(token(key), PROVIDER, DISCOVERY, "a-different-nonce", keyset)


def test_expired_token_is_refused(signer):
    key, keyset = signer
    with pytest.raises(sso.SignInDenied):
        sso.validate_id_token(token(key, exp=int(time.time()) - 3600, iat=int(time.time()) - 7200), PROVIDER, DISCOVERY, "n-1", keyset)


def test_unknown_signing_key_is_refused(signer):
    key, keyset = signer
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(sso.SignInDenied):
        sso.validate_id_token(token(other), PROVIDER, DISCOVERY, "n-1", keyset)


def test_symmetric_algorithm_is_refused(signer):
    key, keyset = signer
    forged = jwt.encode({"iss": ISSUER, "aud": CLIENT, "sub": "x", "iat": int(time.time()), "exp": int(time.time()) + 60,
                         "nonce": "n-1", "preferred_username": "admin"}, "shared-secret", algorithm="HS256")
    with pytest.raises(sso.SignInDenied):
        sso.validate_id_token(forged, PROVIDER, DISCOVERY, "n-1", keyset)


def test_pkce_challenge_matches_verifier():
    verifier, challenge = sso.pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert challenge == expected and 43 <= len(verifier) <= 128


def test_authorization_url_carries_state_nonce_and_pkce():
    config = {"public_base_url": "https://jarn.example.com"}
    url, remembered = sso.build_authorization_url(PROVIDER, DISCOVERY, config)
    assert url.startswith(f"{ISSUER}/auth?")
    assert f"state={remembered['state']}" in url and f"nonce={remembered['nonce']}" in url
    assert "code_challenge_method=S256" in url
    assert "redirect_uri=https%3A%2F%2Fjarn.example.com%2Fapi%2Fv1%2Fauth%2Fsso%2Fcallback" in url


def test_redirect_needs_public_address():
    with pytest.raises(sso.SignInDenied):
        sso.build_authorization_url(PROVIDER, DISCOVERY, {})


def test_nested_claims_and_group_mapping():
    assert sso.claim_value({"realm_access": {"roles": ["x"]}}, "realm_access.roles") == ["x"]
    assert sso.claim_value({}, "missing.path") is None
    assert sso.map_roles(["JARN-Admins", "other"], {"jarn-admins": "admin"}) == ["admin"]


def test_highest_mapped_role_wins():
    assert sso.decide_roles(["jarn-operators", "jarn-admins"], PROVIDER["role_map"], None) == "admin"


def test_no_group_and_no_default_refuses_sign_in():
    assert sso.decide_roles(["unrelated"], PROVIDER["role_map"], None) is None


def test_default_role_applies_when_no_group_matches():
    assert sso.decide_roles(["unrelated"], PROVIDER["role_map"], "viewer") == "viewer"


def test_allowed_email_list_grants_access_without_groups():
    assert sso.decide_roles([], {}, "operator", email="Pat@Example.com", allowed_emails=["pat@example.com"]) == "operator"
    assert sso.decide_roles([], {}, None, email="other@example.com", allowed_emails=["pat@example.com"]) is None


# ---------- LDAP, against an in-memory directory ----------

def _directory():
    from ldap3 import Server, Connection, MOCK_SYNC, OFFLINE_AD_2012_R2
    server = Server("fake-ldap", get_info=OFFLINE_AD_2012_R2)
    admin = Connection(server, user="cn=svc,dc=test", password="svc-pw", client_strategy=MOCK_SYNC)
    admin.strategy.add_entry("cn=svc,dc=test", {"userPassword": "svc-pw", "objectClass": "person"})
    admin.strategy.add_entry("cn=admins,ou=groups,dc=test", {"cn": "admins", "objectClass": "group"})
    admin.strategy.add_entry("uid=alice,ou=people,dc=test", {
        "uid": "alice", "mail": "alice@example.com", "userPassword": "alice-pw", "objectClass": "person",
        "memberOf": ["cn=admins,ou=groups,dc=test"]})
    admin.strategy.add_entry("uid=bob,ou=people,dc=test", {
        "uid": "bob", "mail": "bob@example.com", "userPassword": "bob-pw", "objectClass": "person", "memberOf": []})
    admin.bind()
    return server


def _factory(server):
    """A directory that checks passwords the way a real server does: a bad password leaves the connection unbound."""
    from ldap3 import Connection, MOCK_SYNC

    def make(user=None, password=None):
        conn = Connection(server, user=user, password=password, client_strategy=MOCK_SYNC)
        conn.bind()  # the directory refuses a wrong password, leaving the connection unbound
        return conn
    return make


LDAP_CFG = {"base_dn": "ou=people,dc=test", "bind_dn": "cn=svc,dc=test", "bind_password": "svc-pw",
            "user_filter": "(&(objectClass=person)(uid={username}))", "username_attribute": "uid",
            "email_attribute": "mail", "group_attribute": "memberOf",
            "role_map": {"cn=admins,ou=groups,dc=test": "admin"}, "default_role": None}


def test_ldap_user_with_right_password_gets_mapped_role():
    entry = sso.ldap_authenticate("alice", "alice-pw", LDAP_CFG, make_connection=_factory(_directory()))
    assert entry is not None and entry["email"] == "alice@example.com"
    assert sso.ldap_role(entry["groups"], LDAP_CFG) == "admin"


def test_ldap_wrong_password_is_refused():
    assert sso.ldap_authenticate("alice", "wrong", LDAP_CFG, make_connection=_factory(_directory())) is None


def test_ldap_unknown_user_is_refused():
    assert sso.ldap_authenticate("mallory", "x", LDAP_CFG, make_connection=_factory(_directory())) is None


def test_ldap_user_without_mapped_group_has_no_role():
    entry = sso.ldap_authenticate("bob", "bob-pw", LDAP_CFG, make_connection=_factory(_directory()))
    assert entry is not None and sso.ldap_role(entry["groups"], LDAP_CFG) is None


def test_ldap_filter_metacharacters_cannot_widen_the_search():
    assert sso.ldap_authenticate("*)(uid=*", "anything", LDAP_CFG, make_connection=_factory(_directory())) is None


def test_ldap_role_accepts_cn_shorthand():
    cfg = {**LDAP_CFG, "role_map": {"admins": "operator"}}
    assert sso.ldap_role(["CN=admins,OU=Groups,DC=test"], cfg) == "operator"
