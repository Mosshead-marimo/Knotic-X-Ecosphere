import unittest

from knotic_api.voice.agora_token import InvalidAgoraToken, build_rtc_token, decode_rtc_token

# Fixed app_id/app_certificate/channel_name/uid/issue_ts/salt/expire values and the expected
# token string are taken verbatim from Agora's own published test suite
# (AgoraIO/Tools, DynamicKey/AgoraDynamicKey/python3/test/AccessToken2Test.py,
# ``test_service_rtc``), so a byte-for-byte match here confirms this port of the AccessToken2
# ("007") wire format is compatible with the real Agora RTC service, not merely internally
# self-consistent.
_APP_ID = "970CA35de60c44645bbae8a215061b33"
_APP_CERTIFICATE = "5CFd2fd1755d40ecb72977518be15d3b"
_CHANNEL_NAME = "7d72365eb983485397e3e3f9d460bdda"
_UID = 2882341273
_ISSUE_TS = 1111111
_SALT = 1
_EXPIRE = 600
_EXPECTED_SUBSCRIBER_TOKEN = (
    "007eJxTYBBbsMMnKq7p9Hf/HcIX5kce9b518kCiQgSr5Zrp4X1Tu6UUGCzNDZwdjU1TUs0Mkk1MzExMk5ISUy0SjQxNDcwMk4"
    "yN3b8IMEQwMTAwMoAwBIL4CgzmKeZGxmamqUmWFsYmFqbGluapxqnGaZYpJmYGSSkpiVwMRhYWRsYmhkbmxgDCaiTj"
)


class BuildRtcTokenTests(unittest.TestCase):
    def test_subscriber_token_matches_agoras_published_fixture(self) -> None:
        token = build_rtc_token(
            app_id=_APP_ID,
            app_certificate=_APP_CERTIFICATE,
            channel_name=_CHANNEL_NAME,
            uid=_UID,
            role="SUBSCRIBER",
            issue_ts=_ISSUE_TS,
            salt=_SALT,
            token_expire_seconds=_EXPIRE,
            privilege_expire_seconds=_EXPIRE,
        )

        self.assertEqual(token, _EXPECTED_SUBSCRIBER_TOKEN)

    def test_publisher_token_round_trips_all_four_privileges(self) -> None:
        token = build_rtc_token(
            app_id=_APP_ID,
            app_certificate=_APP_CERTIFICATE,
            channel_name=_CHANNEL_NAME,
            uid=_UID,
            role="PUBLISHER",
            issue_ts=_ISSUE_TS,
            salt=_SALT,
            token_expire_seconds=_EXPIRE,
            privilege_expire_seconds=_EXPIRE,
        )

        decoded = decode_rtc_token(token, app_certificate=_APP_CERTIFICATE)

        self.assertTrue(decoded.signature_valid)
        self.assertEqual(decoded.app_id, _APP_ID)
        self.assertEqual(decoded.channel_name, _CHANNEL_NAME)
        self.assertEqual(decoded.uid, str(_UID))
        self.assertEqual(decoded.issue_ts, _ISSUE_TS)
        self.assertEqual(decoded.token_expire_seconds, _EXPIRE)
        self.assertEqual(decoded.privileges, {1: _EXPIRE, 2: _EXPIRE, 3: _EXPIRE, 4: _EXPIRE})

    def test_decode_rejects_tampered_signature(self) -> None:
        token = build_rtc_token(
            app_id=_APP_ID,
            app_certificate=_APP_CERTIFICATE,
            channel_name=_CHANNEL_NAME,
            uid=_UID,
            role="SUBSCRIBER",
            issue_ts=_ISSUE_TS,
            salt=_SALT,
            token_expire_seconds=_EXPIRE,
            privilege_expire_seconds=_EXPIRE,
        )

        decoded = decode_rtc_token(token, app_certificate="f" * 32)

        self.assertFalse(decoded.signature_valid)

    def test_decode_rejects_unsupported_version(self) -> None:
        with self.assertRaises(InvalidAgoraToken):
            decode_rtc_token("006" + "a", app_certificate=_APP_CERTIFICATE)

    def test_build_rejects_invalid_identifiers_and_bounds(self) -> None:
        base = {
            "app_id": _APP_ID,
            "app_certificate": _APP_CERTIFICATE,
            "channel_name": _CHANNEL_NAME,
            "uid": _UID,
            "role": "SUBSCRIBER",
            "issue_ts": _ISSUE_TS,
            "salt": _SALT,
            "token_expire_seconds": _EXPIRE,
            "privilege_expire_seconds": _EXPIRE,
        }
        with self.assertRaises(InvalidAgoraToken):
            build_rtc_token(**{**base, "app_id": "not-hex"})
        with self.assertRaises(InvalidAgoraToken):
            build_rtc_token(**{**base, "uid": 0})
        with self.assertRaises(InvalidAgoraToken):
            build_rtc_token(**{**base, "uid": 2**32})
        with self.assertRaises(InvalidAgoraToken):
            build_rtc_token(**{**base, "channel_name": ""})
        with self.assertRaises(InvalidAgoraToken):
            build_rtc_token(**{**base, "token_expire_seconds": 0})
        with self.assertRaises(InvalidAgoraToken):
            build_rtc_token(**{**base, "privilege_expire_seconds": 100_000})


if __name__ == "__main__":
    unittest.main()
