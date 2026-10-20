terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.67.0"
    }
  }

  # State lives in the S3 bucket created by bootstrap/. The bucket name is passed at init time:
  #   terraform init -backend-config=backend.hcl
  backend "s3" {}
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
