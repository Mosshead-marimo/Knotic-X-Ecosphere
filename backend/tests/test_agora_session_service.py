import unittest
from datetime import UTC, datetime

from knotic_api.domain.identifiers import new_uuid7
from knotic_api.voice.session_service import (
    AgoraSessionDenied,
    AgoraSessionTokenService,
    InMemoryAgoraAuditSink,
    InMemoryAgoraSessionStore,
    channel_name_for,
    uid_for,
)

_APP_ID = "0123456789abcdef0123456789abcdef"
_APP_CERTIFICATE = "fedcba9876543210fedcba9876543210"
_NOW = datetime(2026, 9, 4, tzinfo=UTC)


class ChannelAndUidDerivationTests(unittest.TestCase):
    def test_channel_name_is_deterministic_and_tenant_and_session_scoped(self) -> None:
        tenant_id = new_uuid7()
        session_id = new_uuid7()

        first = channel_name_for(tenant_id=tenant_id, session_id=session_id)
        second = channel_name_for(tenant_id=tenant_id, session_id=session_id)

        self.assertEqual(first, second)
        self.assertLessEqual(len(first.encode("utf-8")), 64)
        self.assertNotEqual(first, channel_name_for(tenant_id=new_uuid7(), session_id=session_id))
        self.assertNotEqual(first, channel_name_for(tenant_id=tenant_id, session_id=new_uuid7()))

    def test_uid_is_deterministic_per_actor_and_fits_agoras_range(self) -> None:
        actor_id = new_uuid7()

        first = uid_for(actor_id)
        second = uid_for(actor_id)

        self.assertEqual(first, second)
        self.assertTrue(1 <= first <= 2**32 - 1)
        self.assertNotEqual(first, uid_for(new_uuid7()))


class AgoraSessionTokenServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryAgoraSessionStore()
        self.audit = InMemoryAgoraAuditSink()
        self.service = AgoraSessionTokenService(
            app_id=_APP_ID, app_certificate=_APP_CERTIFICATE, store=self.store, audit_sink=self.audit
        )
        self.tenant_id = new_uuid7()
        self.actor_id = new_uuid7()
        self.session_id = new_uuid7()

    def test_issue_returns_a_channel_and_uid_scoped_to_the_caller_and_audits_it(self) -> None:
        issued = self.service.issue(
            tenant_id=self.tenant_id, actor_id=self.actor_id, session_id=self.session_id, role="PUBLISHER", now=_NOW
        )

        self.assertEqual(issued.channel_name, channel_name_for(tenant_id=self.tenant_id, session_id=self.session_id))
        self.assertEqual(issued.uid, uid_for(self.actor_id))
        self.assertEqual(issued.app_id, _APP_ID)
        self.assertGreater(issued.expires_at, issued.issued_at)
        self.assertEqual([event.action for event in self.audit.events], ["ISSUED"])
        self.assertEqual(self.audit.events[0].channel_name, issued.channel_name)

    def test_renew_requires_a_prior_active_issuance(self) -> None:
        with self.assertRaises(AgoraSessionDenied):
            self.service.renew(
                tenant_id=self.tenant_id,
                actor_id=self.actor_id,
                session_id=self.session_id,
                role="PUBLISHER",
                now=_NOW,
            )
        self.assertEqual([event.action for event in self.audit.events], ["DENIED"])

        self.service.issue(
            tenant_id=self.tenant_id, actor_id=self.actor_id, session_id=self.session_id, role="PUBLISHER", now=_NOW
        )
        renewed = self.service.renew(
            tenant_id=self.tenant_id, actor_id=self.actor_id, session_id=self.session_id, role="PUBLISHER", now=_NOW
        )
        self.assertEqual(renewed.channel_name, channel_name_for(tenant_id=self.tenant_id, session_id=self.session_id))

    def test_revoke_blocks_further_issuance_and_renewal_until_reissued(self) -> None:
        self.service.issue(
            tenant_id=self.tenant_id, actor_id=self.actor_id, session_id=self.session_id, role="PUBLISHER", now=_NOW
        )

        self.service.revoke(tenant_id=self.tenant_id, actor_id=self.actor_id, session_id=self.session_id)

        with self.assertRaises(AgoraSessionDenied):
            self.service.issue(
                tenant_id=self.tenant_id,
                actor_id=self.actor_id,
                session_id=self.session_id,
                role="PUBLISHER",
                now=_NOW,
            )
        with self.assertRaises(AgoraSessionDenied):
            self.service.renew(
                tenant_id=self.tenant_id,
                actor_id=self.actor_id,
                session_id=self.session_id,
                role="PUBLISHER",
                now=_NOW,
            )
        self.assertTrue(self.store.is_revoked(tenant_id=self.tenant_id, session_id=self.session_id))

    def test_issuing_for_a_different_session_never_touches_another_sessions_channel(self) -> None:
        other_session_id = new_uuid7()

        first = self.service.issue(
            tenant_id=self.tenant_id, actor_id=self.actor_id, session_id=self.session_id, role="PUBLISHER", now=_NOW
        )
        second = self.service.issue(
            tenant_id=self.tenant_id, actor_id=self.actor_id, session_id=other_session_id, role="PUBLISHER", now=_NOW
        )

        self.assertNotEqual(first.channel_name, second.channel_name)
        self.assertEqual(first.uid, second.uid)

    def test_rejects_out_of_range_token_ttl(self) -> None:
        with self.assertRaises(ValueError):
            AgoraSessionTokenService(
                app_id=_APP_ID,
                app_certificate=_APP_CERTIFICATE,
                store=self.store,
                audit_sink=self.audit,
                token_ttl_seconds=30,
            )


if __name__ == "__main__":
    unittest.main()
