import base64
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key


LOGGER = logging.getLogger()
LOGGER.setLevel(logging.INFO)

TABLE_NAME = os.environ.get("TABLE_NAME", "mtip-main")
UPLOAD_BUCKET = os.environ.get("UPLOAD_BUCKET", "")
RESULTS_BUCKET = os.environ.get("RESULTS_BUCKET", "")
PRESIGNED_URL_TTL = int(os.environ.get("PRESIGNED_URL_TTL", "300"))

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
}
TENANT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
JOB_PATH_PATTERN = re.compile(
    r"^/jobs/(?P<job_id>[0-9a-fA-F-]{36})(?P<result>/result)?$"
)

DYNAMODB = boto3.resource("dynamodb")
TABLE = DYNAMODB.Table(TABLE_NAME)
S3 = boto3.client("s3")


class HttpError(Exception):
    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def log_event(level, event_name, **fields):
    payload = {"event": event_name, **fields}
    getattr(LOGGER, level)(json.dumps(payload, separators=(",", ":")))


def json_default(value):
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Cache-Control": "no-store",
        },
        "body": json.dumps(body, default=json_default, separators=(",", ":")),
    }


def parse_body(event):
    raw_body = event.get("body")
    if not raw_body:
        raise HttpError(400, "A JSON request body is required")

    if event.get("isBase64Encoded"):
        try:
            raw_body = base64.b64decode(raw_body).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise HttpError(400, "Request body is not valid UTF-8") from exc

    try:
        value = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HttpError(400, "Request body is not valid JSON") from exc

    if not isinstance(value, dict):
        raise HttpError(400, "Request body must be a JSON object")
    return value


def claims_from_event(event):
    return (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )


def resolve_tenant(event):
    subject = claims_from_event(event).get("sub")
    if not subject:
        raise HttpError(401, "Authenticated user identity is unavailable")

    result = TABLE.get_item(
        Key={"PK": f"USER#{subject}", "SK": "MEMBERSHIP"},
        ConsistentRead=True,
    )
    membership = result.get("Item")

    if not membership or membership.get("status") != "ACTIVE":
        raise HttpError(403, "No active tenant membership exists for this user")

    tenant_id = membership.get("tenantId", "")
    if not TENANT_ID_PATTERN.fullmatch(tenant_id):
        log_event("error", "invalid_tenant_mapping", subject=subject)
        raise HttpError(403, "Tenant membership is invalid")

    return subject, tenant_id


def validate_job_id(job_id):
    try:
        parsed = uuid.UUID(job_id)
    except ValueError as exc:
        raise HttpError(400, "Job ID is invalid") from exc
    if str(parsed) != job_id.lower():
        raise HttpError(400, "Job ID is invalid")
    return str(parsed)


def get_job(tenant_id, job_id):
    result = TABLE.get_item(
        Key={"PK": f"TENANT#{tenant_id}", "SK": f"JOB#{job_id}"},
        ConsistentRead=True,
    )
    item = result.get("Item")
    if not item:
        # Returning 404 also prevents disclosure of another tenant's job.
        raise HttpError(404, "Job not found")
    return item


def public_job(item):
    private_fields = {
        "PK",
        "SK",
        "inputBucket",
        "resultBucket",
        "ownerSub",
        "processingEventId",
        "processingLeaseUntil",
    }
    return {key: value for key, value in item.items() if key not in private_fields}


