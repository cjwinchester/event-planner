"""AWS S3 storage connector — publishes static schedule exports.

Options (non-secret): bucket, region, path_template, public_base_url.
  path_template — where an event's files go; placeholders {org} and {event}.
                  Default "{org}/{event}/". IRE can use "{event}/" to keep
                  existing schedules.ire.org URLs.
  public_base_url — CDN/custom domain in front of the bucket
                  (e.g. https://schedules.ire.org/). Defaults to the bucket URL.
Credentials: access_key_id / secret_access_key, or leave blank to use the
server's IAM role / environment (recommended).
"""

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from ...base import BaseConnector, Capability, ConnectionStatus, ConnectorError, CredentialField
from ...registry import register


@register
class S3Connector(BaseConnector):
    slug = "s3"
    display_name = "Amazon S3"
    description = "Publish schedule feeds and static exports to an S3 bucket."
    docs_url = "https://docs.aws.amazon.com/s3/"
    capabilities = frozenset({Capability.STORAGE})
    credential_fields = [
        CredentialField("access_key_id", "Access key ID", secret=False, required=False),
        CredentialField("secret_access_key", "Secret access key", required=False),
    ]
    CACHE_CONTROL = "public, max-age=300"  # feeds change during the event; 5-minute cache

    def _client(self, ctx):
        kwargs = {"region_name": ctx.get("region") or None}
        if ctx.get("access_key_id"):
            kwargs.update(
                aws_access_key_id=ctx.get("access_key_id"), aws_secret_access_key=ctx.get("secret_access_key")
            )
        return boto3.client("s3", **kwargs)

    def base_url(self, ctx, prefix):
        ctx.require("bucket")
        base = ctx.get("public_base_url")
        if not base:
            region = ctx.get("region") or "us-east-1"
            base = f"https://{ctx.get('bucket')}.s3.{region}.amazonaws.com/"
        return base.rstrip("/") + "/" + prefix

    def put(self, ctx, key, content, content_type):
        ctx.require("bucket")
        try:
            self._client(ctx).put_object(
                Bucket=ctx.get("bucket"),
                Key=key,
                Body=content,
                ContentType=content_type,
                CacheControl=self.CACHE_CONTROL,
            )
        except (BotoCoreError, ClientError) as exc:
            raise ConnectorError(f"S3 upload of {key} failed: {exc}") from exc
        return key

    def test_connection(self, ctx):
        try:
            ctx.require("bucket")
            self._client(ctx).head_bucket(Bucket=ctx.get("bucket"))
        except (BotoCoreError, ClientError, ConnectorError) as exc:
            return ConnectionStatus(False, str(exc))
        return ConnectionStatus(True, "Bucket reachable.")
