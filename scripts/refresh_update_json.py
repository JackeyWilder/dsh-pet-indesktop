# -*- coding: utf-8 -*-
"""从自用 fork 的最新 Release 重新生成仓库根的 update.json。

用途：GitHub Release API 不可达时，更新器回退到 jsDelivr 上的 update.json；
那份清单里的 url/size/sha256 必须与 Release 上的真实资产一致，否则下载校验不过。
GitHub 的 Release API 给每个 asset 都带 size 与 digest（sha256:...），直接抄即可。

用法（必须走系统代理，本机直连 github.com 不通）：
    $env:HTTPS_PROXY='http://127.0.0.1:7892'
    python scripts/refresh_update_json.py [--repo JackeyWilder/dsh-pet-indesktop] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _api(repo: str) -> dict:
    out = subprocess.run(
        ['gh', 'api', f'/repos/{repo}/releases/latest'],
        capture_output=True, text=True, encoding='utf-8',
    )
    if out.returncode != 0:
        sys.exit(f'gh api 失败：{out.stderr.strip()}')
    return json.loads(out.stdout)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--repo', default='JackeyWilder/dsh-pet-indesktop')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    payload = _api(args.repo)
    tag = payload['tag_name']
    assets = {}
    for asset in payload['assets']:
        name = asset['name']
        digest = str(asset.get('digest') or '')
        if not digest.startswith('sha256:'):
            sys.exit(f'{name} 缺少 sha256 digest，拒绝写入不完整清单')
        assets[name] = {
            'fileName': name,
            'urls': [asset['browser_download_url']],
            'size': int(asset['size']),
            'sha256': digest.split(':', 1)[1],
            'platform': '',
            'kind': '',
            'variant': '',
        }
    if not assets:
        sys.exit('最新 Release 没有任何资产，拒绝写空清单')

    manifest = {
        'version': tag[1:] if tag.startswith('v') else tag,
        'html_url': payload['html_url'],
        'notes': f'自用构建 {tag}（由 scripts/refresh_update_json.py 从 Release API 生成）',
        'assets': dict(sorted(assets.items())),
    }
    text = json.dumps(manifest, ensure_ascii=False, indent=2) + '\n'
    target = REPO_ROOT / 'update.json'
    if args.dry_run:
        print(text)
        return 0
    target.write_text(text, encoding='utf-8', newline='\n')
    print(f'wrote {target} ({len(assets)} assets, version {manifest["version"]})')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
