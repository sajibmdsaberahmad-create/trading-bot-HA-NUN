# Sniper-Lock Integration

## State
- `core/config.py`: sniper config flags added.
- `core/scalper_sniper_integration.py`: drop-in bridge added.
- `core/scalper_runner.py`: not patched because direct edits were not persisting reliably in this environment.

## Run / Verify
Use `core/scalper_sniper_integration.py` as the integration point.
It wraps startup and scanning so sniper cannot block trading.

## If you want me to proceed
I can finish by making the Small, surgical edit only once the file surface is stable.