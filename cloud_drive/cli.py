"""Cloud-drive CLI — Dropbox-like S3 backup tool."""

import os
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich import box

from cloud_drive import config as cfg_mod
from cloud_drive import index as idx_mod
from cloud_drive import storage
from cloud_drive import sync as sync_mod

console = Console()

_PASS_CFG = click.make_pass_decorator(dict)


def _human(n_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n_bytes < 1024:
            return f"{n_bytes:.1f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} PB"


@click.group()
@click.option("--config", "-c", default=None, help="Path to config.yaml")
@click.pass_context
def cli(ctx, config):
    """Cloud-drive: back up your external HD to AWS S3."""
    ctx.ensure_object(dict)
    ctx.obj = cfg_mod.load(config)


# ── init ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("bucket")
@click.option("--region", default="us-east-1", show_default=True)
@_PASS_CFG
def init(cfg, bucket, region):
    """Create the S3 bucket and enable versioning.

    \b
    Example:
      cloud-drive init my-backup-bucket-2024
    """
    client = storage.make_client(cfg)
    if storage.bucket_exists(client, bucket):
        console.print(f"[yellow]Bucket '{bucket}' already exists.[/yellow]")
        return
    storage.create_bucket(client, bucket, region)
    console.print(
        f"[green]✓[/green] Bucket [bold]{bucket}[/bold] created in [bold]{region}[/bold] "
        f"with versioning enabled."
    )
    console.print(
        "\nAdd this to your [bold]config.yaml[/bold]:\n"
        f"  [cyan]bucket: {bucket}[/cyan]"
    )


# ── sync ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("source", type=click.Path(exists=True, file_okay=False))
@click.option("--dry-run", is_flag=True, help="Show what would be uploaded without uploading.")
@click.option("--force", is_flag=True, help="Re-upload even if file appears unchanged.")
@_PASS_CFG
def sync(cfg, source, dry_run, force):
    """Sync SOURCE directory to S3 (uploads new and changed files only).

    \b
    Example:
      cloud-drive sync /media/my-external-hd
      cloud-drive sync /media/my-external-hd --dry-run
    """
    if not cfg["bucket"]:
        console.print("[red]No bucket configured. Add 'bucket:' to config.yaml or run `cloud-drive init`.[/red]")
        sys.exit(1)

    label = "[dim](dry run)[/dim] " if dry_run else ""
    console.print(f"\n[bold]Cloud-drive sync[/bold] {label}[cyan]{source}[/cyan] → [yellow]{cfg['bucket']}/{cfg['s3_prefix']}[/yellow]\n")

    result = sync_mod.run_sync(cfg, Path(source), dry_run=dry_run, force=force)

    verb = "Would upload" if dry_run else "Uploaded"
    io_warn = f" · [yellow]I/O errors (skipped dirs): {result['io_errors']}[/yellow]" if result.get("io_errors") else ""
    console.print(
        f"\n[green]✓[/green] {verb} [bold]{result['uploaded']}[/bold] files "
        f"([bold]{_human(result['uploaded_bytes'])}[/bold]) · "
        f"Skipped [bold]{result['skipped']}[/bold] · "
        f"Failed [bold]{result['failed']}[/bold]{io_warn}"
    )
    if result["failed"]:
        sys.exit(1)


# ── list ──────────────────────────────────────────────────────────────────────

@cli.command(name="list")
@click.option("--prefix", default=None, help="Filter by S3 key prefix.")
@click.option("--remote", is_flag=True, help="List from S3 directly instead of local index.")
@_PASS_CFG
def list_files(cfg, prefix, remote):
    """List synced files."""
    if remote:
        client = storage.make_client(cfg)
        effective_prefix = prefix or cfg["s3_prefix"]
        objects = storage.list_objects(client, cfg["bucket"], effective_prefix)

        table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold blue")
        table.add_column("S3 Key", style="cyan", no_wrap=False)
        table.add_column("Size", justify="right", style="green")
        table.add_column("Last Modified", justify="right", style="dim")

        for obj in objects:
            table.add_row(
                obj["Key"],
                _human(obj["Size"]),
                obj["LastModified"].strftime("%Y-%m-%d %H:%M"),
            )
        console.print(table)
        console.print(f"  [dim]{len(objects)} objects in s3://{cfg['bucket']}/{effective_prefix}[/dim]")
    else:
        index = idx_mod.Index(cfg["index_db"])
        rows = index.all_synced()
        index.close()

        table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold blue")
        table.add_column("Local Path", style="cyan")
        table.add_column("S3 Key", style="dim")
        table.add_column("Size", justify="right", style="green")
        table.add_column("Class", justify="right", style="yellow")
        table.add_column("Synced At", justify="right", style="dim")

        for row in rows:
            if prefix and prefix not in row["s3_key"]:
                continue
            table.add_row(
                row["local_path"],
                row["s3_key"],
                _human(row["size"]),
                row["storage_class"],
                row["synced_at"],
            )
        console.print(table)


# ── restore ───────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("s3_key")
@click.argument("destination", type=click.Path())
@_PASS_CFG
def restore(cfg, s3_key, destination):
    """Download a file from S3.

    \b
    Example:
      cloud-drive restore hd-backup/Photos/2023/IMG_001.jpg ./restored/
    """
    client = storage.make_client(cfg)
    dest = Path(destination)
    if dest.is_dir():
        dest = dest / Path(s3_key).name

    console.print(f"Downloading [cyan]{s3_key}[/cyan] → [green]{dest}[/green]")
    storage.download(client, cfg["bucket"], s3_key, dest)
    console.print(f"[green]✓[/green] Saved to {dest}")


# ── status ────────────────────────────────────────────────────────────────────

@cli.command()
@_PASS_CFG
def status(cfg):
    """Show sync statistics from the local index."""
    index = idx_mod.Index(cfg["index_db"])
    stats = index.stats()
    index.close()

    console.print()
    console.print(f"  [bold]Bucket[/bold]        {cfg['bucket'] or '[red]not configured[/red]'}")
    console.print(f"  [bold]S3 prefix[/bold]     {cfg['s3_prefix']}")
    console.print(f"  [bold]Storage class[/bold] {cfg['default_storage_class']}")
    console.print(f"  [bold]Index DB[/bold]      {cfg['index_db']}")
    console.print()
    console.print(f"  [bold]Files indexed[/bold] {stats['count']:,}")
    console.print(f"  [bold]Total size[/bold]    {_human(stats['total_bytes'])}")
    console.print()
