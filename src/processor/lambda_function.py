import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError


LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

TABLE_NAME = os.environ.get("TABLE_NAME", "mtip-main")
UPLOAD_BUCKET = os.environ.get("UPLOAD_BUCKET", "")
RESULTS_BUCKET = os.environ.get("RESULTS_BUCKET", "")
MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))
MAX_LABELS = int(os.environ.get("MAX_LABELS", "10"))
MIN_CONFIDENCE = float(os.environ.get("MIN_CONFIDENCE", "75"))
PROCESSING_LEASE_SECONDS = int(os.environ.get("PROCESSING_LEASE_SECONDS", "120"))

OBJECT_KEY_PATTERN = re.compile(
    r"^tenants/(?P<tenant>[a-z0-9][a-z0-9-]{1,62})/uploads/"
    r"(?P<job_id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})/image\.(?P<extension>jpg|png)$"
)
ALLOWED_CONTENT_TYPES = {"image/jpeg", "image/png"}

DYNAMODB = boto3.resource("dynamodb")
TABLE = DYNAMODB.Table(TABLE_NAME)
S3 = boto3.client("s3")
REKOGNITION = boto3.client("rekognition")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def log_event(level, event_name, **fields):
    payload = {"event": event_name, **fields}
    getattr(LOGGER, level)(json.dumps(payload, separators=(",", ":")))


def get_job(tenant_id, job_id):
    result = TABLE.get_item(
        Key={"PK": f"TENANT#{tenant_id}", "SK": f"JOB#{job_id}"},
        ConsistentRead=True,
    )
    return result.get("Item")


def acquire_processing_lease(tenant_id, job_id, event_id):
    epoch_now = int(time.time())
    lease_until = epoch_now + PROCESSING_LEASE_SECONDS
    try:
        result = TABLE.update_item(
            Key={"PK": f"TENANT#{tenant_id}", "SK": f"JOB#{job_id}"},
            UpdateExpression=(
                "SET #status = :processing, processingEventId = :event_id, "
                "processingLeaseUntil = :lease_until, updatedAt = :updated_at "
                "ADD processingAttempts :one"
            ),
            ConditionExpression=(
                "#status = :awaiting OR #status = :failed OR "
                "(#status = :processing AND processingLeaseUntil < :epoch_now)"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":processing": "PROCESSING",
                ":awaiting": "AWAITING_UPLOAD",
                ":failed": "FAILED",
                ":event_id": event_id,
                ":lease_until": lease_until,
                ":epoch_now": epoch_now,
                ":updated_at": utc_now(),
                ":one": 1,
            },
            ReturnValues="ALL_NEW",
        )
        return result["Attributes"]
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise

        latest = get_job(tenant_id, job_id)
        if latest and latest.get("status") == "COMPLETED":
            return None
        raise RuntimeError("Job processing lease is currently held") from exc


def mark_failed(tenant_id, job_id, event_id, error_type):
    try:
        TABLE.update_item(
            Key={"PK": f"TENANT#{tenant_id}", "SK": f"JOB#{job_id}"},
            UpdateExpression=(
                "SET #status = :failed, lastErrorCode = :error_type, "
                "lastFailedAt = :now, updatedAt = :now "
                "REMOVE processingLeaseUntil, processingEventId"
            ),
            ConditionExpression="processingEventId = :event_id",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":failed": "FAILED",
                ":error_type": error_type,
                ":now": utc_now(),
                ":event_id": event_id,
            },
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise


def normalized_labels(rekognition_response):
    labels = []
    for label in rekognition_response.get("Labels", []):
        labels.append(
            {
                "name": label.get("Name"),
                "confidence": round(float(label.get("Confidence", 0)), 2),
                "parents": [
                    parent.get("Name")
                    for parent in label.get("Parents", [])
                    if parent.get("Name")
                ],
            }
        )
    return labels


def analyze_image(bucket, key, version_id):
    s3_object = {"Bucket": bucket, "Name": key}
    if version_id and version_id != "null":
        s3_object["Version"] = version_id

    return REKOGNITION.detect_labels(
        Image={"S3Object": s3_object},
        MaxLabels=MAX_LABELS,
        MinConfidence=MIN_CONFIDENCE,
    )


def validate_object(bucket, key, version_id, event_size):
    if bucket != UPLOAD_BUCKET:
        raise ValueError("Unexpected S3 source bucket")
    if event_size > MAX_IMAGE_BYTES:
        raise ValueError("Uploaded image exceeds the configured size limit")

    params = {"Bucket": bucket, "Key": key}
    if version_id and version_id != "null":
        params["VersionId"] = version_id
    metadata = S3.head_object(**params)

    if metadata.get("ContentLength", 0) > MAX_IMAGE_BYTES:
        raise ValueError("Uploaded image exceeds the configured size limit")
    if metadata.get("ContentType") not in ALLOWED_CONTENT_TYPES:
        raise ValueError("Uploaded object has an unsupported content type")


