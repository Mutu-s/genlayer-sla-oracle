# SLA Breach Oracle — a GenLayer Intelligent Contract

A reusable primitive for **infrastructure SLAs**: RPC providers, validators and
staking operators, hosting, public APIs. It turns a provider's public status
page into an on-chain, validator-verified uptime ledger and computes the SLA
credit owed for each month.

> Design rule: **the LLM extracts, the contract computes.**
> Validators agree on *facts* read from the evidence (is this service affected,
> is it resolved, when did impact start and end). Every number that matters
> (outage minutes, monthly uptime, breach tier, credit) is computed
> deterministically by the contract, never by the model.

Built for GenVM v0.4 (`py-genlayer:kzybg95…`): Studio v0.123+, Testnet
Bradbury Phase 3 and Clarke.

## How it works

```
provider ── register_service(id, name, status_domain, target, credit tiers)
anyone   ── file_incident(id, https://<status_domain>/incidents/…)
              │
              ├─ every validator renders the page itself (gl.nondet.web.render)
              ├─ LLM → JSON facts {affects_service, resolved, start_utc, end_utc}
              ├─ facts normalised identically by leader and validators
              └─ gl.vm.run_nondet: validator re-derives and compares with tolerance
              │
              ▼ deterministic
           incident stored · minutes added to service|YYYY-MM ledger
anyone   ── get_period_report(id, "YYYY-MM") → uptime_bps, tier, credit_bps
```

### Consensus design (the interesting part)

| Concern | How it is handled |
|---|---|
| Wording differs between models | Only structured facts are compared; `summary` is never compared |
| Status pages round times / show local zones | Start and end must each be within **10 min**; times are converted to UTC before comparison |
| Leader inflates the outage | Validators recompute duration from their own reading: must match within `max(10 min, 10 %)`, and the leader's stored minutes must equal `end − start` of its own times |
| Wrong verdict (unrelated incident counted) | `affects_service` must match exactly |
| Unresolved incident, missing times, end ≤ start, > 1 month | Rejected with an `[EVIDENCE]` error; validators agree on a failure **only if they hit the same evidence error** |
| Page could not be fetched | `[TRANSIENT]`; validators agree only if they also failed to fetch |
| Malformed model output | `[LLM_ERROR]` → always disagree, forcing a rotation |
| Prompt injection from the page | Page text is delimited as untrusted data; nothing the model returns is executed, only parsed into typed facts |
| Arbitrary evidence URLs | `https` only, host must be the registered status domain or its subdomain (no `@`, no ports, no lookalike `status.example.com.evil.com`) |
| Double counting | One incident per `(service, url)`; URL fragments are stripped |

### State

| Storage | Purpose |
|---|---|
| `services: TreeMap[str, Service]` | provider, name, status domain, target/severe thresholds and credits (bps) |
| `incidents: TreeMap[str, Incident]` | `service|url` → agreed facts, claimant, period |
| `period_incidents: TreeMap[str, DynArray[str]]` | `service|YYYY-MM` → incident URLs |
| `downtime: TreeMap[str, u32]` | `service|YYYY-MM` → counted outage minutes |

Unrelated incidents are stored too (with `affected = false`, 0 minutes) so the
same URL cannot be re-filed hoping for a different verdict; GenLayer's appeal
process is the path to contest a decision.

## API

| Method | Kind | Notes |
|---|---|---|
| `register_service(service_id, name, status_domain, target_bps, credit_bps, severe_bps, severe_credit_bps)` | write | e.g. `9990, 1000, 9900, 3000` = 99.90 % target, 10 % credit; below 99.00 %, 30 % |
| `set_active(service_id, active)` | write | provider only |
| `file_incident(service_id, incident_url)` | write | anyone; nondeterministic, consensus as above |
| `get_service(service_id)` | view | |
| `get_incident(service_id, incident_url)` | view | |
| `get_period_report(service_id, "YYYY-MM")` | view | month length incl. leap years; `tier` = `ok`/`breach`/`severe` |

## Live run (GenLayer Studio, 27 Nov 2026)

| Step | Tx |
|---|---|
| Deploy `0x52F888cBb84e2690fF9695574600CfFB0CdE4c3F` | `0x2485706a…477cf` |
| `register_service("github-api", "GitHub API Requests", "githubstatus.com", 9990, 1000, 9900, 3000)` | `0x5d44b3ba…1c70c` |
| `file_incident("github-api", "https://www.githubstatus.com/incidents/qn8mfv6q5hlx")` | `0xfd289c2d…bda07` |

Validators independently rendered the real GitHub incident of 25 Nov 2026 and
agreed on `16:21 → 16:52 UTC` (31 min) affecting API Requests. The contract
then reported November: `downtime_minutes 31`, `uptime_bps 9992`, `tier ok`,
`credit_bps 0`. Full output: [`deployments/studionet.json`](deployments/studionet.json).

## Tests

17 direct-mode tests (`genlayer-test`, milliseconds, no network):
registration rules, provider-only actions, domain/https checks, lookalike
hosts, duplicates and fragments, UTC normalisation across a month boundary,
accumulation into the severe tier, unrelated and unresolved incidents, and the
**validator logic itself**: accepting small time differences, rejecting an
inflated outage, rejecting a flipped verdict, and agreeing with a leader error
only when the validator hits the same evidence error.

```bash
python -m venv .venv && .venv/bin/pip install genlayer-test pytest
.venv/bin/python -m pytest tests -q        # 17 passed
```

`tests/conftest.py` works around a genlayer-test 0.30.2 incompatibility with
the v0.4 SDK: its LLM mock hands the SDK a parsed dict, while
`exec_prompt(response_format="json")` on v0.4 expects JSON text.

## Limits and extensions

- An outage is attributed to the month it **started** in (no cross-month split).
- Overlapping incidents on the same service are summed; providers that publish
  one incident per component should register one service per component.
- Natural extensions: escrowed monthly fees paid out by `credit_bps`, a
  provider bond slashed on `severe`, or an EVM message to settle credits on
  another chain once a month is closed.

## License

MIT
