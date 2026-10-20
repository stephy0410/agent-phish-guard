# Alerts as spend approaches the free-plan credits. Credits are excluded so the budget tracks what
# the project actually uses, not the $0 left after they are applied. AWS allows five notifications
# per budget: four on actual spend, one on the forecast.
resource "aws_budgets_budget" "monthly" {
  count = var.budget_email == "" ? 0 : 1

  name         = "agent-phish-guard-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.budget_limit_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_types {
    include_credit = false
  }

  dynamic "notification" {
    for_each = var.budget_actual_alert_percentages

    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.budget_email]
    }
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = var.budget_forecast_alert_percentage
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.budget_email]
  }
}
