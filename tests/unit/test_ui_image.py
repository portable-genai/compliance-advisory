"""The console image the portal deploys agrees with the Next.js build it packages.

The portal's ``embedded_apps`` needs a digest-pinned ``ui_image`` beside the API image, and
hosted CI never runs ``docker build``, so nothing else would notice these drift:

* the image copies ``.next/standalone``, which exists only when ``next.config.mjs`` asks for
  standalone output; without it the build step fails, on the machine that builds the image;
* the base path and the API base are BUILD-time inputs to Next.js, so a Dockerfile that took
  them at run time would ship a console mounted at the wrong prefix;
* standalone output does not copy ``public/``, and the console's logo lives there;
* a plain ``<img src="/logo.jpg">`` is not rewritten by ``basePath``, so behind the portal it
  asked the portal's own root for the file.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UI = REPO_ROOT / "ui"
DOCKERFILE = UI / "Dockerfile"


def _dockerfile() -> str:
    assert DOCKERFILE.exists(), "ui/Dockerfile is missing: the portal has no console image to pin"
    return DOCKERFILE.read_text(encoding="utf-8")


def test_the_image_copies_the_standalone_output_the_config_produces() -> None:
    dockerfile = _dockerfile()
    assert "/app/.next/standalone" in dockerfile
    assert 'CMD ["node", "server.js"]' in dockerfile
    config = (UI / "next.config.mjs").read_text(encoding="utf-8")
    assert re.search(r'^\s*output:\s*"standalone",', config, flags=re.MULTILINE), (
        "ui/Dockerfile copies .next/standalone but next.config.mjs does not produce it"
    )


def test_the_base_path_and_api_base_are_build_arguments() -> None:
    dockerfile = _dockerfile()
    builder = dockerfile[: dockerfile.index("AS runtime")]
    for name in ("NEXT_PUBLIC_BASE_PATH", "NEXT_PUBLIC_API_BASE"):
        assert f"ARG {name}" in builder, f"{name} must be a build ARG: Next inlines it at build"
        assert builder.index(f"ARG {name}") < builder.index("npm run build")


def test_every_base_image_is_pinned_by_digest() -> None:
    images = re.findall(r"^FROM\s+(\S+)", _dockerfile(), flags=re.MULTILINE)
    assert images, "no FROM line found, so nothing was checked"
    for image in images:
        assert re.search(r"@sha256:[0-9a-f]{64}$", image), f"{image} is not digest-pinned"


def test_the_static_files_the_console_serves_reach_the_runtime_image() -> None:
    assert (UI / "public").is_dir()
    assert "/app/public ./public" in _dockerfile()


def test_the_logo_follows_the_base_path() -> None:
    page = (UI / "app" / "page.tsx").read_text(encoding="utf-8")
    assert 'src="/logo.jpg"' not in page, "a root-relative <img> ignores basePath"
    assert "${BASE_PATH}/logo.jpg" in page


def test_the_runtime_image_runs_as_a_non_root_user_without_a_package_manager() -> None:
    runtime = _dockerfile()[_dockerfile().index("AS runtime") :]
    assert re.search(r"^USER\s+(?!root\b)\S+", runtime, flags=re.MULTILINE)
    assert "/usr/local/bin/npm" in runtime
