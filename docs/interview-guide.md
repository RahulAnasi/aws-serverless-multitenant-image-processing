# Interview Guide

## Thirty-second explanation

I built a serverless multi-tenant image-analysis platform on AWS. Cognito signs
users in with Authorization Code and PKCE, API Gateway validates access tokens,
and Lambda derives the tenant from a DynamoDB membership keyed by the JWT
subject. The browser uploads directly to a private S3 bucket with a five-minute
presigned URL. S3 publishes to SQS, a Lambda processor calls Rekognition, and
the result is stored privately in S3 with job state in DynamoDB. I validated
unauthorized access, cross-tenant isolation, duplicate delivery, retries, DLQ
isolation, monitoring, and CloudTrail audit history.

## Design decisions to defend

### Why not upload through API Gateway and Lambda?

Direct S3 upload avoids Lambda/API payload limits, execution time, and data
transfer through the compute layer. The API authorizes the operation and signs
only one short-lived object write.

### Why SQS between S3 and the processor?

SQS buffers bursts, separates ingestion from processing, provides retry control,
and isolates poison messages in a DLQ. S3-to-Lambda alone has less explicit
back-pressure and failure handling.

### Why derive the tenant from `sub`?

Browser input is untrusted. Mapping a validated immutable user subject to a
tenant prevents a caller from changing a request field to access another
tenant's partition or object prefix.

### Why is idempotency necessary?

S3 and standard SQS provide at-least-once delivery. Duplicate events are normal,
so the processor checks completed state and uses a conditional processing lease
before making a billable Rekognition call.

### Why return `404` for another tenant's job?

`403` would reveal that the identifier exists. A tenant-scoped DynamoDB lookup
naturally returns `404`, reducing cross-tenant information disclosure.

### Why no VPC?

The platform uses public AWS service endpoints with IAM-controlled managed
services. A VPC and NAT Gateway would add cost and operational complexity without
protecting an otherwise private server fleet.

## Honest limitations and production extensions

- Provision resources with Terraform or AWS CDK and deploy through CI/CD.
- Use separate accounts and stages for development, test, and production.
- Add application-user MFA and a controlled tenant onboarding workflow.
- Add custom OAuth resource-server scopes such as `jobs.read` and `jobs.write`.
- Use a custom domain, CloudFront, WAF, and production frontend hosting.
- Enable longer CloudTrail delivery and selected data events where audit and
  compliance requirements justify the cost.
- Add distributed tracing, service-level objectives, notification routing, and
  load/performance tests.

