# image_registry.tftest.hcl: the repository this stack's two images are promoted into.
#
# Mock providers, so no credentials and no cloud call: `make tf-test`. Every assertion reads a
# value Terraform knows at plan time, never a computed id, because a mock provider cannot resolve
# those and an assertion over an unknown proves nothing.
#
# Why this file exists at all: the support stack was applied into a shared project with no registry
# of its own, so both images had nowhere to be promoted to and the deployment stopped there. The
# gap was invisible to every check in the repository, because nothing asserted that the paths the
# README tells an operator to push to are paths this Terraform creates.

mock_provider "google" {
  mock_data "google_project" {
    defaults = {
      number = "123456789012"
    }
  }
}

mock_provider "google-beta" {}

variables {
  project_id = "fictional-compliance-sg"
  org_id     = "123456789012"
}

run "the_registry_is_regional_docker_cmek_and_immutably_tagged" {
  command = plan

  variables {
    cmek_enabled  = true
    enable_vpc_sc = false
    worm_locked   = false
  }

  # The key's `id` is computed, so under a mock provider a plan does not know it and an assertion
  # that reads it is refused rather than failed. Naming the value for the plan phase is what makes
  # the CMEK claim checkable at all here: compared against a literal instead, the assertion would
  # still pass with the repository bound to some OTHER project's key.
  override_resource {
    target          = google_kms_crypto_key.compliance
    override_during = plan
    values = {
      id = "projects/fictional-compliance-sg/locations/asia-southeast1/keyRings/compliance-advisory-ring/cryptoKeys/compliance-advisory-cmek"
    }
  }

  assert {
    condition = (
      google_artifact_registry_repository.images.repository_id == "compliance-advisory" &&
      google_artifact_registry_repository.images.location == var.region &&
      google_artifact_registry_repository.images.format == "DOCKER"
    )
    error_message = "The repository must be the in-region Docker repository named compliance-advisory (P-03)."
  }

  assert {
    condition     = google_artifact_registry_repository.images.kms_key_name == google_kms_crypto_key.compliance[0].id
    error_message = "An image carries the application and its configuration, so the repository uses this stack's own key (P-09)."
  }

  assert {
    condition     = google_artifact_registry_repository.images.docker_config[0].immutable_tags == true
    error_message = "Tags must be immutable: a moved tag changes what a reviewer approved with no diff anywhere."
  }

  # CMEK does not cascade, and Artifact Registry encrypts as its own service agent rather than as
  # the caller, so without this binding the repository cannot be created at all.
  assert {
    condition = (
      google_kms_crypto_key_iam_member.artifactregistry[0].crypto_key_id == google_kms_crypto_key.compliance[0].id &&
      google_kms_crypto_key_iam_member.artifactregistry[0].role == "roles/cloudkms.cryptoKeyEncrypterDecrypter"
    )
    error_message = "The Artifact Registry service agent must hold the key it is asked to encrypt with."
  }

  assert {
    condition     = contains(keys(google_project_service.required), "artifactregistry.googleapis.com")
    error_message = "A repository cannot be created in a project where the API was never enabled."
  }

  # Nothing in this stack may delete a running deployment's image on a timer.
  assert {
    condition     = length(google_artifact_registry_repository.images.cleanup_policies) == 0
    error_message = "No cleanup policy: the deployment pins images by digest, so deletion is an operator action, never a default."
  }
}

run "every_identity_that_runs_a_container_can_pull_it_and_no_one_else_is_named" {
  command = plan

  variables {
    enable_vpc_sc                       = false
    worm_locked                         = false
    additional_serving_service_accounts = ["journey-a-compli-abc123@fictional-compliance-sg.iam.gserviceaccount.com"]
  }

  # Same reason as above: `name` is computed, so the scoping assertion below needs the repository
  # the bindings point at to be a value the plan knows.
  override_resource {
    target          = google_artifact_registry_repository.images
    override_during = plan
    values = {
      name = "compliance-advisory"
    }
  }

  assert {
    condition = (
      length(google_artifact_registry_repository_iam_member.readers) == 4 &&
      contains(keys(google_artifact_registry_repository_iam_member.readers), "app") &&
      contains(keys(google_artifact_registry_repository_iam_member.readers), "agent_runtime") &&
      contains(keys(google_artifact_registry_repository_iam_member.readers), "pipeline") &&
      contains(
        keys(google_artifact_registry_repository_iam_member.readers),
        "additional:journey-a-compli-abc123@fictional-compliance-sg.iam.gserviceaccount.com"
      )
    )
    error_message = "The serving, agent-runtime and pipeline identities and every embedding host identity must hold pull access, and nothing else."
  }

  assert {
    condition = alltrue([
      for reader in values(google_artifact_registry_repository_iam_member.readers) :
      reader.role == "roles/artifactregistry.reader" &&
      reader.repository == google_artifact_registry_repository.images.name
    ])
    error_message = "Pull access is reader, scoped to THIS repository: a project-level grant admits every sibling's images too."
  }
}

run "an_app_deployed_on_its_own_grants_no_host_identity_anything" {
  command = plan

  variables {
    enable_vpc_sc = false
    worm_locked   = false
  }

  assert {
    condition = (
      length(google_artifact_registry_repository_iam_member.readers) == 3 &&
      length([
        for key in keys(google_artifact_registry_repository_iam_member.readers) :
        key if startswith(key, "additional:")
      ]) == 0
    )
    error_message = "With no embedding host, only this stack's own three identities may be named."
  }
}

run "the_refresh_image_the_validation_accepts_is_a_digest_in_this_registry" {
  command = plan

  variables {
    enable_vpc_sc        = false
    worm_locked          = false
    corpus_refresh_image = "asia-southeast1-docker.pkg.dev/fictional-compliance-sg/compliance-advisory/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }

  # Reads the image the job was actually given back against the repository path this stack builds,
  # rather than against a literal. A renamed repository_id, a different region or a project the
  # registry does not live in all break this, where a regex on the digest suffix alone would not.
  assert {
    condition = (
      google_cloud_run_v2_job.freshness_refresh[0].template[0].template[0].containers[0].image ==
      "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}/api@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    )
    error_message = "corpus_refresh_image must accept, and the job must run, a digest under this stack's own registry."
  }
}
