# traefik-dns-sync
# Watches Traefik's Redis store and auto-creates/deletes Cloudflare CNAME records.

## How it works
1. On startup: enables Redis keyspace notifications, then does a full sync
2. Subscribes to Redis pub/sub keyspace events
3. When a `traefik/http/routers/*/rule` key is set or deleted, runs a full reconciliation
4. Creates CNAME records for new hostnames, optionally deletes records for removed ones
5. Only touches records it created (identified by `traefik-dns-sync` comment on Cloudflare)

## Docker Compose

```yaml
  traefik-dns-sync:
    image: traefik-dns-sync:latest  # or build: ./traefik-dns-sync
    container_name: traefik-dns-sync
    restart: unless-stopped
    environment:
      # Redis
      REDIS_HOST: 192.168.206.11        # Master Redis IP
      REDIS_PORT: 6379
      REDIS_PASSWORD: ${REDIS_PASSWORD}
      REDIS_ROOT_KEY: traefik           # Must match traefik-kop rootKey

      # Cloudflare
      CF_API_TOKEN: ${CF_API_TOKEN}
      CF_ZONE_NAME: tristandk.be
      CNAME_TARGET: gent.tristandk.be   # What all CNAMEs point to

      # Behaviour
      CF_PROXIED: "true"                # Orange cloud on/off
      CF_AUTO_DELETE: "false"           # Delete records when route removed
      EXCLUDE_HOSTS: "traefik.tristandk.be,traefik-ingress-02.tristandk.be,traefik-ingress-03.tristandk.be"
      LOG_LEVEL: INFO
    networks:
      - frontend-net
```

## Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| REDIS_HOST | ✅ | - | Redis hostname/IP |
| REDIS_PORT | | 6379 | Redis port |
| REDIS_PASSWORD | | - | Redis password |
| REDIS_ROOT_KEY | | traefik | Must match Traefik's rootKey |
| CF_API_TOKEN | ✅ | - | Cloudflare API token (DNS edit) |
| CF_ZONE_NAME | ✅ | - | e.g. tristandk.be |
| CNAME_TARGET | ✅ | - | e.g. gent.tristandk.be |
| CF_PROXIED | | true | Orange cloud toggle |
| CF_AUTO_DELETE | | false | Delete records when route removed |
| EXCLUDE_HOSTS | | - | Comma-separated hostnames to ignore |
| LOG_LEVEL | | INFO | DEBUG/INFO/WARNING/ERROR |

## Notes
- Only creates records for hostnames in CF_ZONE_NAME
- Only deletes records with comment `traefik-dns-sync` (records it created)
- Manually edited records are never deleted or modified
- Reacts instantly via Redis pub/sub, no polling interval
- Run only once (not on every ingress node) — one instance per Redis master is enough
