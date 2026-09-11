# model_armor.tf: the Model Armor guardrail template 'compliance-guardrail'.
#
# General Principle map:
#   P-04 (safety at the boundary): every prompt/response is screened for prompt injection,
#         jailbreak, sensitive data, malicious URLs and RAI categories BEFORE it reaches the
#         model or the user. The adapter calls :sanitizeUserPrompt / :sanitizeModelResponse
#         against this template.
#   P-03 (residency): the template lives in var.region (the regional Model Armor endpoint in
#         settings.yaml).
# verify: https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/model_armor_template

resource "google_model_armor_template" "compliance_guardrail" {
  provider    = google-beta
  project     = var.project_id
  location    = var.region
  template_id = "compliance-guardrail" # matches settings.yaml model_armor.template_id

  filter_config {
    # Responsible AI floor: block high-confidence harmful content (P-04).
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

    # Prompt injection & jailbreak floor, strictest confidence: compliance is high-stakes.
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = "LOW_AND_ABOVE"
    }

    # Regional capability. A region that does not serve it (asia-southeast1) refuses the whole
    # template with CAPABILITY_NOT_SUPPORTED, so a deployment there declines it EXPLICITLY via
    # the variable and discloses the narrowed guardrail.
    dynamic "malicious_uri_filter_settings" {
      for_each = var.model_armor_full_capabilities ? [1] : []
      content {
        filter_enforcement = "ENABLED"
      }
    }

    # Sensitive Data Protection: delegate to the DLP templates (dlp.tf), so Model Armor inspects
    # and de-identifies with the SAME templates the redaction adapter uses (one PII policy).
    sdp_settings {
      advanced_config {
        inspect_template    = google_data_loss_prevention_inspect_template.compliance.id
        deidentify_template = google_data_loss_prevention_deidentify_template.compliance.id
      }
    }
  }

  template_metadata {
    # Sanitize-operation entries carry the screened (already de-identified) prompt and
    # response. On by default; a deployment with no locked audit bucket to hold them declines.
    log_sanitize_operations = var.model_armor_log_sanitize_operations
    log_template_operations = true
    enforcement_type        = "INSPECT_AND_BLOCK"
  }

  depends_on = [
    google_project_service.required,
    google_data_loss_prevention_inspect_template.compliance,
    google_data_loss_prevention_deidentify_template.compliance,
  ]
}
