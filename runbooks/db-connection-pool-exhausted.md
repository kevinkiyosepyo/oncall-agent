---
title: Database Connection Pool Exhausted
summary: Application cannot obtain database connections — symptoms are connection timeout errors, "pool exhausted" or "too many clients" exceptions, and requests failing fast under normal traffic.
applies_to: DBConnectionPoolExhausted, DBConnectionErrors
---

# Database Connection Pool Exhausted

## Diagnose

1. Check current pool usage vs. configured pool size, and Postgres
   `pg_stat_activity` for idle-in-transaction sessions holding connections.
2. Look for a recent change that reduced pool size, added a long-running
   transaction, or introduced a connection leak (acquire without release).
3. Rule out a slow-query pileup: if queries got slower, connections are held
   longer and the pool drains at normal traffic levels.

## Mitigate

- Connection leak from a recent change: roll back the change.
- Idle-in-transaction sessions: terminate them
  (`pg_terminate_backend`) and set `idle_in_transaction_session_timeout`.
- Genuine capacity: raise the pool size only if the database has headroom —
  otherwise you move the outage from the app to the database.

## Escalate

Page the database owner if Postgres itself is at `max_connections`, since
raising app pool sizes will make that worse.
