# Terraform 1.5 Upgrade Notes

Breaking changes from 1.2:

- `optional()` attributes now require Terraform >= 1.3
- S3 backend `use_lockfile` replaces DynamoDB lock table in new setups
- `terraform test` framework is GA

Run `terraform init -upgrade` after bumping `required_version = ">= 1.5.0"`.
