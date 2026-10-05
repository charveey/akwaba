import pytest
from pydantic import ValidationError

from app.schemas.members import IdentityIn, MemberIn


def test_member_normalization():
    m = MemberIn(full_name="  Awa Koné ", email=" Awa@Example.TEST ", phone_e164="+225 01-02.03 04 05",
                 country_code="ci", notes="  ")
    assert m.full_name == "Awa Koné"
    assert m.email == "awa@example.test"
    assert m.phone_e164 == "+2250102030405"
    assert m.country_code == "CI"
    assert m.notes is None


def test_blank_optional_fields_become_none():
    m = MemberIn(full_name="X", email="  ", phone_e164="", country_code=" ")
    assert (m.email, m.phone_e164, m.country_code) == (None, None, None)


@pytest.mark.parametrize("field, value", [
    ("full_name", "   "),
    ("email", "pas-un-email"),
    ("phone_e164", "0102030405"),
    ("phone_e164", "+0123456789"),
    ("country_code", "CIV"),
    ("country_code", "1A"),
])
def test_invalid_values_are_rejected(field, value):
    with pytest.raises(ValidationError):
        MemberIn(**{"full_name": "X", field: value})


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        MemberIn(full_name="X", is_admin=True)


def test_identity_login_is_lowercased_and_validated():
    assert IdentityIn(login_name=" User@Example.TEST ").login_name == "user@example.test"
    with pytest.raises(ValidationError):
        IdentityIn(login_name="a b@example.test")
    with pytest.raises(ValidationError):
        IdentityIn(login_name="   ")
