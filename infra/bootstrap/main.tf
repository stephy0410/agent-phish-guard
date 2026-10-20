# One-time setup: the S3 bucket that holds the main configuration's Terraform state, so the
# state lives outside the repo and survives a lost laptop. Apply this first, then infra/.
#
# This configuration's own state is local (git-ignored) and only tracks the bucket. Destroy it
# last, after `terraform destroy` in infra/.

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.67.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.9.1"
    }
  }
}

variable "region" {
  description = "AWS region for the state bucket."
  type        = string
  default     = "us-east-1"
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "agent-phish-guard"
      ManagedBy = "terraform"
    }
  }
}

# Bucket names are global, so a random suffix avoids collisions without publishing the account ID.
resource "random_id" "suffix" {
  byte_length = 4
}

resource "aws_s3_bucket" "state" {
  bucket = "agent-phish-guard-tfstate-${random_id.suffix.hex}"

  # Teardown rule: nothing stays up after the hackathon, so destroy also empties the bucket.
  force_destroy = true
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

data "aws_iam_policy_document" "https_only" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.state.arn,
      "${aws_s3_bucket.state.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.https_only.json

  depends_on = [aws_s3_bucket_public_access_block.state]
}

output "backend_config" {
  description = "Write to infra/backend.hcl: terraform output -raw backend_config > ../backend.hcl"
  value       = <<-EOT
    bucket       = "${aws_s3_bucket.state.bucket}"
    key          = "infra/terraform.tfstate"
    region       = "${var.region}"
    encrypt      = true
    use_lockfile = true
  EOT
}
