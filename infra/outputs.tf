output "agent_role_arn" {
  description = "Role for the compute that runs the agent."
  value       = aws_iam_role.agent.arn
}

output "bedrock_invoke_policy_arn" {
  description = "Least-privilege policy shared by the agent role and the developers group."
  value       = aws_iam_policy.bedrock_invoke.arn
}

output "inference_profile_arns" {
  description = "The inference profiles the agent may invoke."
  value       = local.inference_profile_arns
}

output "foundation_model_arns" {
  description = "The regional foundation models behind those profiles."
  value       = local.foundation_model_arns
}

output "developer_users" {
  description = "IAM users in phishguard-developers. Enable their console access by hand (see README)."
  value       = sort([for u in aws_iam_user.developer : u.name])
}

output "budget_name" {
  description = "Budget name, or null when budget_email is empty."
  value       = one(aws_budgets_budget.monthly[*].name)
}
