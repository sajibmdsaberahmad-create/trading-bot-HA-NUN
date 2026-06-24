# Model Preservation — Quick Reference

## One-Time Setup

1. Create `.env` from `.env.example` and fill real values.
2. Install deps:
```bash
./venv/bin/pip install -r requirements.txt
```

## Automatic Preservation

After training finishes via `main.py` or `AdvancedTrainingPipeline.run_all()`,
the system automatically:
- Writes `models/model_manifest.json`
- Uploads binaries to GitHub Releases (`grandmaster-latest`)
- Uploads binaries to HuggingFace Hub (`HF_REPO_ID`)

All Git-tracked artifacts are lightweight:
- `models/model_manifest.json`
- `training_history_*.json`

## Manual Preservation

```bash
./venv/bin/python run_preserve.py \
  --tag grandmaster-v1 \
  --repo sajibmdsaberahmad-create/trading-bot-HA-NUN \
  --github-token "$GITHUB_TOKEN" \
  --hf-repo-id sajibmdsaberahmad-create/trading-bot-HA-NUN \
  --hf-token "$HF_TOKEN"
```

## Verify

- GitHub Releases: `https://github.com/sajibmdsaberahmad-create/trading-bot-HA-NUN/releases`
- HuggingFace Hub: `huggingface-cli repo-info sajibmdsaberahmad-create/trading-bot-HA-NUN --repo-type model`

## Rules

- Do not commit raw weights to Git
- Use GitHub Releases or HuggingFace Hub for binaries
- Keep `GITHUB_TOKEN` and `HF_TOKEN` secret