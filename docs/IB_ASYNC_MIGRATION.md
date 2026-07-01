# ib_insync → ib_async migration

**Status:** Phase 2 complete (shim live) · Phase 3 soak optional

## Done

| Item | Status |
|------|--------|
| `core/ib_client.py` shim | ✅ `IB_CLIENT_BACKEND=ib_insync` (default) or `ib_async` |
| All `core/` imports routed via shim | ✅ connector, broker, entry_pipeline, data, scanner, … |
| `ib_async` in requirements | ✅ optional install alongside `ib-insync` |
| `tests/test_ib_client.py` | ✅ backend selection |
| Connector boot log | ✅ prints active backend |

## Usage

```bash
# Default — legacy ib_insync (unchanged behavior)
./start.sh

# Try maintained fork (pip install ib_async first)
export IB_CLIENT_BACKEND=ib_async
./start.sh
```

## Remaining (optional soak)

- [ ] 5 paper sessions on `IB_CLIENT_BACKEND=ib_async`
- [ ] Replay-live full farm on ib_async
- [ ] Flip default to `ib_async` after soak
- [ ] Remove direct `ib-insync` dependency

## Rollback

```bash
export IB_CLIENT_BACKEND=ib_insync
```

## Verification checklist

- [x] Unit tests pass with shim
- [ ] Connect / reclaim client id 10197 (manual paper)
- [ ] Bracket parent + children on paper
- [ ] Extended-hours limit entry + flatten
- [ ] execDetails → fill reconciler round-trip
- [ ] Scanner universe lock
