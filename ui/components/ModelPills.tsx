"use client";

import { useEffect, useState } from "react";

import { watchAnswers } from "../lib/answer-provenance.mjs";
import { API_BASE, healthz } from "../lib/api";

/**
 * Two small pills at the top right of every page: the model that ANSWERED, and `Search` when
 * the answer used an online search tool (owner decision, 2026-09-23; they replace the
 * full-width provenance banner).
 *
 * The pill names what answered, not what configuration would call. Until an answer arrives it
 * shows the service's configured `generator_model` from `/healthz`, dimmed, with where the
 * runtime sits in its title. From then on it shows the `X-Answered-By` header of the console's
 * last answering response, solid, and `Search` appears only while that response carried
 * `X-Search-Used: true`. Both headers are emitted by the service
 * (`install_answer_provenance` in `api/app.py`, which also lists them in the CORS
 * `expose_headers` so a cross-origin console can read them).
 *
 * **Every value comes from the service**, and nothing here infers one. A UI that read its own
 * runtime from `window.location` would be right until the deployment served through a proxy,
 * and wrong silently after that.
 *
 * Health goes through `healthz` in `lib/api`, on the same `API_BASE` as every other call this
 * console makes: `<mount>/api` under the portal, the service's own origin standalone. The
 * `connect-src` this console ships is built from that same value, so a health check on a base
 * of its own would be silently refused, and the pills would render nothing.
 */

// EMBED mode drops the page's own top bar, so the pills sit at a different height there (see
// `.model-pills` in globals.css). Inlined at build time, like the check in page.tsx.
const EMBEDDED = process.env.NEXT_PUBLIC_EMBED === "1";

interface Configured {
  model: string;
  where: string;
}

interface Answer {
  model: string;
  search: boolean;
}

/**
 * Renders once the service has answered `/healthz`, and nothing before that.
 *
 * The null-until-known state is deliberate: a pill defaulting to a model or a runtime while the
 * fetch is in flight would state a falsehood on some page load, and a failed health call renders
 * nothing for the same reason. The page's own error surface owns the failure.
 */
export function ModelPills() {
  const [configured, setConfigured] = useState<Configured | null>(null);
  const [answer, setAnswer] = useState<Answer | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    let live = true;
    const stop = watchAnswers(window, API_BASE, (next) => {
      if (live) setAnswer(next);
    });
    healthz(controller.signal)
      .then((status) => {
        if (!live || !status.runtime || !status.generatorModel) return;
        setConfigured({
          model: status.generatorModel,
          where: status.runtime === "gcp" ? "running on GCP" : "running locally",
        });
      })
      .catch(() => undefined);
    return () => {
      live = false;
      controller.abort();
      stop();
    };
  }, []);

  if (!configured) return null;
  return (
    <div className={EMBEDDED ? "model-pills embedded" : "model-pills"} data-testid="model-pills">
      {answer ? (
        <span className="model-pill answered" data-state="answered" title="answered the last request">
          {answer.model}
        </span>
      ) : (
        <span className="model-pill configured" data-state="configured" title={configured.where}>
          {configured.model}
        </span>
      )}
      {answer?.search ? (
        <span className="model-pill search" title="the last answer used an online search tool">
          Search
        </span>
      ) : null}
    </div>
  );
}
