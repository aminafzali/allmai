"""S3-compatible object storage (future backend). Implements the same
StorageProvider protocol; access stays gated behind our API."""

import boto3

from app.core.config import get_settings


class S3Storage:
    provider_name = "s3"
    def __init__(self) -> None:
        s = get_settings()
        self.bucket = s.S3_BUCKET
        self._client = boto3.client(
            "s3",
            endpoint_url=s.S3_ENDPOINT_URL,
            aws_access_key_id=s.S3_ACCESS_KEY,
            aws_secret_access_key=s.S3_SECRET_KEY,
            region_name=s.S3_REGION,
        )

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return key

    def get(self, key: str) -> bytes:
        obj = self._client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def presigned_get(self, key: str, expires_seconds: int = 900) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_seconds,
        )


def get_storage():
    """Factory: local disk by default (STORAGE_BACKEND=local), S3 when set."""
    from app.storage.local import LocalStorageProvider

    if get_settings().STORAGE_BACKEND == "s3":
        return S3Storage()
    return LocalStorageProvider()
