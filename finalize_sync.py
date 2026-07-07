"""
Final cleanup after all folder uploads complete.
Deletes from S3 any pending_deletions entries that were NOT resolved
by a CopyObject during the upload phase (i.e. truly deleted files).
"""

import json
import sqlite3
import sys
from pathlib import Path

import boto3
import yaml
from rich.console import Console

console = Console()

CONFIG_PATH  = Path(__file__).parent / "config.yaml"
cfg          = yaml.safe_load(CONFIG_PATH.read_text())
BUCKET       = cfg["bucket"]
INDEX_DB     = str(Path(cfg["index_db"]).expanduser())
PENDING_PATH = Path(cfg["index_db"]).expanduser().parent / "pending_deletions.json"
REGION       = cfg.get("region", "us-east-1")

if not PENDING_PATH.exists() or PENDING_PATH.read_text().strip() in ("", "{}"):
    console.print("[green]No pending deletions to finalize.[/green]")
    sys.exit(0)

pending = json.loads(PENDING_PATH.read_text())
if not pending:
    console.print("[green]No pending deletions to finalize.[/green]")
    sys.exit(0)

console.print(f"[bold]Finalizing {len(pending):,} unresolved deletions from S3…[/bold]")

client = boto3.client("s3", region_name=REGION)
conn   = sqlite3.connect(INDEX_DB)

s3_keys     = [v["s3_key"] for v in pending.values()]
local_paths = [v["local_path"] for v in pending.values()]

deleted_count = error_count = 0
for i in range(0, len(s3_keys), 1000):
    batch = [{"Key": k} for k in s3_keys[i:i+1000]]
    resp  = client.delete_objects(Bucket=BUCKET, Delete={"Objects": batch})
    deleted_count += len(resp.get("Deleted", []))
    for e in resp.get("Errors", []):
        console.print(f"  [red]S3 error:[/red] {e['Key']} — {e['Message']}")
        error_count += 1

conn.executemany("DELETE FROM files WHERE local_path = ?", [(lp,) for lp in local_paths])
conn.commit()
conn.close()

PENDING_PATH.write_text("{}")
console.print(f"[green]Deleted {deleted_count:,} objects from S3, removed {len(local_paths):,} from index.[/green]")
if error_count:
    console.print(f"[red]{error_count} S3 errors.[/red]")
    sys.exit(1)
