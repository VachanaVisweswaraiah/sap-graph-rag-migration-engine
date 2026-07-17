<!--
NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.
-->

# Backup & restore

Local dev/demo scope — matches how this actually runs today (one Docker Compose
service on a single machine, no remote/production hosting yet).

## The real source of truth is not the running graph

The graph is fully rebuildable from three inputs, all committed to the repo:

1. `data/raw/kg_nodes.csv` + `kg_edges.csv` — the base graph (immutable, protected).
2. `uv run kgme enrich disposition` — adds the `MIGRATES_TO` edges derived from NovaPharm
   Biologics' own migration notes (3 edges as of this writing).
3. `uv run kgme enrich s4-catalog` — adds `s4_*` properties from the external SAP
   Simplification Catalog, if/when that's fetched (deferred by design — see `CLAUDE.md`).

**This means the graph itself doesn't strictly need a backup — it needs a documented
rebuild procedure**, which is more robust than a database dump: it's auditable (you can
see exactly what was derived and how, per `docs/AUDIT.md`) and it can't restore stale
data, since it always regenerates from the current source CSVs.

## Rebuild from scratch

```bash
make up                                    # start FalkorDB
KGME_ALLOW_WIPE=1 uv run kgme-load --wipe  # wipe + reload the base graph
uv run kgme enrich disposition             # re-add the derived MIGRATES_TO edges
# uv run kgme enrich s4-catalog             # only if the SAP catalog has been fetched
```

Confirm with `uv run kgme db check --deep`, or `GET /health`.

## If you actually need the live graph state (not just a rebuild)

For example, if someone has manually promoted an `inferred` edge to `documented`
after human review (per `CLAUDE.md`'s non-negotiable #2) — that's real, one-off state
that a rebuild from the source CSVs would **not** reproduce, since the CSVs themselves
weren't changed. Use `redis-cli`/FalkorDB's own persistence:

```bash
# Snapshot: trigger an AOF rewrite + flush now, then copy the file out of the volume
docker exec kgme-falkordb redis-cli -a "$FALKORDB_PASSWORD" BGREWRITEAOF
docker run --rm -v kg-migration-engine_falkordb_data:/data \
  -v "$(pwd)/backups:/backup" alpine \
  sh -c "cp -r /data/appendonlydir /backup/appendonlydir-$(date +%Y%m%d)"

# Restore: stop the stack, replace the volume's appendonlydir, start again
make down
docker run --rm -v kg-migration-engine_falkordb_data:/data \
  -v "$(pwd)/backups/appendonlydir-YYYYMMDD:/restore" alpine \
  sh -c "rm -rf /data/appendonlydir && cp -r /restore /data/appendonlydir"
make up
```

## A real bug this session found and fixed, worth knowing

The Docker volume was, until this session, mounted at `/data` in `docker-compose.yml`
— but FalkorDB's actual Redis persistence directory is `/var/lib/falkordb/data`, a
different path entirely inside the container's own ephemeral filesystem layer. That
meant the named volume was **silently persisting nothing** — restarting the container
in place never exposed this (the running process kept its data in memory the whole
time), but a full `docker compose down` + `up` (or any real container recreation)
lost the entire graph with zero warning or error. This is now fixed (the volume mounts
the correct path) and verified by actually testing a full container recreation, not
just a restart — confirmed 55 nodes / 47 edges survive a `down` + `up` cycle. If
you're troubleshooting a "why is the graph suddenly empty" issue on an older checkout,
this mount-path bug is almost certainly why.