def complete_job(
    tenant_id,
    job_id,
    event_id,
    result_key,
    label_count,
    fingerprint,
):
    now = utc_now()
    TABLE.update_item(
        Key={"PK": f"TENANT#{tenant_id}", "SK": f"JOB#{job_id}"},
        UpdateExpression=(
            "SET #status = :completed, resultKey = :result_key, "
            "labelCount = :label_count, completedAt = :now, updatedAt = :now, "
            "processedFingerprint = :fingerprint "
            "REMOVE processingLeaseUntil, processingEventId, "
            "lastErrorCode, lastFailedAt"
        ),
        ConditionExpression="processingEventId = :event_id",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={
            ":completed": "COMPLETED",
            ":result_key": result_key,
            ":label_count": label_count,
            ":now": now,
            ":fingerprint": fingerprint,
            ":event_id": event_id,
        },
    )


def process_s3_record(s3_record, message_id):
    if s3_record.get("eventSource") != "aws:s3":
        raise ValueError("Unsupported event source")
    if not s3_record.get("eventName", "").startswith("ObjectCreated:"):
        raise ValueError("Unsupported S3 event type")

    bucket = s3_record["s3"]["bucket"]["name"]
    object_details = s3_record["s3"]["object"]
    key = unquote_plus(object_details["key"])
    version_id = object_details.get("versionId")
    event_size = int(object_details.get("size", 0))
    etag = object_details.get("eTag", "")
    sequencer = object_details.get("sequencer", "unknown")

    match = OBJECT_KEY_PATTERN.fullmatch(key)
    if not match:
        raise ValueError("Uploaded object key does not match the trusted format")

    tenant_id = match.group("tenant")
    job_id = match.group("job_id")
    event_id = f"{message_id}:{sequencer}"
    fingerprint = version_id or etag or sequencer

    job = get_job(tenant_id, job_id)
    if not job:
        raise ValueError("No job record exists for the uploaded object")
    if job.get("tenantId") != tenant_id or job.get("inputKey") != key:
        raise ValueError("Uploaded object does not match its job record")
    if job.get("status") == "COMPLETED":
        log_event(
            "info",
            "duplicate_event_skipped",
            tenantId=tenant_id,
            jobId=job_id,
        )
        return

    leased_job = acquire_processing_lease(tenant_id, job_id, event_id)
    if leased_job is None:
        log_event(
            "info",
            "duplicate_event_skipped",
            tenantId=tenant_id,
            jobId=job_id,
        )
        return

    try:
        validate_object(bucket, key, version_id, event_size)
        rekognition_response = analyze_image(bucket, key, version_id)
        labels = normalized_labels(rekognition_response)
        result_key = f"tenants/{tenant_id}/results/{job_id}/labels.json"
        result_document = {
            "schemaVersion": 1,
            "jobId": job_id,
            "tenantId": tenant_id,
            "processedAt": utc_now(),
            "source": {
                "key": key,
                "versionId": version_id,
                "eTag": etag,
            },
            "labels": labels,
        }

        S3.put_object(
            Bucket=RESULTS_BUCKET,
            Key=result_key,
            Body=json.dumps(result_document, separators=(",", ":")).encode("utf-8"),
            ContentType="application/json",
            Metadata={"tenant-id": tenant_id, "job-id": job_id},
        )
        complete_job(
            tenant_id,
            job_id,
            event_id,
            result_key,
            len(labels),
            fingerprint,
        )
        log_event(
            "info",
            "job_completed",
            tenantId=tenant_id,
            jobId=job_id,
            labelCount=len(labels),
        )
    except Exception as exc:
        mark_failed(tenant_id, job_id, event_id, type(exc).__name__)
        raise


def process_sqs_record(record):
    message_id = record.get("messageId", "unknown")
    try:
        body = json.loads(record.get("body", ""))
    except json.JSONDecodeError as exc:
        raise ValueError("SQS message body is not valid JSON") from exc

    if body.get("Event") == "s3:TestEvent":
        log_event("info", "s3_test_event_ignored", messageId=message_id)
        return

    records = body.get("Records")
    if not isinstance(records, list) or not records:
        raise ValueError("SQS message does not contain S3 event records")

    for s3_record in records:
        process_s3_record(s3_record, message_id)


def lambda_handler(event, context):
    failures = []
    for record in event.get("Records", []):
        message_id = record.get("messageId", "unknown")
        try:
            process_sqs_record(record)
        except Exception as exc:
            log_event(
                "exception",
                "message_processing_failed",
                messageId=message_id,
                errorType=type(exc).__name__,
                requestId=getattr(context, "aws_request_id", None),
            )
            failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": failures}
