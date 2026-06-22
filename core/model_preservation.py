#!/usr/bin/env python3
"""
core/model_preservation.py — Model versioning and artifact preservation.

This module provides:
- GitHub Releases asset uploader (binary weights -> GitHub Releases)
- Hugging Face Hub uploader (primary model registry)
- Git-LFS metadata guard / model promotion workflow
- Artifact manifest generation (preserves reproducibility without pushing weights to Git)
"""

import os
import io
import sys
import json
import zipfile
import hashlib
import requests
from typing import Optional, List
from datetime import datetime, timezone


def compute_sha256(path: str, block_size: int = 1024 * 1024) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, 'rb') as f:
            while True:
                b = f.read(block_size)
                if not b:
                    break
                h.update(b)
        return h.hexdigest()
    except Exception:
        return None


def create_model_manifest(model_paths: List[str], manifest_path: str = "models/model_manifest.json") -> str:
    manifest = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'models': [],
    }
    for p in model_paths:
        if not os.path.isfile(p):
            continue
        manifest['models'].append({
            'path': p,
            'filename': os.path.basename(p),
            'size_bytes': os.path.getsize(p),
            'sha256': compute_sha256(p),
        })
    os.makedirs(os.path.dirname(manifest_path), exist_ok=True)
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)
    return manifest_path


def push_github_release(model_paths: List[str],
                        repo: str,
                        token: str,
                        tag: str = "grandmaster-latest",
                        name: str = "Grandmaster Release",
                        body: str = "") -> bool:
    headers = {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json',
    }
    base = f'https://api.github.com/repos/{repo}'
    release_url = f'{base}/releases'
    release_id = None
    try:
        r = requests.get(f'{release_url}/tags/{tag}', headers=headers, timeout=20)
        if r.status_code == 200:
            rel = r.json()
            release_id = rel['id']
            for asset in rel.get('assets', []):
                try:
                    requests.delete(asset['url'], headers=headers, timeout=20)
                except Exception:
                    pass
        else:
            hf_link = f'https://huggingface.co/{hf_repo_id or repo}' if hf_repo_id else ''
            body_auto = (
                f'{body}\n\n'
                f'### Model Weights\n'
                f'Raw binary weights are preserved on Hugging Face Hub:\n'
                f'🔗 [Download Model Weights from Hugging Face Hub]({hf_link})\n\n'
                f'### Verification\n'
                f'- Manifest: `models/model_manifest.json`\n'
                f'- Training history: `training_history_*.json`\n'
            ) if hf_link else body
            payload = {
                'tag_name': tag,
                'name': name,
                'body': body_auto,
                'draft': False,
                'prerelease': False,
            }
            r = requests.post(release_url, headers=headers, json=payload, timeout=20)
            if r.status_code not in (200, 201):
                return False
            release_id = r.json()['id']
    except Exception:
        return False
    if release_id is None:
        return False
    upload_url = f'https://uploads.github.com/repos/{repo}/releases/{release_id}/assets'
    ok = True
    for model_path in model_paths:
        if not os.path.isfile(model_path):
            continue
        filename = os.path.basename(model_path)
        try:
            with open(model_path, 'rb') as f:
                data = f.read()
            headers_upload = {
                'Authorization': f'token {token}',
                'Accept': 'application/vnd.github.v3+json',
                'Content-Type': 'application/octet-stream',
            }
            r = requests.post(f'{upload_url}?name={filename}', headers=headers_upload, data=data, timeout=120)
            if r.status_code not in (200, 201):
                ok = False
        except Exception:
            ok = False
    return ok


def push_huggingface_hub(model_paths: List[str],
                         repo_id: str,
                         token: Optional[str] = None,
                         commit_message: str = "Add model weights") -> bool:
    token = token or os.getenv('HF_TOKEN') or os.getenv('HUGGINGFACE_TOKEN')
    if not token or not repo_id:
        return False
    try:
        from huggingface_hub import HfApi
        api = HfApi(token=token)
        for model_path in model_paths:
            if not os.path.isfile(model_path):
                continue
            try:
                api.upload_file(
                    path_or_fileobj=model_path,
                    path_in_repo=os.path.basename(model_path),
                    repo_id=repo_id,
                    repo_type='model',
                    commit_message=commit_message,
                )
            except Exception:
                pass
        return True
    except ImportError:
        return False


def preserve_all(model_paths: List[str],
                 github_repo: Optional[str] = None,
                 github_token: Optional[str] = None,
                 hf_repo_id: Optional[str] = None,
                 hf_token: Optional[str] = None,
                 tag: str = "grandmaster-latest") -> dict:
    manifest_path = create_model_manifest(model_paths)
    gh_ok = push_github_release(model_paths, repo=github_repo, token=github_token, tag=tag) if github_repo and github_token else False
    hf_ok = push_huggingface_hub(model_paths, repo_id=hf_repo_id or github_repo or '', token=hf_token) if hf_repo_id and hf_token else False
    return {
        'manifest': manifest_path,
        'github_release': gh_ok,
        'huggingface_hub': hf_ok,
    }
