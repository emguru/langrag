output "cluster_name" {
  value = module.eks.cluster_name
}

output "cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "cluster_oidc_provider_arn" {
  value = module.eks.oidc_provider_arn
}

output "kubeconfig_command" {
  value = "aws eks update-kubeconfig --region ${var.aws_region} --name ${module.eks.cluster_name}"
}

output "node_max_size" {
  value = var.node_max_size
}

output "demo_mode" {
  value = var.demo_mode
}

output "node_group_name" {
  description = "Primary EKS node group — used by demo-stop/demo-start scripts"
  value       = module.eks.eks_managed_node_groups["general"].node_group_name
}

output "postgres_endpoint" {
  value     = local.enable_rds ? aws_db_instance.postgres[0].address : null
  sensitive = true
}

output "rds_instance_id" {
  description = "RDS instance identifier for stop/start (null if RDS disabled)"
  value       = local.enable_rds ? aws_db_instance.postgres[0].id : null
}

output "redis_endpoint" {
  value = local.enable_elasticache ? aws_elasticache_replication_group.redis[0].primary_endpoint_address : null
}

output "documents_bucket" {
  value = aws_s3_bucket.documents.bucket
}

output "ecr_repositories" {
  value = {
    agents    = aws_ecr_repository.agents.repository_url
    ingestion = aws_ecr_repository.ingestion.repository_url
    ui        = aws_ecr_repository.ui.repository_url
  }
}

output "agents_irsa_role_arn" {
  value = module.agents_irsa.iam_role_arn
}

output "secrets_manager_arn" {
  value = aws_secretsmanager_secret.app.arn
}

output "vpc_id" {
  value = module.vpc.vpc_id
}

output "private_subnet_ids" {
  value = module.vpc.private_subnets
}
