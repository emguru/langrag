terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.25"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.12"
    }
  }

  # Uncomment for remote state in production:
  # backend "s3" {
  #   bucket         = "your-terraform-state-bucket"
  #   key            = "kube-rag-langg/terraform.tfstate"
  #   region         = "us-east-1"
  #   encrypt        = true
  #   dynamodb_table = "terraform-locks"
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = merge(var.tags, {
      Project     = "kube-rag-langg"
      ManagedBy   = "terraform"
      Environment = var.environment
    })
  }
}

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

locals {
  name       = var.cluster_name
  azs        = slice(data.aws_availability_zones.available.names, 0, 3)
  tags       = var.tags
  account_id = data.aws_caller_identity.current.account_id
  demo_mode  = var.demo_mode

  # Demo: one cheap node pool that can scale to zero. Non-demo keeps a dedicated agents pool.
  eks_node_groups = local.demo_mode ? {
    general = {
      name           = "general"
      instance_types = var.node_instance_types
      min_size       = var.node_min_size
      max_size       = var.node_max_size
      desired_size   = var.node_desired_size
      labels         = { role = "general" }
    }
  } : {
    general = {
      name           = "general"
      instance_types = var.node_instance_types
      min_size       = max(var.node_min_size, 1)
      max_size       = var.node_max_size
      desired_size   = var.node_desired_size
      labels         = { role = "general" }
    }
    agents = {
      name           = "agents"
      instance_types = var.agent_node_instance_types
      min_size       = 1
      max_size       = var.node_max_size
      desired_size   = var.agent_node_desired_size
      labels         = { role = "agents" }
      taints = [{
        key    = "workload"
        value  = "agents"
        effect = "NO_SCHEDULE"
      }]
    }
  }

  enable_rds           = var.enable_rds
  enable_elasticache   = var.enable_elasticache
}

# ── Networking ────────────────────────────────────────────────────────────────

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "~> 5.0"

  name = "${local.name}-vpc"
  cidr = var.vpc_cidr

  azs             = local.azs
  private_subnets = [for i, az in local.azs : cidrsubnet(var.vpc_cidr, 4, i)]
  public_subnets  = [for i, az in local.azs : cidrsubnet(var.vpc_cidr, 4, i + 3)]
  intra_subnets   = [for i, az in local.azs : cidrsubnet(var.vpc_cidr, 4, i + 6)]

  enable_nat_gateway   = true
  single_nat_gateway   = var.environment != "production"
  enable_dns_hostnames = true
  enable_dns_support   = true

  public_subnet_tags = {
    "kubernetes.io/role/elb" = 1
  }
  private_subnet_tags = {
    "kubernetes.io/role/internal-elb" = 1
  }
}

# ── EKS ───────────────────────────────────────────────────────────────────────

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "~> 20.0"

  cluster_name    = local.name
  cluster_version = var.kubernetes_version

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  cluster_endpoint_public_access = true

  enable_cluster_creator_admin_permissions = true

  cluster_addons = {
    coredns = {
      most_recent = true
    }
    kube-proxy = {
      most_recent = true
    }
    vpc-cni = {
      most_recent = true
    }
    aws-ebs-csi-driver = {
      most_recent                 = true
      service_account_role_arn    = module.ebs_csi_irsa.iam_role_arn
    }
  }

  eks_managed_node_groups = local.eks_node_groups
}

module "ebs_csi_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.0"

  role_name             = "${local.name}-ebs-csi"
  attach_ebs_csi_policy = true
  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["kube-system:ebs-csi-controller-sa"]
    }
  }
}

# ── RDS PostgreSQL ────────────────────────────────────────────────────────────

