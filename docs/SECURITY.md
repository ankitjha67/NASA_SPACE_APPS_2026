# AgniSetu security design and threat model

## What is being protected

AgniSetu serves analysis of public NASA data. It has no user accounts and stores nothing about the
people who use it. The assets worth protecting are therefore:

| Asset | Why it matters |
| --- | --- |
| Server compute and memory | Analysis is CPU heavy. An attacker could try to exhaust it |
| The server's network position | The server makes outbound requests. It must not become a proxy to other hosts |
| The FIRMS MAP_KEY | A personal credential with a transaction quota |
| The admin token | Allows starting ingestion jobs |
| Integrity of the store | Wrong data would produce wrong warnings |
| The user's browser | The page must not run or load anything it should not |

## Trust boundaries

1. **Browser to API.** Everything from the browser is untrusted: bodies, query strings, headers.
2. **API to FIRMS.** Upstream CSV is untrusted input. The host is trusted only as far as TLS
   identifies it.
3. **Operator to server.** Configuration and the command line are trusted.
4. **Build time to boundary provider.** `scripts/build_boundaries.py` fetches boundary files once.
   The running server never contacts that provider.

## Threats and controls (STRIDE)

| Threat | Scenario | Control | Verified by |
| --- | --- | --- | --- |
| **Spoofing** | Caller pretends to be an operator | Admin routes need a token compared in constant time (`hmac.compare_digest`). Without a configured token the routes answer 404 as if they did not exist | `test_admin_routes_require_the_token`, `test_admin_routes_do_not_exist_without_a_configured_token` |
| **Spoofing** | Host header attack to poison links or reach a virtual host | `TrustedHostMiddleware` with an allow list | `test_trusted_host_and_unknown_routes` |
| **Tampering** | SQL injection through area ids, period ids, dates or filters | Every query uses bound parameters. Identifiers that reach SQL text come only from catalog constants. Request fields are validated by strict patterns before use | parametrized cases in `test_invalid_requests_are_rejected_with_a_uniform_error` |
| **Tampering** | Malicious or corrupt upstream file | Header check, every field cast with `TRY_CAST` and range checks, rejected rows counted, files with rows outside their year rejected whole, SHA-256 recorded | `test_archive_ingestion_records_provenance_and_rejects_bad_rows`, `test_normalize_sql_fails_closed_on_missing_columns` |
| **Tampering** | Partial write corrupts the store | Files are written to a temp path and moved into place atomically. A lock file keeps two ingestion runs apart | `test_store_lock_prevents_concurrent_ingestion` |
| **Repudiation** | No trace of who did what | Structured JSON access log with request id, client, route, status and duration. The request id is returned to the caller and shown in error messages | `test_security_headers_on_every_response` (id present) |
| **Information disclosure** | MAP_KEY leaks through logs, manifests or the browser | Typed as a secret, redacted from every logged or stored URL, never included in any response. The `httpx` logger that prints URLs is silenced | `test_api_url_validates_inputs_and_redaction_hides_the_key`, `test_log_formatter_redacts_a_map_key_in_messages`, `test_health_and_meta` |
| **Information disclosure** | Stack traces or paths in errors | One error shape with a code, a safe message and a request id. Unhandled errors return a generic message and are logged server side only | `test_invalid_requests_are_rejected_with_a_uniform_error` |
| **Denial of service** | Flood of expensive requests | Token bucket per client with separate budgets for light, heavy and admin routes; `Retry-After` on 429; the table of clients is bounded | `test_rate_limiter_token_bucket`, `test_rate_limit_returns_429_with_retry_after` |
| **Denial of service** | Huge request body | 512 KB limit checked on `Content-Length` and again while the body streams in | `test_body_size_limit_is_enforced_while_streaming` |
| **Denial of service** | Pathological geometry | Vertex limit (20,000), part limit, area limits (25 to 6,000,000 square km), nesting depth limit, finite number checks, repair with `make_valid` | `test_parse_geojson_rejects_bad_input`, `test_parse_geojson_enforces_vertex_and_area_limits` |
| **Denial of service** | Huge map query | Date window capped at 92 days, at most 40,000 points, automatic switch to a density grid | `test_hotspots_points_grid_and_clipping` |
| **Denial of service** | Cache flooding with distinct areas | Area ids are content hashes; memory and disk caches are bounded LRU; at most 3 heavy computations run at once and identical in flight requests share one result | code review, `aoi_cache_entries` setting |
| **Denial of service** | Native crash through concurrency | Shared geometries are never prepared in place; each query uses a private copy. Database cursors are created under a lock and old connections are not closed under running queries | `test_concurrent_requests_on_the_same_area_do_not_crash`, `test_point_classifier_matches_exact_tests_and_keeps_shared_geometry_unprepared` |
| **Elevation of privilege** | Server side request forgery | Outbound URLs are built only from catalog values. The downloader refuses any URL that is not `https://firms.modaps.eosdis.nasa.gov`, never follows redirects, and enforces size and time limits. No request field is ever used as a URL | `test_url_builders_only_target_the_firms_host`, `test_download_refuses_other_hosts` |
| **Elevation of privilege** | Cross site scripting | React escaping throughout, no `dangerouslySetInnerHTML`, map popups built with `textContent`, strict Content Security Policy with no inline script | `test_security_headers_on_every_response`, end to end CSP violation listener |
| **Elevation of privilege** | Clickjacking | `X-Frame-Options: DENY` and `frame-ancestors 'none'` | same |
| **Elevation of privilege** | Path traversal | Preset ids are matched against a strict pattern and looked up in a dictionary, never joined into a path. Export file names are built from a content hash. Static files are served by the framework's guarded file server | `test_presets_search_and_detail` |

