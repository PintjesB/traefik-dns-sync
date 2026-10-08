import importlib
import os
import unittest
from unittest import mock

os.environ.setdefault("REDIS_HOST", "redis")
os.environ.setdefault("CF_API_TOKEN", "test-token")
os.environ.setdefault("CF_ZONE_NAME", "example.com")
os.environ.setdefault("CNAME_TARGET", "edge.example.net")
os.environ.setdefault("EXCLUDE_HOSTS", "skip.example.com")

sync = importlib.import_module("sync")


class FakeRedis:
    def __init__(self, values):
        self.values = values

    def scan_iter(self, pattern, count=100):
        self.pattern = pattern
        self.count = count
        return iter(self.values)

    def get(self, key):
        return self.values[key]


class SyncTests(unittest.TestCase):
    def test_extract_hosts_handles_multiple_hosts(self):
        self.assertEqual(
            sync.extract_hosts("Host(`a.example.com`) || Host(`b.example.com`)"),
            ["a.example.com", "b.example.com"],
        )

    def test_zone_matching_accepts_zone_and_subdomains(self):
        self.assertTrue(sync.is_in_zone("example.com"))
        self.assertTrue(sync.is_in_zone("api.example.com"))

    def test_zone_matching_rejects_lookalike_suffixes(self):
        self.assertFalse(sync.is_in_zone("example.com.evil.test"))
        self.assertFalse(sync.is_in_zone("notexample.com"))

    def test_router_rules_are_read_with_scan(self):
        redis = FakeRedis({
            "traefik/http/routers/a/rule": "Host(`a.example.com`)",
            "traefik/http/routers/b/rule": "Host(`b.example.com`)",
        })
        rules = sync.get_all_router_rules(redis)
        self.assertEqual(set(rules), set(redis.values))
        self.assertEqual(redis.pattern, "traefik/http/routers/*/rule")
        self.assertEqual(redis.count, 100)

    @mock.patch.object(sync, "get_all_router_rules")
    def test_desired_hosts_filters_zone_and_exclusions(self, get_rules):
        get_rules.return_value = {
            "a": "Host(`api.example.com`)",
            "b": "Host(`skip.example.com`)",
            "c": "Host(`outside.test`)",
        }
        desired = sync.DnsSync(mock.Mock(), "zone").desired_hosts()
        self.assertEqual(desired, {"api.example.com"})

    @mock.patch.object(sync, "cf_create_record")
    @mock.patch.object(sync, "cf_list_records")
    @mock.patch.object(sync.DnsSync, "desired_hosts")
    def test_sync_creates_only_missing_records(self, desired_hosts, list_records, create_record):
        desired_hosts.return_value = {"existing.example.com", "new.example.com"}
        list_records.return_value = {
            "existing.example.com": {"id": "1", "name": "existing.example.com"}
        }
        sync.DnsSync(mock.Mock(), "zone-id").sync_all()
        create_record.assert_called_once_with("zone-id", "new.example.com")

    @mock.patch.object(sync, "cf_delete_record")
    @mock.patch.object(sync, "cf_list_records")
    @mock.patch.object(sync.DnsSync, "desired_hosts")
    def test_sync_deletes_only_stale_owned_records_when_enabled(
        self, desired_hosts, list_records, delete_record
    ):
        desired_hosts.return_value = {"keep.example.com"}
        list_records.return_value = {
            "keep.example.com": {"id": "1", "name": "keep.example.com"},
            "stale.example.com": {"id": "2", "name": "stale.example.com"},
        }
        with mock.patch.object(sync, "CF_AUTO_DELETE", True):
            sync.DnsSync(mock.Mock(), "zone-id").sync_all()
        delete_record.assert_called_once_with("zone-id", "2", "stale.example.com")

    @mock.patch.object(sync, "_cf_request")
    def test_cloudflare_record_listing_paginates_and_keeps_owned_records(self, request):
        request.side_effect = [
            {
                "result": [
                    {"name": "a.example.com", "comment": sync.RECORD_COMMENT, "id": "1"},
                    {"name": "foreign.example.com", "comment": "other", "id": "2"},
                ],
                "result_info": {"total_pages": 2},
            },
            {
                "result": [
                    {"name": "b.example.com", "comment": sync.RECORD_COMMENT, "id": "3"}
                ],
                "result_info": {"total_pages": 2},
            },
        ]
        records = sync.cf_list_records("zone-id")
        self.assertEqual(set(records), {"a.example.com", "b.example.com"})
        self.assertEqual(request.call_count, 2)

    def test_watch_recovers_from_pubsub_disconnect_and_processes_events(self):
        """Exercise the real forever-loop reconnect path without Cloudflare I/O."""
        class StopWatch(BaseException):
            pass

        client = mock.Mock()
        disconnected = mock.Mock()
        disconnected.listen.side_effect = sync.redis.exceptions.ConnectionError(
            "simulated pubsub disconnect"
        )
        restored = mock.Mock()
        restored.listen.return_value = iter([
            {"type": "subscribe", "data": 1},
            {
                "type": "message",
                "data": "traefik/http/routers/redis-reconnect/rule",
            },
        ])
        client.pubsub.side_effect = [disconnected, restored]

        with mock.patch.object(sync.time, "sleep") as sleep, mock.patch.object(
            sync.DnsSync, "sync_all", side_effect=StopWatch
        ) as sync_all:
            with self.assertRaises(StopWatch):
                sync.DnsSync(client, "ci-zone").watch()

        self.assertEqual(client.pubsub.call_count, 2)
        channels = (
            "__keyevent@0__:set",
            "__keyevent@0__:del",
            "__keyevent@0__:expired",
        )
        disconnected.subscribe.assert_called_once_with(*channels)
        restored.subscribe.assert_called_once_with(*channels)
        sync_all.assert_called_once_with()
        sleep.assert_any_call(5)
        sleep.assert_any_call(0.5)

    @mock.patch.object(sync, "_cf_request")
    def test_zone_lookup_rejects_missing_zone(self, request):
        request.return_value = {"result": []}
        with self.assertRaisesRegex(ValueError, "not found"):
            sync.cf_get_zone_id()


if __name__ == "__main__":
    unittest.main()
