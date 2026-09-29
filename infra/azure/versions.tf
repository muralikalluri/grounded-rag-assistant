terraform {
  # >= 1.11 for ephemeral/write-only support and current `terraform test` mocking.
  required_version = ">= 1.11"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.40"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.12"
    }
  }
}

provider "azurerm" {
  # azurerm 4.x needs a subscription: set var.subscription_id or export ARM_SUBSCRIPTION_ID.
  subscription_id = var.subscription_id

  features {
    key_vault {
      # A PoC is destroyed and recreated. See README "Destroying" for the production stance.
      purge_soft_delete_on_destroy = true
    }
    log_analytics_workspace {
      permanently_delete_on_destroy = true
    }
    resource_group {
      prevent_deletion_if_contains_resources = false
    }
  }
}
