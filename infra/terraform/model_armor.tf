# model_armor.tf — Model Armor guardrail template 'compliance-guardrail'.
#
# General Principle map:
#   P-04 (safety at the boundary): every prompt/response is screened by Model Armor
#         for prompt-injection/jailbreak, sensitive data, malicious URLs, and RAI
#         categories BEFORE it reaches the model or the user. The C1 adapter calls
#         :sanitizeUserPrompt / :sanitizeModelResponse against this template.
#   P-03 (residency): the template lives in asia-southeast1 (regional Model Armor
#         endpoint modelarmor.asia-southeast1.rep.googleapis.com in settings.yaml).
#
# "Floor settings" = the minimum filters that are always enforced. We turn all four
# families ON: PI/jailbreak, sensitive data (delegated to DLP templates), malicious
# URL, and the Responsible-AI categories.
# verify: https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/model_armor_template

resource "google_model_armor_template" "compliance_guardrail" {
  provider    = google-beta
  project     = var.project_id
  location    = var.region             # asia-southeast1 (P-03)
  template_id = "compliance-guardrail" # matches settings.yaml model_armor.template_id

  filter_config {
    # --- Responsible AI floor: block high-confidence harmful content (P-04) --- #
    rai_settings {
      rai_filters {
        filter_type      = "HATE_SPEECH"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
      rai_filters {
        filter_type      = "HARASSMENT"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
      rai_filters {
        filter_type      = "SEXUALLY_EXPLICIT"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
      rai_filters {
        filter_type      = "DANGEROUS"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
    }

    # --- Prompt injection & jailbreak floor --- #
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = "LOW_AND_ABOVE" # strictest — compliance is high-stakes
    }

    # --- Malicious URL floor --- #
    malicious_uri_filter_settings {
      filter_enforcement = "ENABLED"
    }

    # --- Sensitive Data Protection: delegate to the DLP templates (dlp.tf) --- #
    # This is what makes Model Armor inspect & de-identify SG NRIC, IBAN, PAN, etc.
    # using the SAME DLP templates the redaction adapter uses (single PII policy).
    sdp_settings {
      advanced_config {
        inspect_template    = google_data_loss_prevention_inspect_template.compliance.id
        deidentify_template = google_data_loss_prevention_deidentify_template.compliance.id
      }
    }
  }

  # Audit hygiene: log that operations ran, but do NOT log the sanitized payloads
  # (content stays out of logs — PII never lands in a log line, P-04).
  template_metadata {
    log_sanitize_operations = true
    log_template_operations = true
    enforcement_type        = "INSPECT_AND_BLOCK"
  }

  depends_on = [
    google_project_service.required,
    google_data_loss_prevention_inspect_template.compliance,
    google_data_loss_prevention_deidentify_template.compliance,
  ]
}
