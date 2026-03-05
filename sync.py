#!/usr/bin/env python3
"""
traefik-dns-sync
Watches Redis for Traefik router rules and auto-creates/deletes Cloudflare DNS records.
"""

import os
import re
import time
import logging
import threading
import requests
import redis

# ── Config from env ────────────────────────────────────────────────────────────
REDIS_HOST         = os.environ["REDIS_HOST"]
REDIS_PORT         = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD     = os.getenv("REDIS_PASSWORD", "")
REDIS_ROOT_KEY     = os.getenv("REDIS_ROOT_KEY", "traefik")

CF_API_TOKEN       = os.environ["CF_API_TOKEN"]
CF_ZONE_NAME       = os.environ["CF_ZONE_NAME"]          # e.g. tristandk.be
CNAME_TARGET       = os.environ["CNAME_TARGET"]          # e.g. gent.tristandk.be
CF_PROXIED         = os.getenv("CF_PROXIED", "true").lower() == "true"
CF_AUTO_DELETE     = os.getenv("CF_AUTO_DELETE", "false").lower() == "true"

# Comma-separated list of hostnames to never touch
EXCLUDE_HOSTS      = set(h.strip() for h in os.getenv("EXCLUDE_HOSTS", "").split(",") if h.strip())

LOG_LEVEL          = os.getenv("LOG_LEVEL", "INFO").upper()

# Tag added to all records we create — used to identify ownership
RECORD_COMMENT     = "traefik-dns-sync"

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ── Cloudflare API ─────────────────────────────────────────────────────────────
CF_BASE = "https://api.cloudflare.com/client/v4"
CF_HEADERS = {
    "Authorization": f"Bearer {CF_API_TOKEN}",
    "Content-Type": "application/json",
}

def cf_get_zone_id():
    r = requests.get(f"{CF_BASE}/zones", headers=CF_HEADERS, params={"name": CF_ZONE_NAME})
    r.raise_for_status()
    zones = r.json()["result"]
    if not zones:
        raise ValueError(f"Zone {CF_ZONE_NAME!r} not found in Cloudflare")
    return zones[0]["id"]

def cf_list_records(zone_id):
    """Return dict of name → record for records we own."""
    records = {}
    page = 1
    while True:
        r = requests.get(
            f"{CF_BASE}/zones/{zone_id}/dns_records",
            headers=CF_HEADERS,
            params={"type": "CNAME", "per_page": 100, "page": page},
        )
        r.raise_for_status()
        data = r.json()
        for rec in data["result"]:
            if rec.get("comment") == RECORD_COMMENT:
                records[rec["name"]] = rec
        if page >= data["result_info"]["total_pages"]:
            break
        page += 1
    return records

def cf_create_record(zone_id, name):
    payload = {
        "type": "CNAME",
        "name": name,
        "content": CNAME_TARGET,
        "ttl": 1,  # Auto
        "proxied": CF_PROXIED,
        "comment": RECORD_COMMENT,
    }
    r = requests.post(f"{CF_BASE}/zones/{zone_id}/dns_records", headers=CF_HEADERS, json=payload)
    r.raise_for_status()
    log.info(f"Created CNAME {name} → {CNAME_TARGET} (proxied={CF_PROXIED})")

def cf_delete_record(zone_id, record_id, name):
    r = requests.delete(f"{CF_BASE}/zones/{zone_id}/dns_records/{record_id}", headers=CF_HEADERS)
    r.raise_for_status()
    log.info(f"Deleted CNAME {name}")

# ── Redis helpers ──────────────────────────────────────────────────────────────
def make_redis():
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD or None,
        decode_responses=True,
    )

def enable_keyspace_notifications(r):
    """Enable Redis keyspace notifications for generic commands and string set/del."""
    try:
        current = r.config_get("notify-keyspace-events").get("notify-keyspace-events", "")
        # We need K (keyspace), E (keyevent), g (generic), $ (string), x (expired)
        needed = set("KEg$x")
        current_set = set(current)
        if not needed.issubset(current_set):
            new_val = "".join(needed | current_set)
            r.config_set("notify-keyspace-events", new_val)
            log.info(f"Enabled Redis keyspace notifications (was: {current!r}, now: {new_val!r})")
        else:
            log.info("Redis keyspace notifications already enabled")
    except Exception as e:
        log.warning(f"Could not set keyspace notifications: {e} — falling back to poll mode")

