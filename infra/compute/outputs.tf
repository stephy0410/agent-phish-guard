output "instance_id" {
  description = "Run commands on it with SSM (see README); there is no SSH."
  value       = aws_instance.agent.id
}

output "source_bucket" {
  description = "Private bucket with the packaged code. Emptied and deleted by destroy."
  value       = aws_s3_bucket.source.id
}

output "auto_terminate" {
  description = "Safety net if nobody runs destroy."
  value       = "The instance terminates itself ${var.auto_terminate_minutes} minutes after it boots."
}
