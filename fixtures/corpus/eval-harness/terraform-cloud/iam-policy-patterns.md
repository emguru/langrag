# IAM Policy Patterns for Terraform

Use least-privilege IAM roles for CI pipelines applying infrastructure.

## Recommended statements

- `terraform plan` role: read-only on state bucket, `Describe*` on EC2/VPC/IAM
- `terraform apply` role: scoped `Create*`, `Modify*`, `Delete*` on tagged resources only

Avoid attaching `AdministratorAccess` to pipeline users. Prefer OIDC federation over long-lived access keys.

Acronym reference: IAM = Identity and Access Management.
