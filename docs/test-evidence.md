# Verification Evidence

Verified in `ap-south-1` on 2026-09-01. Environment-specific identifiers and
credentials are intentionally omitted.

| Test | Expected control | Observed result | Status |
| --- | --- | --- | --- |
| Unauthenticated `GET /jobs` | API Gateway JWT authorizer rejects request | HTTP `401` | Pass |
| Browser CORS preflight | Only local origin and required methods/headers allowed | HTTP `204`; origin, method, and authorization headers present | Pass |
| Tenant A UI workflow | Create, upload, queue, process, persist, and render result | `COMPLETED`, one attempt, two labels | Pass |
| Presigned result download | Private result accessible only through short-lived URL | HTTP `200` | Pass |
| Tenant B list | Tenant B cannot see Tenant A jobs | Zero jobs | Pass |
| Cross-tenant direct job lookup | Another tenant's job is concealed | HTTP `404` | Pass |
| Client tenant spoofing | Request cannot override derived tenant | HTTP `400` | Pass |
| Duplicate completed S3 event | No duplicate processing or DetectLabels call | Empty batch failures; attempt count remained one; `duplicate_event_skipped` logged | Pass |
| Malformed queue messages | Retry and failure isolation | Messages exceeded max receives and arrived in DLQ | Pass |
| Processor failure log | Structured failures become a CloudWatch metric | `ProcessorMessageFailures` metric filter created and exercised | Pass |
| Log retention | Development logs do not accumulate indefinitely | Both Lambda log groups set to 14 days | Pass |
| CloudTrail management audit | Control-plane changes are attributable | `PutMetricAlarm` events visible for builder identity | Pass |

## Security interpretation

The list and direct-access tests cover different risks. An empty Tenant B list
proves tenant-scoped query behavior through the deployed API, while the direct
job lookup proves that possessing another tenant's job ID does not grant read
access or disclose its existence. Rejecting `tenantId` in create requests proves
the server, not the browser, controls tenant context.

## Resilience interpretation

The successful duplicate replay proves at-least-once delivery does not cause a
second Rekognition call or attempt increment. The malformed-message test proves
poison messages do not retry forever or block healthy work: SQS applies the
configured retry budget and isolates them in the DLQ.