def get_all_router_rules(r):
    """Scan Redis for all traefik router rule keys."""
    pattern = f"{REDIS_ROOT_KEY}/http/routers/*/rule"
    keys = r.keys(pattern)
    rules = {}
    for key in keys:
        val = r.get(key)
        if val:
            rules[key] = val
    return rules

# ── Hostname parsing ───────────────────────────────────────────────────────────
HOST_RE = re.compile(r"Host\(`([^`]+)`\)", re.IGNORECASE)

def extract_hosts(rule: str) -> list[str]:
    """Extract all hostnames from a Traefik router rule string."""
    return HOST_RE.findall(rule)

def is_in_zone(hostname: str) -> bool:
    return hostname == CF_ZONE_NAME or hostname.endswith(f".{CF_ZONE_NAME}")

# ── Core sync logic ────────────────────────────────────────────────────────────
class DnsSync:
    def __init__(self):
        self.r = make_redis()
        self.zone_id = cf_get_zone_id()
        log.info(f"Zone ID for {CF_ZONE_NAME}: {self.zone_id}")

    def desired_hosts(self) -> set:
        """All hostnames currently in Redis that belong to our zone."""
        hosts = set()
        for key, rule in get_all_router_rules(self.r).items():
            for h in extract_hosts(rule):
                if is_in_zone(h) and h not in EXCLUDE_HOSTS:
                    hosts.add(h)
        return hosts

    def sync_all(self):
        """Full reconciliation: create missing, delete removed (if enabled)."""
        desired = self.desired_hosts()
        owned = cf_list_records(self.zone_id)

        # Create missing
        for host in desired:
            if host not in owned:
                try:
                    cf_create_record(self.zone_id, host)
                except Exception as e:
                    log.error(f"Failed to create {host}: {e}")
            else:
                log.debug(f"Record already exists: {host}")

        # Delete removed (only records we own)
        if CF_AUTO_DELETE:
            for name, rec in owned.items():
                if name not in desired:
                    try:
                        cf_delete_record(self.zone_id, rec["id"], name)
                    except Exception as e:
                        log.error(f"Failed to delete {name}: {e}")

    def watch(self):
        """Subscribe to Redis keyspace events and react to router rule changes."""
        pubsub = self.r.pubsub()
        channel = f"__keyevent@0__:set"
        pubsub.subscribe(channel)
        pubsub.subscribe("__keyevent@0__:del")
        pubsub.subscribe("__keyevent@0__:expired")
        log.info("Subscribed to Redis keyspace events")

        for message in pubsub.listen():
            if message["type"] != "message":
                continue
            key = message["data"]
            # Only care about router rule keys
            if not re.match(rf"^{re.escape(REDIS_ROOT_KEY)}/http/routers/[^/]+/rule$", key):
                continue
            log.debug(f"Keyspace event for: {key}")
            # Small debounce — wait briefly for any related keys
            time.sleep(0.5)
            try:
                self.sync_all()
            except Exception as e:
                log.error(f"sync_all failed: {e}")

def main():
    log.info("traefik-dns-sync starting")
    log.info(f"  Zone: {CF_ZONE_NAME}")
    log.info(f"  CNAME target: {CNAME_TARGET}")
    log.info(f"  Proxied: {CF_PROXIED}")
    log.info(f"  Auto-delete: {CF_AUTO_DELETE}")
    log.info(f"  Exclude: {EXCLUDE_HOSTS or '(none)'}")

    # Connect with retry
    r = None
    for attempt in range(10):
        try:
            r = make_redis()
            r.ping()
            log.info(f"Connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
            break
        except Exception as e:
            log.warning(f"Redis not ready ({e}), retrying in 3s...")
            time.sleep(3)
    else:
        raise RuntimeError("Could not connect to Redis after 10 attempts")

    enable_keyspace_notifications(r)

    sync = DnsSync()

    # Initial full sync on startup
    log.info("Running initial sync...")
    sync.sync_all()
    log.info("Initial sync complete")

    # Watch for changes
    sync.watch()

if __name__ == "__main__":
    main()
