from datetime import date, datetime, timedelta

import pytest

import onchain_ledger as ol
import request_date as rd
from tests.test_ledger import UID, gql_attestation


def ts_hex(y, m, d, hour=0):
    return {"type": "BigNumber", "hex": hex(int(datetime(y, m, d, hour, tzinfo=ol.JST).timestamp()))}


def attestation(timestamp):
    a = gql_attestation()  # 記録した日時: 2026-09-24 10:00:46 JST
    import json

    fields = json.loads(a["decodedDataJson"])
    for f in fields:
        if f["name"] == "timestamp":
            f["value"]["value"] = timestamp
    return {**a, "decodedDataJson": json.dumps(fields, ensure_ascii=False)}


def test_parse_valid_empty_and_invalid_dates():
    assert rd.parse_request_date("2026-09-22") == date(2026, 9, 22)
    assert rd.parse_request_date("  ") is None and rd.parse_request_date(None) is None
    for bad in ("2026/09/22", "9/22", "来週", "2026-13-01", "1999-12-31"):
        with pytest.raises(ValueError):
            rd.parse_request_date(bad)
    tomorrow = (rd.today_jst() + timedelta(days=1)).isoformat()
    with pytest.raises(ValueError, match="未来"):
        rd.parse_request_date(tomorrow)
    assert rd.parse_request_date(rd.today_jst().isoformat()) == rd.today_jst(), "今日はよい"


def test_unix_round_trip_is_jst_midnight():
    d = date(2026, 9, 22)
    assert rd.from_unix(rd.to_unix(d)) == d
    assert rd.to_unix(d) == int(datetime(2026, 9, 22, tzinfo=ol.JST).timestamp())
    assert rd.from_unix(0) is None


def test_declared_request_date_is_used_as_the_deadline_start():
    e = ol._parse(attestation(ts_hex(2026, 9, 22)), 84532, None, today=datetime(2026, 10, 9, tzinfo=ol.JST))
    assert e["requested_date"] == "2026-09-22" and e["requested_date_declared"] is True
    assert e["recorded_at_display"].startswith("2026年09月24日"), "記録した日は別に残る"
    # 愛知県知事: 請求日(9/22)を1日目として15日以内 = 10/6、延長上限は +30日 = 11/5
    assert (e["deadline"]["deadline"], e["deadline"]["extended_deadline"]) == ("2026-10-06", "2026-11-05")


def test_undeclared_request_date_falls_back_to_the_recording_date():
    e = ol._parse(attestation(ts_hex(2026, 9, 24, hour=10)), 84532, None, today=datetime(2026, 10, 9, tzinfo=ol.JST))
    assert e["requested_date"] == "2026-09-24" and e["requested_date_declared"] is False
    assert e["deadline"]["deadline"] == "2026-10-08"


@pytest.mark.parametrize("value", [ts_hex(2026, 9, 25), {"type": "BigNumber", "hex": "0x0"}, "garbage", None, ts_hex(1990, 1, 1)])
def test_invalid_declared_dates_are_ignored(value):
    # 記録した日（9/24）より後の日付・0・読めない値・2000年より前は、申告として扱わない
    e = ol._parse(attestation(value), 84532, None, today=datetime(2026, 10, 9, tzinfo=ol.JST))
    assert e["requested_date"] == "2026-09-24" and e["requested_date_declared"] is False


def test_notice_request_date_matching_the_declared_date_is_not_flagged():
    import ledger_extensions as le
    from tests.test_ledger_extensions import ext_att, request_entry

    entry = {**request_entry(), "requested_date": "2026-09-22"}
    att = ext_att()  # 通知書記載の請求日 9/22
    got = le._parse(att, 84532, entry, None)
    assert got["request_date_differs_from_recorded"] is False, "申告された請求日と通知書が一致していれば注記しない"
    got = le._parse(att, 84532, {**entry, "requested_date": "2026-09-24"}, None)
    assert got["request_date_differs_from_recorded"] is True


def test_pages_have_the_request_date_controls_and_the_current_wallet_schema():
    from pathlib import Path

    templates = Path(__file__).resolve().parent.parent / "templates"
    index = (templates / "index.html").read_text(encoding="utf-8")
    assert "attestationRequestDate" in index and "date-feed-btn" in index and "/submitted-date" in index
    # ウォレット署名は、現在のスキーマ（請求種別を含む7項目）で組み立てる。旧い6項目では台帳に載らない
    assert "['string', 'string', 'string', 'string', 'bytes32', 'uint256', 'string']" in index
    assert "['string', 'string', 'string', 'bytes32', 'uint256', 'string']" not in index
    ledger = (templates / "ledger.html").read_text(encoding="utf-8")
    assert "requested_date_declared" in ledger and "請求者の申告" in ledger
