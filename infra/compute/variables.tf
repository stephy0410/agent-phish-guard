variable "region" {
  description = "AWS region, the same as infra/ (the agent role can only call Bedrock there)."
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = <<-EOT
    CPU instance for the agent and Decider. The 2B model needs about 5 GB in bfloat16, so
    m7i-flex.large (2 vCPU, 8 GiB) fits, and it's one of the types an AWS Free plan account can
    launch. On a paid plan, m7i.xlarge (4 vCPU, 16 GiB) is faster.
  EOT
  type        = string
  default     = "m7i-flex.large"
}

variable "volume_size_gb" {
  description = "Root volume: the OS, PyTorch, the project's packages and the model (about 5 GB)."
  type        = number
  default     = 40
}

variable "auto_terminate_minutes" {
  description = "The instance terminates itself this long after boot, in case nobody runs destroy."
  type        = number
  default     = 180
}

variable "source_archive" {
  description = "The committed code, packaged with git archive (see README). Never the working tree."
  type        = string
  default     = "build/source.tar.gz"
}

variable "bedrock_model_id" {
  description = "Model the agent uses. Must be one of the inference profiles the agent role allows."
  type        = string
  default     = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
}

variable "agent_role_name" {
  description = "The agent role and instance profile created by infra/."
  type        = string
  default     = "agent-phish-guard-agent"
}
