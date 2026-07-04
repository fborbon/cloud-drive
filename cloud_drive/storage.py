"""S3 operations wrapper."""

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


def upload(
    client,
    local_path: Path,
    bucket: str,
    s3_key: str,
    storage_class: str,
    transfer_config: TransferConfig,
    progress_cb=None,
) -> str:
    extra = {"StorageClass": storage_class}
    client.upload_file(
        str(local_path),
        bucket,
        s3_key,
        ExtraArgs=extra,
        Config=transfer_config,
        Callback=progress_cb,
    )
    head = client.head_object(Bucket=bucket, Key=s3_key)
    return head["ETag"].strip('"')


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
