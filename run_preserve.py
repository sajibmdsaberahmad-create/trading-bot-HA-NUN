#!/usr/bin/env python3
"""
run_preserve.py — Post-training model preservation runner.

Usage (PowerShell / bash):
  python run_preserve.py --tag grandmaster-v1 --repo sajibmdsaberahmad-create/trading-bot-HA-NUN --github-token $GITHUB_TOKEN --hf-repo-id sajibmdsaberahmad-create/trading-bot-HA-NUN --hf-token $HF_TOKEN
"""
import os
import sys
import argparse
from core.model_preservation import preserve_all, create_model_manifest


def main():
    p = argparse.ArgumentParser(description='Post-training model preservation')
    p.add_argument('--tag', default='grandmaster-latest')
    p.add_argument('--repo', default=os.getenv('GITHUB_REPO', ''))
    p.add_argument('--github-token', default=os.getenv('GITHUB_TOKEN', ''))
    p.add_argument('--hf-repo-id', default=os.getenv('HF_REPO_ID', ''))
    p.add_argument('--hf-token', default=os.getenv('HF_TOKEN', ''))
    p.add_argument('--models', nargs='*', default=[
        'ppo_trader.zip',
        'models/transformer_model.pth',
        'models/lstm_model.h5',
        'models/fusion_state.json',
    ])
    args = p.parse_args()

    print('Creating manifest...')
    manifest = create_model_manifest(args.models)
    print('Manifest:', manifest)

    print('Preserving artifacts...')
    result = preserve_all(
        model_paths=args.models,
        github_repo=args.repo or None,
        github_token=args.github_token or None,
        hf_repo_id=args.hf_repo_id or None,
        hf_token=args.hf_token or None,
        tag=args.tag,
    )
    print('Result:', result)


if __name__ == '__main__':
    main()