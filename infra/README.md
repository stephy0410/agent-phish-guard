# infra

Terraform for the AWS resources the agent needs: an IAM role with Bedrock invoke access and the compute that runs the agent and the Decider server.

Nothing is defined yet. Two rules apply once it is:

- State files and `*.tfvars` are git-ignored. Never commit them.
- Run `terraform destroy` when the hackathon ends. No resource stays up afterwards.
