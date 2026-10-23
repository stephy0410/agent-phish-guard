terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.67.0"
    }
  }

  # Its own state, in the bucket bootstrap/ created, under its own key, so destroying the
  # compute never touches the IAM and budget of infra/:
  #   terraform -chdir=infra/compute init -backend-config=../backend.hcl \
  #     -backend-config=key=infra/compute.tfstate
  backend "s3" {}
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project   = "agent-phish-guard"
      ManagedBy = "terraform"
      Stack     = "compute"
    }
  }
}