def create_job(event, subject, tenant_id):
    body = parse_body(event)
    if "tenantId" in body:
        raise HttpError(400, "tenantId must not be supplied by the client")

    filename = body.get("filename")
    content_type = body.get("contentType")
    if not isinstance(filename, str) or not filename.strip():
        raise HttpError(400, "filename is required")
    if len(filename) > 255:
        raise HttpError(400, "filename is too long")
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HttpError(400, "Only JPEG and PNG images are supported")

    job_id = str(uuid.uuid4())
    extension = ALLOWED_CONTENT_TYPES[content_type]
    input_key = f"tenants/{tenant_id}/uploads/{job_id}/image{extension}"
    result_key = f"tenants/{tenant_id}/results/{job_id}/labels.json"
    timestamp = utc_now()

    item = {
        "PK": f"TENANT#{tenant_id}",
        "SK": f"JOB#{job_id}",
        "entityType": "JOB",
        "jobId": job_id,
        "tenantId": tenant_id,
        "ownerSub": subject,
        "status": "AWAITING_UPLOAD",
        "originalFilename": os.path.basename(filename.strip()),
        "contentType": content_type,
        "inputBucket": UPLOAD_BUCKET,
        "inputKey": input_key,
        "resultBucket": RESULTS_BUCKET,
        "resultKey": result_key,
        "processingAttempts": 0,
        "createdAt": timestamp,
        "updatedAt": timestamp,
    }

    TABLE.put_item(
        Item=item,
        ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
    )

    upload_url = S3.generate_presigned_url(
        "put_object",
        Params={
            "Bucket": UPLOAD_BUCKET,
            "Key": input_key,
            "ContentType": content_type,
        },
        ExpiresIn=PRESIGNED_URL_TTL,
        HttpMethod="PUT",
    )

    log_event(
        "info",
        "job_created",
        jobId=job_id,
        tenantId=tenant_id,
        requestId=event.get("requestContext", {}).get("requestId"),
    )
    return response(
        201,
        {
            "job": public_job(item),
            "upload": {
                "method": "PUT",
                "url": upload_url,
                "headers": {"Content-Type": content_type},
                "expiresIn": PRESIGNED_URL_TTL,
            },
        },
    )


def list_jobs(tenant_id):
    result = TABLE.query(
        KeyConditionExpression=Key("PK").eq(f"TENANT#{tenant_id}")
        & Key("SK").begins_with("JOB#")
    )
    jobs = [public_job(item) for item in result.get("Items", [])]
    jobs.sort(key=lambda item: item.get("createdAt", ""), reverse=True)
    return response(200, {"jobs": jobs})


def get_job_response(tenant_id, job_id):
    return response(200, {"job": public_job(get_job(tenant_id, job_id))})


def get_result_url(tenant_id, job_id):
    item = get_job(tenant_id, job_id)
    if item.get("status") != "COMPLETED":
        raise HttpError(409, "Job result is not ready")

    result_key = item.get("resultKey")
    expected_key = f"tenants/{tenant_id}/results/{job_id}/labels.json"
    if result_key != expected_key:
        log_event("error", "invalid_result_key", jobId=job_id, tenantId=tenant_id)
        raise HttpError(500, "Stored job result is invalid")

    download_url = S3.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": RESULTS_BUCKET,
            "Key": result_key,
            "ResponseContentType": "application/json",
            "ResponseContentDisposition": f'attachment; filename="{job_id}-labels.json"',
        },
        ExpiresIn=PRESIGNED_URL_TTL,
        HttpMethod="GET",
    )
    return response(
        200,
        {"url": download_url, "expiresIn": PRESIGNED_URL_TTL},
    )


def lambda_handler(event, context):
    request_id = event.get("requestContext", {}).get("requestId")
    try:
        http_context = event.get("requestContext", {}).get("http", {})
        method = http_context.get("method", "")
        path = event.get("rawPath", "")
        subject, tenant_id = resolve_tenant(event)

        if method == "POST" and path == "/jobs":
            return create_job(event, subject, tenant_id)
        if method == "GET" and path == "/jobs":
            return list_jobs(tenant_id)

        match = JOB_PATH_PATTERN.fullmatch(path)
        if match and method == "GET":
            job_id = validate_job_id(match.group("job_id"))
            if match.group("result"):
                return get_result_url(tenant_id, job_id)
            return get_job_response(tenant_id, job_id)

        raise HttpError(404, "Route not found")
    except HttpError as exc:
        log_event(
            "warning",
            "request_rejected",
            statusCode=exc.status_code,
            requestId=request_id,
        )
        return response(exc.status_code, {"message": exc.message})
    except Exception as exc:
        log_event(
            "exception",
            "request_failed",
            errorType=type(exc).__name__,
            requestId=request_id,
        )
        return response(500, {"message": "Internal server error"})
