import pytest
from ordinance_data import (
    AUTHORITIES,
    ORDINANCES,
    POLICE_AUTHORITIES,
    list_authorities,
    get_ordinance,
    get_office_info,
    match_authority_by_text,
)


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


def test_every_authority_has_office_info():
    """全ての機関に窓口・アクセス情報が存在すること（過去のtoyota/gamagori不整合の再発防止）"""
    for key, info in AUTHORITIES.items():
        office = get_office_info(key)
        assert office["name"], f"{key}: office.name が空です"
        assert office["lat"] and office["lon"], f"{key}: 緯度経度が未設定です"


def test_toyota_and_gamagori_are_registered():
    """agent.py / station_guide.py が参照していたが条例データが欠けていた機関"""
    assert get_ordinance("toyota-city") is not None
    assert get_ordinance("gamagori-city") is not None


def test_authority_keys_match_filenames_and_categories():
    for key, info in AUTHORITIES.items():
        assert info.key == key
        assert info.category in ("自治体", "警察", "裁判所")


def test_court_authorities():
    from ordinance_data import COURT_AUTHORITIES, is_court_authority, COURT_COUNTER_ARGUMENTS
    court = COURT_AUTHORITIES.get("supreme-court")
    assert court is not None
    assert court.authority_type == "最高裁判所"
    assert is_court_authority("supreme-court") is True
    assert is_court_authority("anjo-city") is False
    assert "第4条第4号" in COURT_COUNTER_ARGUMENTS


def test_match_authority_by_text_courts():
    """裁判所キーワードからの推定テスト"""
    assert match_authority_by_text("最高裁判所の議事録を見たい") == "supreme-court"
    assert match_authority_by_text("最高裁の判断資料を請求したい") == "supreme-court"
    assert match_authority_by_text("東京地裁の修繕費の内訳が知りたい") == "tokyo-district-court"
    assert match_authority_by_text("名古屋高裁の通達を確認したい") == "nagoya-high-court"


def test_match_authority_by_text_prefers_longer_alias():
    """「愛知県」は「愛知県警」の部分文字列だが、より長いエイリアスの警察組織が優先されること"""
    assert match_authority_by_text("愛知県警に不満がある") == "aichi-police"
    assert match_authority_by_text("愛知県庁の対応に怒っている") == "aichi-pref"


def test_match_authority_by_text_default_fallback():
    assert match_authority_by_text("誰にも言いたくないけど怒っている") == "anjo-city"


def test_new_authority_can_be_added_without_code_changes(tmp_path, monkeypatch):
    """新規JSONファイルを1つ追加するだけで対象機関が増えることを確認する"""
    import ordinance_data
    import json

    sample = {
        "key": "sample-city",
        "category": "自治体",
        "authority": "サンプル市",
        "authority_type": "市長",
        "ordinance_name": "サンプル市情報公開条例",
        "ordinance_id": "テスト用",
        "enacted": "2026年1月1日",
        "request_form": "サンプル市情報公開請求書",
        "request_deadline_days": 30,
        "extension_days": 30,
        "review_period_days": 90,
        "non_disclosure_grounds": [],
        "review_authority": "サンプル市情報公開審査会",
        "contact": "サンプル市役所",
        "office": {
            "name": "サンプル市役所",
            "address": "テスト住所",
            "lat": 0.0,
            "lon": 0.0,
            "nearest_station": "テスト駅",
        },
        "aliases": ["サンプル市"],
    }
    (tmp_path / "sample-city.json").write_text(
        json.dumps(sample, ensure_ascii=False), encoding="utf-8"
    )

    monkeypatch.setattr(ordinance_data, "DATA_DIR", tmp_path)
    loaded = ordinance_data._load_authorities()

    assert "sample-city" in loaded
    assert loaded["sample-city"].authority == "サンプル市"
