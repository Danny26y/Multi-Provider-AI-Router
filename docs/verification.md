# Checkpoint F verification record

Checkpoint F.1 remediation is recorded below; the original Checkpoint F results
and benchmark measurements remain historical evidence.

Recorded November 11, 2026. Branch: `feature/nvidia-router-v2`. Starting tree was
clean at Checkpoint E commit `9a200359fe96052f1d856a7a8ec572af188c945a`.
Checkpoint F changes remain working-tree changes pending review; no push, merge,
release publication, deployment, cloud account, or paid resource was created.

## Automated correctness and security

| Check | Windows Python 3.12.8 | Linux/amd64 |
| --- | --- | --- |
| Deterministic suite | 250 passed, 97% reported coverage, 24.05 s | 245 passed, 1 module skipped, 5.89 s; full suite unverified |
| Ruff, including benchmarks and legacy demo | Passed | Not rerun: full development installation blocked |
| Configured mypy scope | Passed, three files | Not rerun: full development installation blocked |
| Compilation and `import main` | Passed | Passed in offline API-image profile |
| `pip check` | Passed for CI and isolated benchmark environments | Passed for API runtime plus pytest subset; full CI/benchmark unverified |
| Strict runtime dependency audit | Zero known vulnerabilities | Zero known vulnerabilities in Linux-target lock inventory |
| Strict CI dependency audit | Zero known vulnerabilities | Zero known vulnerabilities in Linux-target lock inventory |
| Strict optional benchmark audit | Zero known vulnerabilities | Zero known vulnerabilities in Linux-target lock inventory |
| Hash-locked installs | CI baseline retained; fresh benchmark installation passed | API image and offline pytest subset passed; full CI/benchmark blocked |

Coverage scope is `app`, `config`, and `main`; it does not include the whole
dashboard, benchmark harness, or demo scripts. Mypy scope is only
`app/schemas/requests.py`, `config/settings.py`, and `app/core/logging.py`.
The original 238 tests remain passing; 12 deterministic harness regressions
exercise real routing against the ASGI mock and malformed benchmark observations.
No test requires provider credentials or a paid upstream.

The Linux skip is the optional `tests/test_dashboard_ui.py` module, containing
five tests. They passed on Windows, including the separate real-router headless
integration; they were not rerun in a fresh Linux dashboard environment. Linux
coverage was not remeasured in the supplemental offline profile. This is not a
claim of 250 Linux passes or full fresh Linux dependency verification.

Linux-target lock audits were executed through the working host connection
against the exact generated inventories. Advisory scanning does not require
executing the Linux wheels; those clean results do not certify installation.

Application pins remain the accepted Checkpoint E versions: FastAPI 0.162.2,
Starlette 1.10.0, python-dotenv 1.2.2, Streamlit 1.66.1, Pillow 13.0.0, pandas
3.0.3, and uv 0.13.4. Optional Locust is pinned at 2.47.0. Resolution uses the
unchanged `2026-11-08T00:00:00Z` cutoff. Windows and Linux runtime/CI/benchmark
hash locks were generated separately. No incompatible override, audit exception,
unlocked installation fallback, or severity exemption was added.

The full Linux retry used two CPUs and 2 GiB for the verification container. Its first
attempt could not access a stopped Docker engine. After Docker restarted,
installation encountered a PyPI download timeout before tests began. The retry
used a 120-second package-download timeout and a local pip cache. Host-side
hash-verified wheel downloads and the pinned resolver's Linux-target download
were also attempted. PyArrow transferred only 49.3 MB of 55.0 MB, and later
requests failed DNS resolution for PyPI/package hosts. Full installation did not
complete. These setup failures are not passing checks.

The supplemental offline Linux check used the verified API image plus five
hash-locked pytest packages, selected from the Linux CI lock and installed from
cached universal wheels with `--no-index --require-hashes`. It used two CPUs,
1 GiB, no network, read-only source, and temporary writable cache directories.
It ran `python -m pytest -v`, compilation, import, and dependency consistency.
Normal CI still requires the full dashboard-inclusive installation; this offline
subset does not replace or weaken that gate.

## Demonstration and dashboard

All five `scripts/demo.py --scenario ... --verify-only` scenarios passed over
real loopback HTTP: success, fallback, sanitized terminal failure, reasoning-only
failover, and visible truncation. Each checked chat authentication, public health,
protected metrics, and response diagnostics. The demo overrides eligible provider
URLs to the loopback simulator and does not use real inference providers.
The guarded legacy `python test_failover.py` alias also passed. Its previous
unauthenticated requests and unsupported health assumptions were replaced by
delegation to the deterministic fallback demo; import/collection performs no I/O.

A separate Streamlit AppTest run submitted a prompt to the **actual authenticated
loopback router**, displayed `mock response`, selected `nvidia` / `demo-nvidia`,
recorded fallback, and fetched authenticated metrics successfully. This complements
the normal HTTP-transport-mocked UI suite. It is a headless integration check;
an interactive browser visual review was not performed in Checkpoint F.