## OWASP API Security Top 10 (2023)

| Risk | Status |
| --- | --- |
| API1 Broken object level authorization | Not applicable: there are no per user objects. All data is public |
| API2 Broken authentication | Only the admin routes are privileged. Token in a header, constant time comparison, own rate limit of 10 per minute |
| API3 Broken object property level authorization | Request models forbid unknown fields; responses are built field by field |
| API4 Unrestricted resource consumption | Rate limits, body limit, geometry limits, window limits, point cap, bounded concurrency and caches |
| API5 Broken function level authorization | Admin functions are behind one dependency and absent without a token |
| API6 Unrestricted access to sensitive business flows | The only costly flow is ingestion, which is admin only and single flight |
| API7 Server side request forgery | Fixed host allow list, no redirects, no user supplied URLs |
| API8 Security misconfiguration | Security headers on every response, CORS closed by default, interactive API documentation not served, server banner removed, trusted hosts |
| API9 Improper inventory management | One versioned prefix `/api/v1`; unknown API routes return a uniform 404; the OpenAPI document lists every route |
| API10 Unsafe consumption of APIs | Upstream data is validated row by row and hashed |

## Response headers

```
Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self';
    style-src-attr 'unsafe-inline'; img-src 'self' data: blob: https://gibs.earthdata.nasa.gov
    https://tile.openstreetmap.org; connect-src 'self' https://gibs.earthdata.nasa.gov
    https://tile.openstreetmap.org; worker-src 'self' blob:; child-src 'self' blob:;
    font-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: strict-origin-when-cross-origin
Permissions-Policy: geolocation=(), camera=(), microphone=(), payment=(), usb=(), interest-cohort=()
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Resource-Policy: same-origin
Cache-Control: no-store            (API responses)
Strict-Transport-Security          (when AGNISETU_ENABLE_HSTS=true behind TLS)
```

`style-src-attr 'unsafe-inline'` is needed because the map and chart libraries position elements with
style attributes. Scripts and style sheets must come from the application's own origin. The map
worker is bundled and served from the same origin.

## Secrets

* `.env` is ignored by git. `.env.example` documents every variable without values.
* `AGNISETU_FIRMS_MAP_KEY` and `AGNISETU_ADMIN_TOKEN` are `SecretStr` values. They do not appear in
  logs, in `/api/v1/meta`, in the manifest or in error messages. The browser learns only whether a key
  is configured.
* The admin token typed into the Data tab is held in component memory for that page view only and is
  sent only with admin requests.

## Deployment notes

* The server binds to `127.0.0.1` by default. To expose it, put it behind a TLS terminating proxy, set
  `AGNISETU_ALLOWED_HOSTS` to the public host name, set `AGNISETU_ENABLE_HSTS=true`, and set
  `AGNISETU_TRUST_FORWARDED_FOR=true` only if the proxy overwrites `X-Forwarded-For`.
* Rate limits are held in process memory. Run one process, or put a shared limiter in the proxy.
* Dependencies are pinned in `backend/pyproject.toml` and `frontend/package-lock.json`. Run
  `pip-audit` and `npm audit` before a release.

## Known limits

* There is no authentication for read access by design. Anyone who can reach the server can run
  analyses within the rate limits.
* The in process rate limiter does not coordinate across several server processes.
* Map tiles are loaded by the browser from NASA GIBS and OpenStreetMap, so those services see the
  user's IP address and the tiles requested.
