"""Integration checks against the disposable Redis service in PR CI.

The Docker-image smoke test runs without Redis and intentionally skips these tests.
"""
import os
import time
import unittest
import uuid

os.environ.setdefault("REDIS_HOST", "127.0.0.1")
os.environ.setdefault("CF_API_TOKEN", "integration-test-token")
os.environ.setdefault("CF_ZONE_NAME", "example.com")
os.environ.setdefault("CNAME_TARGET", "edge.example.net")

import sync


@unittest.skipUnless(os.getenv("REDIS_INTEGRATION") == "1", "requires disposable Redis in CI")
class RedisIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = sync.make_redis()
        cls.client.ping()

    def test_scan_and_get_router_rules(self):
        key = f"traefik/http/routers/ci-{uuid.uuid4().hex}/rule"
        rule = "Host(`redis-ci.example.com`)"
        try:
            self.client.set(key, rule)
            self.assertEqual(sync.get_all_router_rules(self.client).get(key), rule)
        finally:
            self.client.delete(key)

    def test_keyspace_notification_configuration_and_pubsub(self):
        previous = self.client.config_get("notify-keyspace-events").get("notify-keyspace-events", "")
        key = f"traefik/http/routers/ci-{uuid.uuid4().hex}/rule"
        try:
            sync.enable_keyspace_notifications(self.client)
            current = self.client.config_get("notify-keyspace-events")["notify-keyspace-events"]
            self.assertTrue(set("KEg$x").issubset(set(current)))
            with self.client.pubsub() as pubsub:
                pubsub.subscribe("__keyevent@0__:set")
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    received = pubsub.get_message(timeout=0.2)
                    if received and received["type"] == "subscribe":
                        break
                else:
                    self.fail("Redis subscription acknowledgement not received")
                self.client.set(key, "Host(`event.example.com`)")
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    received = pubsub.get_message(timeout=0.2)
                    if received and received.get("type") == "message" and received.get("data") == key:
                        break
                else:
                    self.fail("Redis keyspace notification was not delivered")
        finally:
            self.client.delete(key)
            self.client.config_set("notify-keyspace-events", previous)


if __name__ == "__main__":
    unittest.main()
