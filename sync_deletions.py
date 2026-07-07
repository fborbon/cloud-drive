"""
Find files deleted/moved locally and stage them for S3 cleanup.
Instead of immediately deleting, saves pending deletions by SHA256 so
sync.py can detect moves via S3 CopyObject (no re-upload needed).
"""

import json
import sqlite3
import sys
from pathlib import Path

import yaml
from rich.console import Console

console = Console()

CONFIG_PATH = Path(__file__).parent / "config.yaml"
cfg = yaml.safe_load(CONFIG_PATH.read_text())

INDEX_DB         = str(Path(cfg["index_db"]).expanduser())
PENDING_PATH     = Path(cfg["index_db"]).expanduser().parent / "pending_deletions.json"

FOLDERS = [
    "/media/patito/seagate/Personal/Datos familia",
    "/media/patito/seagate/Personal/Documentos",
    "/media/patito/seagate/Personal/Musica",
    "/media/patito/seagate/Personal/Programar",
    "/media/patito/seagate/Personal/Videos/Baile/Salsemba",
]

conn = sqlite3.connect(INDEX_DB)
like_clauses = " OR ".join(f"local_path LIKE ?" for _ in FOLDERS)
params = [f"{f}/%" for f in FOLDERS]
rows = conn.execute(
    f"SELECT local_path, s3_key, sha256, size FROM files WHERE {like_clauses}", params
).fetchall()
conn.close()

console.print(f"[bold]Checking {len(rows):,} indexed files for deletions…[/bold]")

missing = [(lp, sk, sh, sz) for lp, sk, sh, sz in rows if not Path(lp).exists()]

if not missing:
    console.print("[green]No deletions found — S3 is in sync with local.[/green]")
    PENDING_PATH.write_text("{}")
    sys.exit(0)

console.print(f"[yellow]{len(missing):,} files missing locally → staging for move detection[/yellow]")

# Build pending dict keyed by sha256. If multiple files share the same sha256
# (duplicate content), keep the first one found.
pending: dict = {}
for local_path, s3_key, sha256, size in missing:
    if sha256 and sha256 not in pending:
        pending[sha256] = {"s3_key": s3_key, "size": size, "local_path": local_path}

PENDING_PATH.write_text(json.dumps(pending, indent=2))
console.print(f"[cyan]{len(pending):,} unique SHA256s staged in {PENDING_PATH}[/cyan]")
console.print("[dim]Actual S3 deletion happens in finalize_sync.py after uploads.[/dim]")
