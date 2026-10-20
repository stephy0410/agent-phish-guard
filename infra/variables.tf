variable "region" {
  description = "AWS region where the agent calls Bedrock."
  type        = string
  default     = "us-east-1"
}

variable "bedrock_inference_profiles" {
  description = "Inference profiles of the Claude models the agent uses. The role can invoke these and nothing else."
  type        = list(string)
  default = [
    "us.anthropic.claude-haiku-4-5-20251001-v1:0", # development (BEDROCK_MODEL_ID in .env.example)
    "us.anthropic.claude-opus-4-6-v1",             # code default (agent/config.py)
  ]
}

variable "agent_trusted_services" {
  description = "AWS services allowed to assume the agent role, i.e. where the agent runs."
  type        = list(string)
  default     = ["ec2.amazonaws.com"]
}

variable "developers" {
  description = "IAM user names for the teammates who call Bedrock from their laptops."
  type        = set(string)
  default     = ["ale", "hannah"]
}

variable "budget_email" {
  description = "Email for the budget alerts. Leave empty to skip the budget."
  type        = string
  default     = ""
}

variable "budget_limit_usd" {
  description = "Monthly spend that the alerts are measured against (the free-plan credits)."
  type        = number
  default     = 100
}

variable "budget_actual_alert_percentages" {
  description = "Alert when actual spend passes these percentages of budget_limit_usd. Early, because the credits are small."
  type        = list(number)
  default     = [5, 10, 25, 50]
}

variable "budget_forecast_alert_percentage" {
  description = "Alert when the month's forecast passes this percentage of budget_limit_usd."
  type        = number
  default     = 80
}