The first additional dashboard check failed because the verification helper
asserted a nonexistent metrics `ok` field. The helper was corrected to the actual
`outcome`/`metrics` contract, then passed; dashboard implementation was unchanged.

## Benchmarks and documentation

Two bounded two-user native Windows mock benchmarks completed without failures.
The [results and retained evidence](benchmark-results.md) include actual durations,
RPS, p50/p95, selection/fallback counts, limits, source hashes, and the exact
audited dependency lock. No live-provider or deployed-infrastructure measurements
were performed. Background host activity was not isolated; these are demonstrations,
not a statistically controlled provider/capacity comparison.

The previous upstream mock lacked a matching model and `finish_reason`; it was
repaired to satisfy strict validation. Locust now checks usable response structure
and writes bounded provider/fallback observations. A periodic Locust CSV was found
to omit late drained requests in an initial run. Final JSON and a matching final
CSV now share the test-stop event; only rerun results are published as measurements.
The final retained reruns also disable Locust's environment-proxy inheritance,
matching the final measured source hashes and preventing loopback requests from
being forwarded through a configured proxy.

All four [Mermaid diagrams](architecture.md) parsed with Mermaid 12.3.0 and were
reviewed against routing, breaker, health, metrics, and dashboard code. Temporary
documentation tooling is ignored under `.locks/` and is not an application or
container dependency. Repository-relative Markdown links and benchmark evidence
checksums/source hashes are checked locally.

## Container and release boundaries

The final Linux/amd64 API image built successfully from the pinned base and Linux
runtime lock. A fresh container with no network, published ports, or provider
credentials passed UID/GID 10001, root-owned source, absent dotenv/dashboard,
public liveness, unconfigured readiness 503, missing/wrong chat and metrics key
rejection, authenticated metrics, sanitized terminal-error, dependency-consistency,
and sanitized-log checks. Graceful shutdown completed with exit 0. Runtime limits
were one CPU and 512 MiB; these probes do not establish inference capacity.
The final image manifest-list digest recorded by the build was
`sha256:11f101cfc3643c1bc3700984ddc18310de92bd910f1ebb6cbda7056bdc3a17f1`.

The source review checks current tracked and nonignored candidate files for
credential prefixes, private keys, sensitive paths, unintended artifacts, and
documentation links. It is not a full Git-history secret scan or an external
security certification. Nine previously tracked `.pyc` files are intentionally
removed. Evidence is explicitly retained under `benchmarks/evidence/`; generated
logs, caches, temporary dependency tools, and normal run directories remain ignored.
No files are staged by this work.

GitHub Actions YAML is parsed locally and its read-only permission scope retained.
CI now also resolves/audits the optional benchmark lock and lints/compiles the
benchmark modules. Hosted Actions execution is not verified by this local run.

Remaining production gates: hosted CI, OS-image vulnerability scanning, protected
HTTPS/dashboard/metrics ingress, secrets provisioning, staging acceptance, and
an explicitly authorized live-provider probe. There is no deployed environment
or production performance claim. The repository is prepared for engineering
review; production release approval is a separate decision.
Before a GitHub release tag, rerun full Linux development/dashboard installation,
all 250 tests, and Linux lint/type checks in a connected environment or hosted CI.
The current source is ready for engineering review with that release verification
blocker explicit. No background verification/download processes remain running.
No repository `LICENSE` file is present. The owner should choose a license before
advertising reuse permissions; this checkpoint does not invent a licensing choice.

## Files changed

- Entry point and guidance: `README.md`, `docs/architecture.md`, `docs/api.md`,
  `docs/operations.md`, `docs/deployment-readiness.md`, `docs/benchmark-results.md`.
- New portfolio/review material: `docs/demo.md`, `docs/case-study.md`, this record.
- Harness: `benchmarks/mock_upstream.py`, `benchmarks/locustfile.py`,
  `benchmarks/README.md`, new `benchmarks/observations.py`, `scripts/demo.py`,
  `scripts/benchmark_local.py`, `tests/test_demo_benchmark.py`, and the repaired
  legacy `test_failover.py` entry point.
- Reproducibility/gates: `requirements-benchmark.txt`,
  `scripts/lock_dependencies.py`, `.github/workflows/ci.yml`, new `.gitattributes`.
- Reviewed benchmark snapshot: `benchmarks/evidence/2026-11-11-windows/`.
- Cleanup: nine tracked bytecode files below root/app/config `__pycache__`
  directories are deleted. No application routing, security, dashboard, or API
  implementation is replaced by Checkpoint F.

## Local evidence locations

- `.locks/f-windows-pytest.log`, `.locks/f-windows/`: Windows tests, locks, audits.
- `.locks/f-linux/`: Linux locks, failed install/download logs, clean lock-inventory
  audits, and passing `core-verification.log`.
