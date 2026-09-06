import pytest
from ordinance_data import ORDINANCES, POLICE_AUTHORITIES, list_authorities


def test_ordinances_exist():
    authorities = list_authorities()
    assert "nagoya-city" in authorities
    assert "anjo-city" in authorities
    assert "okazaki-city" in authorities
    assert "metropolitan-police" in authorities


def test_nagoya_ordinance_details():
    nagoya = ORDINANCES.get("nagoya-city")
    assert nagoya is not None
    assert nagoya.request_deadline_days == 30
    assert nagoya.extension_days == 30
    assert len(nagoya.non_disclosure_grounds) > 0


def test_police_authorities():
    police = POLICE_AUTHORITIES.get("metropolitan-police")
    assert police is not None
    assert police.authority_type == "警察本部"
