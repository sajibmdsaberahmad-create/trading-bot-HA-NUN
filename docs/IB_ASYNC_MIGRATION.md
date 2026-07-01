# ib_insync → ib_async migration plan

**Status:** Planned (not started)  
**Risk:** `ib_insync` is archived; IB API updates may break unmaintained client.

## Why migrate

- Live scalper depends on `ib_insync` for orders, market data, account snapshots.
- No upstream fixes for IB Gateway API changes or Python 3.12+ issues.
- Community fork [`ib_async`](https://github.com/ib-api-reloaded/ib_async) maintains compatibility.

## Scope

| Module | IB usage | Migration effort |
|--------|----------|------------------|
| `core/connector.py` | Connection, events, reconnect | High |
| `core/broker.py` | Bracket orders | High |
| `core/entry_pipeline.py` | Limit/market orders | Medium |
| `core/fill_tracker.py` | execDetails | Medium |
| `core/scanner.py` | ScannerSubscription | Low |
| `tests/*` | Mock `ib_insync` modules | Low |

## Phases

1. **Pin + document** — keep `ib-insync==0.9.86` in `requirements-lock.txt` (done).
2. **Abstraction shim** — `core/ib_client.py` re-export order/contract types; swap import path only.
3. **Paper replay** — run replay-live against `ib_async` on paper Gateway.
4. **Live cutover** — feature flag `IB_CLIENT_BACKEND=ib_async`, soak 5 sessions.
5. **Remove ib_insync** — delete shim after soak.

## Verification checklist

- [ ] Connect / reclaim client id 10197
- [ ] Bracket parent + children on paper
- [ ] Extended-hours limit entry + flatten
- [ ] execDetails → fill reconciler round-trip
- [ ] Scanner universe lock

## Rollback

Set `IB_CLIENT_BACKEND=ib_insync` (default until phase 4 complete).
