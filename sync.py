#!/usr/bin/env python3
"""
traefik-dns-sync
Watches Redis for Traefik router rules and auto-creates/deletes Cloudflare CNAME records.
Uses only stdlib + redis-py — no requests dependency.
"""

import json
import os
import re
import time
import logging
import urllib.request
import urllib.parse
import urllib.error
import redis

# ── Config from env ────────────────────────────────────────────────────────────
REDIS_HOST     = os.environ["REDIS_HOST"]
REDIS_PORT     = int(os.getenv("REDIS_PORT", "6379"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_ROOT_KEY = os.getenv("REDIS_ROOT_KEY", "traefik")

CF_API_TOKEN   = os.environ["CF_API_TOKEN"]
CF_ZONE_NAME   = os.environ["CF_ZONE_NAME"]
CNAME_TARGET   = os.environ["CNAME_TARGET"]
CF_PROXIED     = os.getenv("CF_PROXIED", "true").lower() == "true"
CF_AUTO_DELETE = os.getenv("CF_AUTO_DELETE", "false").lower() == "true"
EXCLUDE_HOSTS  = set(h.strip() for h in os.getenv("EXCLUDE_HOSTS", "").split(",") if h.strip())
LOG_LEVEL      = os.getenv("LOG_LEVEL", "INFO").upper()

HTTP_TIMEOUT   = 10  # seconds for all HTTP calls
RECORD_COMMENT = "traefik-dns-sync"

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

# ── Compiled constants ─────────────────────────────────────────────────────────
HOST_RE      = re.compile(r"Host\(`([^`]+)`\)", re.IGNORECASE)
ROUTER_RE    = re.compile(r"^traefik/http/routers/[^/]+/rule$")
CF_BASE      = "https://api.cloudflare.com/client/v4"
CF_HEADERS   = {
    "Authorization": f"Bearer {CF_API_TOKEN}",
    "Content-Type": "application/json",
}

# ── Cloudflare API (stdlib urllib) ─────────────────────────────────────────────
def _cf_request(method: str, path: str, params: dict = None, body: dict = None) -> dict:
    url = f"{CF_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, headers=CF_HEADERS, method=method)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        return json.loads(resp.read())

def cf_get_zone_id() -> str:
    data = _cf_request("GET", "/zones", params={"name": CF_ZONE_NAME})
    if not data["result"]:
        raise ValueError(f"Zone {CF_ZONE_NAME!r} not found in Cloudflare")
    return data["result"][0]["id"]

def cf_list_records(zone_id: str) -> dict:
    """Return dict of name → record for records we own."""
    owned = {}
    page = 1
    while True:
        data = _cf_request("GET", f"/zones/{zone_id}/dns_records",
                           params={"type": "CNAME", "per_page": 100, "page": page})
        for rec in data["result"]:
            if rec.get("comment") == RECORD_COMMENT:
                owned[rec["name"]] = rec
        if page >= data["result_info"]["total_pages"]:
            break
        page += 1
    return owned

def cf_create_record(zone_id: str, name: str) -> None:
    _cf_request("POST", f"/zones/{zone_id}/dns_records", body={
        "type": "CNAME",
        "name": name,
        "content": CNAME_TARGET,
        "ttl": 1,
        "proxied": CF_PROXIED,
        "comment": RECORD_COMMENT,
    })
    log.info(f"Created CNAME {name} → {CNAME_TARGET} (proxied={CF_PROXIED})")

def cf_delete_record(zone_id: str, record_id: str, name: str) -> None:
    _cf_request("DELETE", f"/zones/{zone_id}/dns_records/{record_id}")
    log.info(f"Deleted CNAME {name}")

# ── Redis helpers ──────────────────────────────────────────────────────────────
def make_redis() -> redis.Redis:
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        password=REDIS_PASSWORD or None,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )

def enable_keyspace_notifications(r: redis.Redis) -> None:
    try:
        current = r.config_get("notify-keyspace-events").get("notify-keyspace-events", "")
        needed = set("KEg$x")
        if not needed.issubset(set(current)):
            new_val = "".join(needed | set(current))
            r.config_set("notify-keyspace-events", new_val)
            log.info(f"Enabled Redis keyspace notifications (was: {current!r}, now: {new_val!r})")
        else:
            log.info("Redis keyspace notifications already enabled")
    except Exception as e:
        log.warning(f"Could not set keyspace notifications: {e}")

def get_all_router_rules(r: redis.Redis) -> dict:
    """Use SCAN instead of KEYS to avoid blocking Redis."""
    rules = {}
    pattern = f"{REDIS_ROOT_KEY}/http/routers/*/rule"
    for key in r.scan_iter(pattern, count=100):
        val = r.get(key)
        if val:
            rules[key] = val
    return rules

# ── Hostname helpers ───────────────────────────────────────────────────────────
def extract_hosts(rule: str) -> list:
    return HOST_RE.findall(rule)

def is_in_zone(hostname: str) -> bool:
    return hostname == CF_ZONE_NAME or hostname.endswith(f".{CF_ZONE_NAME}")

# ── Core sync logic ────────────────────────────────────────────────────────────
class DnsSync:
    def __init__(self, r: redis.Redis, zone_id: str):
        self.r = r
        self.zone_id = zone_id

    def desired_hosts(self) -> set:
        hosts = set()
        for rule in get_all_router_rules(self.r).values():
            for h in extract_hosts(rule):
                if is_in_zone(h) and h not in EXCLUDE_HOSTS:
                    hosts.add(h)
        return hosts

    def sync_all(self) -> None:
        desired = self.desired_hosts()
        owned   = cf_list_records(self.zone_id)

        for host in desired:
            if host not in owned:
                try:
                    cf_create_record(self.zone_id, host)
                except Exception as e:
                    log.error(f"Failed to create {host}: {e}")
            else:
                log.debug(f"Record already exists: {host}")

        if CF_AUTO_DELETE:
            for name, rec in owned.items():
                if name not in desired:
                    try:
                        cf_delete_record(self.zone_id, rec["id"], name)
                    except Exception as e:
                        log.error(f"Failed to delete {name}: {e}")

    def watch(self) -> None:
        """Subscribe to Redis keyspace events with automatic reconnect."""
        while True:
            try:
                pubsub = self.r.pubsub()
                pubsub.subscribe(
                    "__keyevent@0__:set",
                    "__keyevent@0__:del",
                    "__keyevent@0__:expired",
                )
                log.info("Subscribed to Redis keyspace events")

                for message in pubsub.listen():
                    if message["type"] != "message":
                        continue
                    key = message["data"]
                    if not ROUTER_RE.match(key):
                        continue
                    log.debug(f"Keyspace event: {key}")
                    time.sleep(0.5)  # debounce
                    try:
                        self.sync_all()
                    except Exception as e:
                        log.error(f"sync_all failed: {e}")

            except redis.exceptions.ConnectionError as e:
                log.warning(f"Redis pubsub disconnected: {e} — reconnecting in 5s")
                time.sleep(5)
            except Exception as e:
                log.error(f"Unexpected error in watch loop: {e} — reconnecting in 5s")
                time.sleep(5)

# ── Entrypoint ────────────────────────────────────────────────────────────────
def main() -> None:
    log.info("traefik-dns-sync starting")
    log.info(f"  Zone:        {CF_ZONE_NAME}")
    log.info(f"  CNAME target: {CNAME_TARGET}")
    log.info(f"  Proxied:     {CF_PROXIED}")
    log.info(f"  Auto-delete: {CF_AUTO_DELETE}")
    log.info(f"  Exclude:     {EXCLUDE_HOSTS or '(none)'}")

    # Redis connect with retry
    r = None
    for attempt in range(1, 11):
        try:
            r = make_redis()
            r.ping()
            log.info(f"Connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
            break
        except Exception as e:
            log.warning(f"Redis not ready (attempt {attempt}/10): {e} — retrying in 3s")
            time.sleep(3)
    else:
        raise RuntimeError("Could not connect to Redis after 10 attempts")

    enable_keyspace_notifications(r)

    zone_id = cf_get_zone_id()
    log.info(f"Cloudflare zone ID: {zone_id}")

    sync = DnsSync(r, zone_id)

    log.info("Running initial sync...")
    sync.sync_all()
    log.info("Initial sync complete — watching for changes")

    sync.watch()

if __name__ == "__main__":
    main()
