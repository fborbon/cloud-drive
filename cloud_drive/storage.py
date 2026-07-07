"""S3 operations wrapper."""

import hashlib
import io
from pathlib import Path

import boto3
from boto3.s3.transfer import TransferConfig


def make_client(cfg: dict):
    return boto3.client("s3", region_name=cfg.get("region", "us-east-1"))


def make_transfer_config(cfg: dict) -> TransferConfig:
    return TransferConfig(
        multipart_threshold=cfg["multipart_threshold"],
        max_concurrency=cfg["threads"],
        use_threads=True,
    )


class _HashingReader:
    """Wraps a file object to compute SHA256 while boto3 reads it for upload."""
    def __init__(self, fobj, progress_cb=None):
        self._f = fobj
        self._h = hashlib.sha256()
        self._cb = progress_cb

    def read(self, n=-1):
        chunk = self._f.read(n)
        if chunk:
            self._h.update(chunk)
            if self._cb:
                self._cb(len(chunk))
        return chunk

    def seek(self, *args):
        # boto3 may seek to measure size; reset hash on rewind to start
        result = self._f.seek(*args)
        if args == (0,) or args == (0, 0):
            self._h = hashlib.sha256()
        return result

    def tell(self):
        return self._f.tell()

    def close(self):
        self._f.close()

    @property
    def checksum(self):
        return self._h.hexdigest()


def upload(
    client,
    local_path: Path,
    bucket: str,
    s3_key: str,
    storage_class: str,
    transfer_config: TransferConfig,
    progress_cb=None,
) -> tuple[str, str]:
    """Upload file to S3, returning (etag, sha256) computed in a single read pass."""
    extra = {"StorageClass": storage_class}
    with open(local_path, "rb") as f:
        reader = _HashingReader(f, progress_cb)
        client.upload_fileobj(
            reader,
            bucket,
            s3_key,
            ExtraArgs=extra,
            Config=transfer_config,
        )
        checksum = reader.checksum
    head = client.head_object(Bucket=bucket, Key=s3_key)
    etag = head["ETag"].strip('"')
    return etag, checksum


def list_objects(client, bucket: str, prefix: str) -> list[dict]:
    paginator = client.get_paginator("list_objects_v2")
    results = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            results.append(obj)
    return results


def download(client, bucket: str, s3_key: str, dest: Path, progress_cb=None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    client.download_file(bucket, s3_key, str(dest), Callback=progress_cb)


def copy_object(client, bucket: str, src_key: str, dst_key: str, storage_class: str) -> str:
    client.copy_object(
        Bucket=bucket,
        CopySource={"Bucket": bucket, "Key": src_key},
        Key=dst_key,
        StorageClass=storage_class,
        MetadataDirective="COPY",
    )
    head = client.head_object(Bucket=bucket, Key=dst_key)
    return head["ETag"].strip('"')


def delete(client, bucket: str, s3_key: str) -> None:
    client.delete_object(Bucket=bucket, Key=s3_key)


def bucket_exists(client, bucket: str) -> bool:
    try:
        client.head_bucket(Bucket=bucket)
        return True
    except client.exceptions.ClientError:
        return False


def create_bucket(client, bucket: str, region: str) -> None:
    if region == "us-east-1":
        client.create_bucket(Bucket=bucket)
    else:
        client.create_bucket(
            Bucket=bucket,
            CreateBucketConfiguration={"LocationConstraint": region},
        )
    client.put_bucket_versioning(
        Bucket=bucket,
        VersioningConfiguration={"Status": "Enabled"},
    )
