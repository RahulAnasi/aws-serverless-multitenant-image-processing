# Operations Runbook

## Monitoring inventory

| Signal | Purpose |
| --- | --- |
| `mtip-dlq-messages-visible` | Detect any failed message isolated in the DLQ |
| `mtip-processing-queue-oldest-message` | Detect a consumer outage or processing backlog older than five minutes |
| `mtip-processor-message-failure-alarm` | Detect structured processor message failures from logs |
| `/aws/lambda/mtip-api` | API acceptance, rejection, and exception events; 14-day retention |
| `/aws/lambda/mtip-processor` | completion, duplicate, and failure events; 14-day retention |

## CloudWatch Logs Insights queries

Recent processor outcomes:

```text
fields @timestamp, @message
| filter @message like /job_completed|duplicate_event_skipped|message_processing_failed/
| sort @timestamp desc
| limit 50
```

Recent API rejections and failures:

```text
fields @timestamp, @message
| filter @message like /request_rejected|request_failed/
| sort @timestamp desc
| limit 50
```

## DLQ response procedure

1. Confirm `mtip-dlq-messages-visible` is in `ALARM`.
2. Review processor logs around the oldest-message timestamp.
3. Poll the DLQ without deleting messages and classify the failure.
4. Verify the referenced job, trusted input key, object metadata, and IAM access.
5. Fix code or configuration before redrive.
6. Redrive only known-safe messages to the main queue.
7. Confirm the job completes and the DLQ returns to zero.

Never redrive an unexamined poison message; doing so can repeat failures and
consume Lambda, S3, and Rekognition capacity.

## Common symptoms

| Symptom | First checks |
| --- | --- |
| API `401` | Access token present, unexpired, correct issuer/audience, `openid` scope |
| API `403` | Cognito `sub` has exactly one active DynamoDB membership |
| API `404` for known job | Correct authenticated tenant and job ID |
| S3 presigned `403` | URL age under five minutes and exact signed `Content-Type` |
| Job remains `AWAITING_UPLOAD` | Upload object exists and S3 notification targets the main queue |
| Job becomes `FAILED` | Processor structured error log, object type/size, IAM and Rekognition access |
| Queue age increases | Lambda trigger enabled, concurrency/throttling, visibility timeout and logs |

## Cost and cleanup controls

- Monthly AWS Budget threshold: USD 5.
- On-demand DynamoDB; no provisioned capacity.
- S3 lifecycle: 30-day current objects, 7-day noncurrent versions, 1-day
  incomplete multipart uploads.
- Standard CloudWatch alarms and one custom metric remain within the small-demo
  free allowances at the time of implementation.
- No NAT Gateway, CloudTrail Lake, custom KMS key, WAF, or always-on compute.
- Delete disposable DLQ messages after evidence is recorded.

