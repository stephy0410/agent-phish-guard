# One CPU instance that runs the agent and Decider 2B, reachable only through SSM. Its own
# stack: `terraform destroy` here removes the instance, its disk, the source bucket and the
# SSM permission, and leaves infra/ (IAM, budget) alone.

data "aws_caller_identity" "current" {}

data "aws_iam_role" "agent" {
  name = var.agent_role_name
}

data "aws_iam_instance_profile" "agent" {
  name = var.agent_role_name
}

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

# --------------------------------------------------------------------------- network

data "aws_vpc" "default" {
  default = true
}

data "aws_ec2_instance_type_offerings" "available" {
  location_type = "availability-zone"

  filter {
    name   = "instance-type"
    values = [var.instance_type]
  }
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }

  filter {
    name   = "default-for-az"
    values = ["true"]
  }

  filter {
    name   = "availability-zone"
    values = data.aws_ec2_instance_type_offerings.available.locations
  }
}

# No inbound rules at all: no SSH, no open ports. Terraform drops the default allow-all egress,
# so the only way out is HTTPS (SSM, Bedrock, S3, PyPI, Hugging Face).
resource "aws_security_group" "agent" {
  name_prefix = "agent-phish-guard-"
  description = "Agent Phish Guard: no inbound, HTTPS out only"
  vpc_id      = data.aws_vpc.default.id
}

resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.agent.id
  description       = "HTTPS to AWS APIs and package and model downloads"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

# --------------------------------------------------------------------------- source code

resource "aws_s3_bucket" "source" {
  bucket_prefix = "agent-phish-guard-source-"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "source" {
  bucket                  = aws_s3_bucket.source.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "source" {
  bucket = aws_s3_bucket.source.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "source_tls_only" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.source.arn, "${aws_s3_bucket.source.arn}/*"]

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

resource "aws_s3_bucket_policy" "source" {
  bucket     = aws_s3_bucket.source.id
  policy     = data.aws_iam_policy_document.source_tls_only.json
  depends_on = [aws_s3_bucket_public_access_block.source]
}

resource "aws_s3_object" "source" {
  bucket = aws_s3_bucket.source.id
  key    = "source.tar.gz"
  source = var.source_archive
  etag   = filemd5(var.source_archive)
}

# --------------------------------------------------------------------------- instance role

# Session Manager instead of SSH. Attached here, so it goes away with the compute.
resource "aws_iam_role_policy_attachment" "ssm" {
  role       = data.aws_iam_role.agent.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "read_source" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.source.arn}/${aws_s3_object.source.key}"]
  }
}

resource "aws_iam_role_policy" "read_source" {
  name   = "agent-phish-guard-read-source"
  role   = data.aws_iam_role.agent.name
  policy = data.aws_iam_policy_document.read_source.json
}

# --------------------------------------------------------------------------- instance

resource "aws_instance" "agent" {
  ami                                  = data.aws_ssm_parameter.al2023.insecure_value
  instance_type                        = var.instance_type
  subnet_id                            = sort(data.aws_subnets.default.ids)[0]
  vpc_security_group_ids               = [aws_security_group.agent.id]
  iam_instance_profile                 = data.aws_iam_instance_profile.agent.name
  associate_public_ip_address          = true # outbound only; the security group allows no inbound
  instance_initiated_shutdown_behavior = "terminate"

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required" # IMDSv2 only
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.volume_size_gb
    encrypted             = true
    delete_on_termination = true
  }

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    region                 = var.region
    bucket                 = aws_s3_bucket.source.id
    key                    = aws_s3_object.source.key
    model_id               = var.bedrock_model_id
    auto_terminate_minutes = var.auto_terminate_minutes
  })
  user_data_replace_on_change = true

  tags = {
    Name = "agent-phish-guard"
  }

  depends_on = [aws_iam_role_policy_attachment.ssm, aws_iam_role_policy.read_source]
}