- `.locks/f-demo-*.log`, `.locks/f-dashboard-check.log`: local demonstrations.
- `.locks/f-mermaid.log`, `.locks/f-review.log`: diagram and source review.
- `.locks/f-image-build.log`, `.locks/f-image/`: passing final image build/probes.
- `benchmarks/results/f-final-*`: ignored raw benchmark results.
- `benchmarks/evidence/2026-11-11-windows/`: reviewed, portable benchmark artifacts.

Local evidence directories are intentionally ignored; release artifacts should be
archived with the source SHA and image digest after review. The committed benchmark
snapshot preserves exact bytes through `.gitattributes` for checksum verification.

## Checkpoint F.1 release-blocker remediation

The stale circuit callback defect is addressed with single-use, generation-scoped
admissions. The provider client passes its exact token through success, failure,
and `finally` cleanup. Transitions invalidate older tokens, so a late completion
cannot close another request's recovery probe or modify a newer cooldown.
`is_available` is now a non-mutating availability query; internal callbacks without
a matching live `admission` are ignored. There is no HTTP API contract change.

The curated benchmark audit report is unignored by its exact repository path.
The general `audit-*.json` exclusion remains in effect. Original benchmark files,
hash lock, audit report, and `SHA256SUMS` are preserved byte-for-byte. These
measurements predate F.1; they are not measurements of the ownership remediation.

Regression coverage includes the exact controlled-clock race, overlapping
provider requests, late success/failure/neutral callbacks across generations,
single-use/foreign tokens, concurrent probe admission, cancellation, and errors
immediately after admission. A Git-backed test stages the complete curated bundle
in a temporary repository, verifies staged blob checksums, exports a fresh file
checkout, and verifies every manifest entry again with CRLF conversion enabled.
It also checks that unrelated audit JSON and raw runs stay excluded.

Final F.1 verification on November 12, 2026:

| Check | Result |
| --- | --- |
| Windows Python 3.12.8 full suite | 283 passed in 35.98 s; 97% reported coverage of app/config/main |
| Linux/amd64 Python 3.12.16 offline suite | 277 passed, 2 skipped in 6.42 s |
| Ruff and configured three-file mypy scope | Passed |
| Compilation/import | Passed on Windows and in the Linux source-overlay profile |
| Dependency consistency | Passed for Windows CI environment, Linux runtime/test subset, and F.1 API image |
| Runtime/CI/benchmark inventory audits | All six Windows/Linux-target inventories passed strict audits with zero findings |
| Benchmark manifest | All 16 referenced members verified against staged Git blobs and a fresh checkout |
| Candidate secret/artifact checks | Passed; unrelated generated reports, raw runs, caches and local environment files excluded |
| Local F.1 API image | Build and runtime probes passed; baked breaker/client source hashes match the remediation |

The two Linux skips are the Streamlit UI module (five tests) and the Git-backed
evidence test (Git is absent from the minimal runtime image). Both execute and
pass on Windows. Full fresh Linux dashboard/development verification and hosted
Actions remain outstanding. Linux coverage was not remeasured. The Linux audit
inventories were scanned through the host connection, independently of that
incomplete development installation.

The real repository's candidate commit was also simulated with an isolated Git
index and quarantined object store: 75 candidate files, all 16 manifest entries
present, staged/fresh-checkout hashes correct, and secret-pattern checks clear.
The real index and HEAD remained byte-for-byte unchanged. The evidence bundle
is commit-ready; no files have actually been staged in the real index.

F.1 container probes ran without provider keys, published ports, or external
network access. Non-root identity, health, chat/metrics authentication, sanitized
errors/logs, and graceful shutdown all passed (exit 0). Initial fixture attempts
exposed restricted Windows temporary-directory access and Git's legacy path
length limit; final checks use workspace-local temporary directories and
command-scoped long-path support without changing global Git configuration.
The forced-offline image build missed its install cache; the standard hash-locked
build subsequently passed. No security gate or dependency pin was relaxed.

Only these ten source/configuration/documentation files changed during F.1:
`.gitignore`, `app/core/circuit_breaker.py`, `app/services/provider_client.py`,
`tests/test_circuit_breaker.py`, `tests/test_response_validation.py`,
`tests/test_operational_hardening.py`, new `tests/test_circuit_ownership.py`,
new `tests/test_evidence_integrity.py`, `docs/architecture.md`, and this record.
The 33 new cases complement the original 250 tests. Existing uncommitted F work
and all original benchmark evidence are preserved. Local F.1 logs, audit reports,
coverage, and index/checkout verification are retained under ignored `.locks/f1/`.
No commit, push, merge, deployment, licensing choice, or later checkpoint is
performed by this remediation. Existing deployment acceptance and licensing
decisions remain outside F.1.
