# imessagerelay

An async-first, strongly-typed Python client for the [iMessageRelay](https://github.com/ogizhelev/iMessageRelay)
HTTP API. It lets you send iMessages (text and file attachments), manage recipient aliases, poll job
status, and read server health from any `asyncio` application — scripts, bots, schedulers, or larger
automation frameworks.

The client is built on [`httpx`](https://www.python-httpx.org/), ships complete type hints (PEP 561,
`py.typed`), and models every wire shape as an immutable dataclass. It is deliberately **not**
Home Assistant-specific; drop it into any automation.

## Installation

Requires Python 3.10 or newer.

```bash
pip install imessagerelay
```

Or install the latest from source:

```bash
pip install "git+https://github.com/ogizhelev/imessagerelay-py.git"
```

## Quickstart

Create a client, send a message, and read the server's health snapshot. The client is an async
context manager, so it cleans up its underlying HTTP connection pool on exit.

```python
import asyncio

from imessagerelay import IMessageRelayClient


async def main() -> None:
    async with IMessageRelayClient("10.0.0.5", "my-secret-token") as client:
        # Send a message to a configured alias ("mom").
        job = await client.send_message("Dinner's ready!", to="mom")
        print(job.id, job.state)  # e.g. "a1b2c3..." JobState.QUEUED

        # Read the server health snapshot (no auth required).
        health = await client.health()
        print(health.status, health.queue_depth, health.runner_state)


asyncio.run(main())
```

The host may include a scheme and/or port — `IMessageRelayClient("https://10.0.0.5:8443", token)`
works and is normalized internally. You can also build one from a full URL:

```python
client = IMessageRelayClient.from_url("https://10.0.0.5:8443", "my-secret-token")
```

### Sending to multiple recipients

`to` accepts a single alias string (used as-is, including pre-joined `"a,b"`) or any sequence of
aliases, which are stripped and comma-joined for you:

```python
job = await client.send_message("Group ping", to=["mom", "dad", "alex"])
```

### Idempotency

Every send carries an `Idempotency-Key`. When you omit `idempotency_key`, a random one is generated.
Pass your own to make retries safe — re-sending an identical payload with the same key returns the
existing job instead of enqueuing a duplicate (a mismatched payload under a reused key raises
`ConflictError`):

```python
job = await client.send_message("Deploy finished", to="ops", idempotency_key="deploy-2026-11-24")
```

## Alias management

Aliases map a friendly name to a contact handle (phone number or email) on the server.

```python
# List current aliases: {"mom": "+15551234567", ...}
aliases = await client.list_aliases()

# Create or overwrite an alias; returns the stored Alias(name=..., contact=...).
alias = await client.add_alias("mom", "+15551234567")
print(alias.name, alias.contact)

# Delete an alias.
await client.remove_alias("mom")
```

## Attachments

Reading a file from disk is synchronous, so attachments are built by the **caller** via the
`Attachment` constructors — the async client never touches the filesystem. Build an `Attachment`,
then pass it to `send_message`:

```python
from imessagerelay import Attachment

# from_path reads and base64-encodes the file (blocking file IO, done up front).
attachment = Attachment.from_path("/tmp/report.pdf")
job = await client.send_message("Monthly report attached", to="mom", attach=attachment)
```

You can also build one from in-memory bytes, or override the filename/MIME type:

```python
attachment = Attachment.from_bytes(pdf_bytes, name="report.pdf", mime="application/pdf")
```

For the common case, `send_file` is a convenience wrapper that calls `Attachment.from_path` for you
(note: it briefly blocks while reading the file before the async request):

```python
job = await client.send_file("/tmp/report.pdf", to="mom", text="Monthly report attached")
```

## Waiting for delivery

A freshly enqueued job starts in `JobState.QUEUED`. Use `wait_for_job` to poll until the job reaches
a terminal state (`SUBMITTED`, `FAILED`, or `UNCERTAIN`):

```python
from imessagerelay import JobState

job = await client.send_message("Ping", to="mom")
final = await client.wait_for_job(job.id, poll_interval=1.0, timeout=60.0)

if final.state is JobState.SUBMITTED:   # or: final.state.is_success
    print("Delivered to the Messages app")
elif final.state is JobState.FAILED:
    print("Permanently rejected")
else:  # JobState.UNCERTAIN
    print("Dispatch callback was lost — delivery unknown")
```

`wait_for_job` raises `IMessageRelayTimeoutError` if no terminal state is reached within `timeout`
seconds. `JobState.is_terminal` and `JobState.is_success` are available for your own polling logic.

## Error handling

Every exception this library raises inherits from `IMessageRelayError`, so a single `except` can
catch them all. Transport problems and HTTP error responses are distinct branches:

```python
from imessagerelay import (
    IMessageRelayError,
    IMessageRelayConnectionError,
    IMessageRelayTimeoutError,
    APIError,
    AuthenticationError,
    ForbiddenError,
    ValidationError,
    ConflictError,
    RateLimitError,
    ServerError,
)

try:
    job = await client.send_message("Hi", to="mom")
except AuthenticationError:
    ...  # bad or missing token (HTTP 401)
except ForbiddenError:
    ...  # alias not configured or not permitted (HTTP 403)
except RateLimitError:
    ...  # back off and retry (HTTP 429)
except APIError as exc:
    # Any other HTTP error carries the parsed response.
    print(exc.status_code, exc.message, exc.payload)
except IMessageRelayConnectionError:
    ...  # DNS failure, connection refused, TLS error, or timeout
```

Any `APIError` exposes `.status_code: int`, `.message: str`, and `.payload: dict | None`, and
stringifies as `"[<status_code>] <message>"`. HTTP error responses are mapped to a specific subclass:

| HTTP status | Exception class        | Meaning                                       |
| ----------- | ---------------------- | --------------------------------------------- |
| 400         | `ValidationError`      | Request failed server-side validation         |
| 401         | `AuthenticationError`  | Unknown, revoked, or missing token            |
| 403         | `ForbiddenError`       | Alias not configured or not permitted         |
| 404         | `NotFoundError`        | Job or resource not found                     |
| 409         | `ConflictError`        | Idempotency-key payload mismatch              |
| 429         | `RateLimitError`       | Rate limit exceeded                           |
| 500–599     | `ServerError`          | Server-side failure                           |
| other 4xx   | `APIError` (base)      | Unmapped client error                         |

Transport-level failures map as follows:

| Condition                               | Exception class                 |
| --------------------------------------- | ------------------------------- |
| Request exceeded the timeout            | `IMessageRelayTimeoutError`     |
| DNS / connection refused / TLS failure  | `IMessageRelayConnectionError`  |

`IMessageRelayTimeoutError` is a subclass of `IMessageRelayConnectionError`, which in turn is a
subclass of `IMessageRelayError`.

## TLS / self-signed certificates

The iMessageRelay server is an HTTPS service that typically presents a **bundled self-signed
certificate**. Because of that, certificate verification is **disabled by default**
(`verify_ssl=False`). This is a deliberate default for the common home-LAN deployment where the
server has no publicly trusted certificate.

If your deployment has a properly trusted certificate (or you have installed the server's CA into
your trust store), turn verification back on:

```python
client = IMessageRelayClient("relay.example.com", token, verify_ssl=True)
```

For full control over TLS (custom CA bundles, client certs, connection limits), inject your own
`httpx.AsyncClient`. A client you inject is used as-is and is **not** closed by `aclose()` — you own
its lifecycle:

```python
import httpx

http = httpx.AsyncClient(verify="/path/to/ca.pem", timeout=30.0)
client = IMessageRelayClient("relay.example.com", token, client=http)
```

## Discovery (mDNS)

The iMessageRelay server advertises itself over Bonjour/mDNS as `_imessagerelay._tcp.local.`, so you
can discover its host and port on the local network instead of hard-coding them (for example with
`zeroconf`). This library does not perform discovery itself; resolve the address however you like,
then pass the result to `IMessageRelayClient` or `IMessageRelayClient.from_url`.

## Related projects

- **iMessageRelay** — the relay server app that exposes this HTTP API:
  <https://github.com/ogizhelev/iMessageRelay>
- **iMessageRelay-HA** — the Home Assistant custom integration built on this library:
  <https://github.com/ogizhelev/iMessageRelay-HA>

## License

MIT
