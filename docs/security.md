# Security Model

## Identity and tenant authorization

- Cognito self-registration is disabled; users are administratively created.
- The SPA is a public client with no client secret and uses Authorization Code
  + PKCE.
- API Gateway validates the Cognito JWT using the configured issuer and app
  client audience and requires the `openid` scope.
- Lambda reads only the validated `sub` claim supplied by API Gateway.
- DynamoDB maps `USER#<sub>` to one active tenant membership.
- Client-supplied `tenantId` is rejected with `400`.
- Job access is resolved within `TENANT#<derived-tenant-id>` partitions.

## Data protection

- Both S3 buckets are private with all public access blocked and ACLs disabled.
- Bucket policies deny non-TLS requests.
- SSE-S3 encryption and versioning are enabled.
- Five-minute presigned URLs constrain object, operation, expiry, and upload
  content type.
- S3 CORS allows only the local development origin and required methods.
- Tenant and job IDs are embedded in trusted object-key layouts.
- Current objects expire after 30 days; noncurrent versions expire after 7 days.

## IAM separation

| Principal | Intended capability |
| --- | --- |
| `capstone-builder` | Provision scoped project services and pass only the two approved Lambda roles |
| API Lambda role | Membership/job DynamoDB access and presigned upload/result permissions |
| Processor Lambda role | Main-queue consumption, upload reads, result writes, job updates, DetectLabels, and logs |

The processor role has no DLQ-consumption permission. The API role cannot call
Rekognition or consume SQS. Neither role has broad administrative permissions.

## Queue protection

- Only the S3 service can publish object events under the upload-bucket source
  ARN and owning-account condition.
- The processor event source uses batch size 1 and partial batch failures.
- The queue visibility timeout is six times the processor Lambda timeout.
- The DLQ redrive allow policy accepts only the main processing queue.

## Audit and monitoring

- Lambda log groups retain events for 14 days.
- CloudWatch alarms monitor DLQ occupancy, oldest main-queue message age, and
  structured processor failure events.
- CloudTrail Event history provides immutable 90-day regional management-event
  evidence, including alarm changes made by the builder identity.

## Deliberate project constraints

- Cognito MFA is not enforced for the two disposable demo application users.
- CloudTrail S3/Lambda data events are not enabled to avoid unnecessary project
  cost; management-event auditing is validated through Event history.
- No VPC or NAT Gateway is used because all components are managed serverless
  services and a NAT Gateway would add cost without improving this design.
- Environment-specific account IDs, bucket names, endpoints, user subjects,
  tokens, and passwords are excluded from source control.