resource "aws_security_group" "rds" {
  count = local.enable_rds ? 1 : 0

  name_prefix = "${local.name}-rds-"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_db_subnet_group" "main" {
  count = local.enable_rds ? 1 : 0

  name       = "${local.name}-db"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_db_instance" "postgres" {
  count = local.enable_rds ? 1 : 0

  identifier = "${local.name}-postgres"

  engine         = "postgres"
  engine_version = var.postgres_version
  instance_class = var.postgres_instance_class

  allocated_storage     = var.postgres_allocated_storage
  max_allocated_storage = var.postgres_max_allocated_storage
  storage_encrypted     = true

  db_name  = "langgraph"
  username = var.postgres_username
  password = var.postgres_password

  vpc_security_group_ids = [aws_security_group.rds[0].id]
  db_subnet_group_name   = aws_db_subnet_group.main[0].name

  backup_retention_period = var.environment == "production" ? 7 : 0
  skip_final_snapshot     = true
  deletion_protection     = false

  performance_insights_enabled = !local.demo_mode
}

# ── ElastiCache Redis ─────────────────────────────────────────────────────────

resource "aws_security_group" "redis" {
  count = local.enable_elasticache ? 1 : 0

  name_prefix = "${local.name}-redis-"
  vpc_id      = module.vpc.vpc_id

  ingress {
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_elasticache_subnet_group" "main" {
  count = local.enable_elasticache ? 1 : 0

  name       = "${local.name}-redis"
  subnet_ids = module.vpc.private_subnets
}

resource "aws_elasticache_replication_group" "redis" {
  count = local.enable_elasticache ? 1 : 0

  replication_group_id = "${local.name}-redis"
  description          = "Redis for LangGraph checkpoints and job queues"

  engine               = "redis"
  engine_version       = "7.1"
  node_type            = var.redis_node_type
  num_cache_clusters   = 1
  parameter_group_name = "default.redis7"
  port                 = 6379

  subnet_group_name  = aws_elasticache_subnet_group.main[0].name
  security_group_ids = [aws_security_group.redis[0].id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  automatic_failover_enabled = false
}

# ── S3 Document Store ─────────────────────────────────────────────────────────

resource "aws_s3_bucket" "documents" {
  bucket = "${local.name}-documents-${local.account_id}"
}

resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket = aws_s3_bucket.documents.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── ECR Repositories ──────────────────────────────────────────────────────────

resource "aws_ecr_repository" "agents" {
  name                 = "${local.name}/research-agents"
  image_tag_mutability = "MUTABLE"
  force_delete         = var.environment != "production"
}

resource "aws_ecr_repository" "ingestion" {
  name                 = "${local.name}/ingestion"
  image_tag_mutability = "MUTABLE"
  force_delete         = var.environment != "production"
}

resource "aws_ecr_repository" "ui" {
  name                 = "${local.name}/showcase-ui"
  image_tag_mutability = "MUTABLE"
  force_delete         = var.environment != "production"
}

# ── IRSA: Agents → Bedrock + S3 ───────────────────────────────────────────────

module "agents_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.0"

  role_name = "${local.name}-agents"

  role_policy_arns = [
    aws_iam_policy.agents_bedrock.arn,
    aws_iam_policy.agents_s3.arn,
  ]

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["agents:research-agents", "rag:ingestion"]
    }
  }
}

resource "aws_iam_policy" "agents_bedrock" {
  name = "${local.name}-bedrock-invoke"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
      Resource = "*"
    }]
  })
}

resource "aws_iam_policy" "agents_s3" {
  name = "${local.name}-s3-documents"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["s3:GetObject", "s3:PutObject", "s3:ListBucket", "s3:DeleteObject"]
      Resource = [
        aws_s3_bucket.documents.arn,
        "${aws_s3_bucket.documents.arn}/*",
      ]
    }]
  })
}

# ── Secrets Manager ───────────────────────────────────────────────────────────

resource "aws_secretsmanager_secret" "app" {
  name = "${local.name}/app-secrets"
}

resource "aws_secretsmanager_secret_version" "app" {
  secret_id = aws_secretsmanager_secret.app.id
  secret_string = jsonencode({
    postgres_url      = local.enable_rds ? "postgresql://${var.postgres_username}:${var.postgres_password}@${aws_db_instance.postgres[0].address}:5432/langgraph" : ""
    redis_url         = local.enable_elasticache ? "rediss://${aws_elasticache_replication_group.redis[0].primary_endpoint_address}:6379" : "redis://redis.rag.svc.cluster.local:6379"
    langsmith_api_key = var.langsmith_api_key
    openai_api_key    = var.openai_api_key
  })
}
