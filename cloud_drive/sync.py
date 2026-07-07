"""Core sync logic: walk local path, compare with index, upload new/changed files."""

import fnmatch
import hashlib
import json
import os
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TransferSpeedColumn,
)

from cloud_drive import config as cfg_mod
from cloud_drive import index as idx_mod
from cloud_drive import storage

console = Console()


def _is_excluded(path: Path, root: Path, patterns: list[str]) -> bool:
    relative = str(path.relative_to(root))
    for pattern in patterns:
        if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(path.name, pattern):
            return True
    return False


def _make_s3_key(cfg: dict, root: Path, local_path: Path) -> str:
    # Use root.parent so the source folder name itself is included in the key.
    # e.g. syncing /hd/Documentos/report.pdf → seagate/Personal/Documentos/report.pdf
    relative = local_path.relative_to(root.parent)
    prefix = cfg["s3_prefix"].rstrip("/")
    return f"{prefix}/{relative.as_posix()}" if prefix else relative.as_posix()


def run_sync(
    cfg: dict,
    source: Path,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    source = source.resolve()
    if not source.exists():
        raise FileNotFoundError(f"Source path does not exist: {source}")

    client = storage.make_client(cfg)
    transfer_config = storage.make_transfer_config(cfg)
    index = idx_mod.Index(cfg["index_db"])

    # Load pending deletions for move detection (sha256 → {s3_key, size})
    pending_path = Path(cfg["index_db"]).expanduser().parent / "pending_deletions.json"
    pending: dict = {}
    size_index: dict[int, list[str]] = {}  # size → [sha256, ...]
    if pending_path.exists():
        try:
            pending = json.loads(pending_path.read_text()) or {}
            for sha, info in pending.items():
                size_index.setdefault(info["size"], []).append(sha)
        except Exception:
            pending = {}

    bucket = cfg["bucket"]
    if not storage.bucket_exists(client, bucket):
        raise RuntimeError(
            f"Bucket '{bucket}' does not exist. Run `cloud-drive init` first."
        )

    exclude_patterns = cfg.get("exclude", [])

    io_errors = []
    def _on_walk_error(exc):
        io_errors.append(exc)
        console.print(f"  [yellow]SKIP (I/O error)[/yellow] {exc.filename}")

    files = []
    for dirpath, _, filenames in os.walk(source, onerror=_on_walk_error):
        for name in filenames:
            files.append(Path(dirpath) / name)

    uploaded = skipped = failed = 0
    uploaded_bytes = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeElapsedColumn(),
        console=console,
        transient=True,
    ) as progress:
        scan_task = progress.add_task("Scanning…", total=len(files))

        for local_path in files:
            progress.update(scan_task, advance=1, description=local_path.name[:40])

            if _is_excluded(local_path, source, exclude_patterns):
                skipped += 1
                continue

            try:
                file_size = local_path.stat().st_size
            except OSError as exc:
                console.print(f"  [yellow]SKIP (unreadable)[/yellow] {local_path}: {exc}")
                skipped += 1
                continue

            if not force and not index.needs_upload(local_path):
                skipped += 1
                continue

            relative = str(local_path.relative_to(source))
            storage_class = cfg_mod.storage_class_for(cfg, relative)
            s3_key = _make_s3_key(cfg, source, local_path)

            if dry_run:
                console.print(
                    f"  [dim]would upload[/dim] [cyan]{relative}[/cyan] "
                    f"→ [yellow]{storage_class}[/yellow] ({file_size:,} bytes)"
                )
                uploaded += 1
                uploaded_bytes += file_size
                continue

            upload_task = progress.add_task(
                f"  {local_path.name[:38]}", total=file_size
            )

            def _progress(n_bytes, task_id=upload_task):
                progress.update(task_id, advance=n_bytes)

            try:
                # Move detection: if a pending deletion has same size, pre-hash to confirm
                moved = False
                if size_index and file_size in size_index:
                    h = hashlib.sha256()
                    with open(local_path, "rb") as f:
                        for chunk in iter(lambda: f.read(1 << 20), b""):
                            h.update(chunk)
                    pre_sha = h.hexdigest()
                    if pre_sha in pending:
                        old_s3_key = pending[pre_sha]["s3_key"]
                        etag = storage.copy_object(client, bucket, old_s3_key, s3_key, storage_class)
                        index.upsert(local_path, s3_key, pre_sha, etag, storage_class)
                        del pending[pre_sha]
                        size_index[file_size] = [s for s in size_index[file_size] if s != pre_sha]
                        pending_path.write_text(json.dumps(pending))
                        console.print(f"  [cyan]MOVED[/cyan] {local_path.name[:50]} (CopyObject, no re-upload)")
                        uploaded += 1
                        uploaded_bytes += file_size
                        moved = True

                if not moved:
                    etag, checksum = storage.upload(
                        client, local_path, bucket, s3_key,
                        storage_class, transfer_config, _progress,
                    )
                    index.upsert(local_path, s3_key, checksum, etag, storage_class)
                    uploaded += 1
                    uploaded_bytes += file_size
            except Exception as exc:
                console.print(f"  [red]FAILED[/red] {relative}: {exc}")
                failed += 1
            finally:
                progress.remove_task(upload_task)

    index.close()
    return {
        "uploaded": uploaded,
        "skipped": skipped,
        "failed": failed,
        "uploaded_bytes": uploaded_bytes,
        "io_errors": len(io_errors),
    }
