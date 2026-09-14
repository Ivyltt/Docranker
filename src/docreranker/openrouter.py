"""OpenRouter HTTP client with durable per-request accounting and stage cache.

The local budget is enforced against recorded charges and conservative reservations.
It is NOT a guarantee of a platform billing cap: tokenizer/provider differences and
requests whose connection is lost can incur charges not yet reported by the API.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import fcntl
from functools import wraps
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

import requests

from .costs import ModelPrice

LOGGER = logging.getLogger(__name__)


def retry_after_seconds(value, now=None):
    """Parse standard Retry-After delta-seconds or an HTTP date; invalid is None."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except (ValueError, TypeError):
        try:
            date = parsedate_to_datetime(str(value))
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = date.timestamp() - (time.time() if now is None else now)
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


class BudgetExceeded(RuntimeError):
    pass


class OpenRouterError(RuntimeError):
    pass


class AnnotationCancelled(OpenRouterError):
    pass


class InvalidTeacherOutput(OpenRouterError):
    """A billed, unusable teacher completion; optionally reject its whole example."""
    def __init__(self, message, request_key=None, generation_id=None):
        super().__init__(message)
        self.request_key = request_key
        self.generation_id = generation_id


def _db_locked(method):
    @wraps(method)
    def locked(self, *args, **kwargs):
        with self._db_lock:
            return method(self, *args, **kwargs)
    return locked


def canonical_hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


class OpenRouterClient:
    """One writer per cache directory; SQLite atomically commits cache + cost.

    max_cost_usd is cumulative for this cache directory, including previous runs.
    A network timeout retains its reservation before a bounded retry is attempted.
    """

    def __init__(self, cache_dir: Path, price: ModelPrice, max_cost_usd: float,
                 api_key=None, session=None, max_attempts=3, timeout=120,
                 sleep=time.sleep, base_url="https://openrouter.ai/api/v1", session_factory=None,
                 max_retry_delay=300, monotonic=time.monotonic):
        if not math.isfinite(max_cost_usd) or max_cost_usd <= 0:
            raise ValueError("A finite positive --max-cost-usd is required")
        if not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts must be between 1 and 5")
        if not math.isfinite(max_retry_delay) or not 0 < max_retry_delay <= 600:
            raise ValueError("max_retry_delay must be between 0 and 600 seconds")
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if not self._api_key:
            raise ValueError("Set OPENROUTER_API_KEY before executing annotations")
        self.price, self.max_cost_usd = price, max_cost_usd
        self.max_attempts, self.timeout, self.sleep = max_attempts, timeout, sleep
        self.max_retry_delay = max_retry_delay
        self._monotonic = monotonic
        self._cooldown_lock = threading.Lock()
        self._cooldown_until = 0.0
        self.base_url = base_url.rstrip("/")
        self._db_lock = threading.RLock()
        self._cancelled = threading.Event()
        self._request_locks = {}
        self._local = threading.local()
        self._sessions = []
        self._provided_session = session  # A supplied test session must be thread-safe if shared.
        self._session_factory = session_factory or requests.Session
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._lock = (self.cache_dir / ".writer.lock").open("a")
        try:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock.close()
            raise OpenRouterError("Another annotation writer is using this cache directory") from None
        self.db = sqlite3.connect(str(self.cache_dir / "usage.sqlite3"), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        # DELETE avoids SQLite WAL shared-memory assumptions on a shared filesystem.
        # A node-local cache is still preferred; the writer lock is mandatory.
        self.db.execute("PRAGMA journal_mode=DELETE")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS attempts (
                id TEXT PRIMARY KEY, request_key TEXT NOT NULL, stage TEXT NOT NULL,
                model TEXT NOT NULL, state TEXT NOT NULL, reserved REAL NOT NULL,
                charged REAL, cost_source TEXT, generation_id TEXT, usage_json TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cache (
                request_key TEXT PRIMARY KEY, response_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS attempts_request_state ON attempts(request_key,state);
            CREATE INDEX IF NOT EXISTS attempts_budget_covering
                ON attempts(charged,reserved,cost_source);
        """)
        (self.cache_dir / "latest_price.json").write_text(
            json.dumps(asdict(price), indent=2) + "\n", encoding="utf-8")

    @property
    def session(self):
        if self._provided_session is not None:
            return self._provided_session
        if not hasattr(self._local, "session"):
            self._local.session = self._session_factory()
            with self._db_lock:
                self._sessions.append(self._local.session)
        return self._local.session

    def cancel_pending(self):
        """Stop new requests/retries; an already-sent request still gets settled."""
        self._cancelled.set()

    def raise_if_cancelled(self):
        if self._cancelled.is_set():
            raise AnnotationCancelled("Annotation stopped after another worker failed; completed stages are cached")

    def _pause(self, seconds):
        """Bound each wait and let a failed peer interrupt a long provider cooldown."""
        while seconds > 0:
            self.raise_if_cancelled()
            chunk = min(30.0, seconds)
            if self.sleep is time.sleep:
                self._cancelled.wait(chunk)
            else:
                self.sleep(chunk)
            seconds -= chunk
        self.raise_if_cancelled()

    def _wait_for_cooldown(self):
        while True:
            with self._cooldown_lock:
                delay = self._cooldown_until - self._monotonic()
            if delay <= 0:
                return
            self._pause(min(30.0, delay))

    def _retry_delay(self, response, attempt):
        supplied = retry_after_seconds(response.headers.get("Retry-After"))
        delay = supplied if supplied is not None else (
            30.0 * 2 ** attempt if response.status_code == 429 else min(30.0, 2 ** attempt))
        if delay > self.max_retry_delay:
            # Never truncate a provider's required wait and retry too early.
            raise OpenRouterError(f"HTTP {response.status_code} requests a {delay:.0f}s cooldown, "
                                  f"exceeding the {self.max_retry_delay:.0f}s bounded wait; resume later")
        return delay

    def _backoff(self, response, attempt):
        delay = self._retry_delay(response, attempt)
        LOGGER.warning("OpenRouter HTTP %s; retry %s/%s after %.1fs",
                       response.status_code, attempt + 2, self.max_attempts, delay)
        if response.status_code == 429:
            # Shared-provider throttling applies to new queries as well as this retry.
            with self._cooldown_lock:
                self._cooldown_until = max(self._cooldown_until, self._monotonic() + delay)
            self._wait_for_cooldown()
        else:
            self._pause(delay)

    @_db_locked
    def close(self):
        # The caller must join all workers before closing this shared client.
        if getattr(self, "db", None) is not None:
            self.export_ledger()
            self.db.close()
            self.db = None
        if not self._lock.closed:
            fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
            self._lock.close()
        for session in self._sessions:
            close = getattr(session, "close", None)
            if close:
                close()
        self._sessions.clear()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @contextmanager
    def _transaction(self):
        with self._db_lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self.db.rollback()
                raise
            else:
                self.db.commit()

    @_db_locked
    def budget_summary(self) -> dict:
        row = self.db.execute("""SELECT COALESCE(SUM(COALESCE(charged,reserved)),0) total,
            COALESCE(SUM(CASE WHEN cost_source IN ('usage.cost','generation.total_cost')
                       THEN charged ELSE 0 END),0) actual,
            COALESCE(SUM(CASE WHEN cost_source='estimated' THEN charged ELSE 0 END),0) estimated,
            COALESCE(SUM(CASE WHEN charged IS NULL THEN reserved ELSE 0 END),0) held
            FROM attempts INDEXED BY attempts_budget_covering""").fetchone()
        return {"accounted_usd": row["total"], "actual_reported_usd": row["actual"],
                "estimated_usd": row["estimated"], "unresolved_reserved_usd": row["held"],
                "remaining_usd": self.max_cost_usd - row["total"],
                "max_cost_usd": self.max_cost_usd}

    def preflight(self, estimated_remaining_usd: float):
        if not math.isfinite(estimated_remaining_usd) or estimated_remaining_usd < 0:
            raise ValueError("Preflight estimate must be finite and nonnegative")
        if self.budget_summary()["remaining_usd"] + 1e-12 < estimated_remaining_usd:
            raise BudgetExceeded("Preflight cost exceeds the remaining local USD budget; "
                                 "reduce --limit or increase the explicitly chosen budget")

    def _reserve(self, key, stage, reserve):
        attempt_id = uuid.uuid4().hex
        with self._transaction():
            self.raise_if_cancelled()
            self.preflight(reserve)
            self.db.execute("""INSERT INTO attempts
                (id,request_key,stage,model,state,reserved,created_at)
                VALUES (?,?,?,?,?,?,?)""", (attempt_id, key, stage, self.price.model,
                    "reserved", reserve, datetime.now(timezone.utc).isoformat()))
        return attempt_id

    def _settle(self, attempt_id, cost=None, source=None, state="uncertain", result=None, key=None):
        with self._transaction():
            self.db.execute("""UPDATE attempts SET state=?,charged=?,cost_source=?,
                generation_id=?,usage_json=? WHERE id=?""", (
                state, cost, source, (result or {}).get("id"),
                json.dumps((result or {}).get("usage", {})), attempt_id))
            if key is not None and result is not None:
                self.db.execute("INSERT OR REPLACE INTO cache VALUES (?,?)",
                                (key, json.dumps(result, ensure_ascii=False)))

    @_db_locked
    def export_ledger(self):
        """Readable ledger contains usage only, never credentials or image payloads."""
        rows = [dict(row) for row in self.db.execute("SELECT * FROM attempts ORDER BY created_at")]
        destination = self.cache_dir / "usage.jsonl"
        temporary = destination.with_suffix(".tmp")
        temporary.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        temporary.replace(destination)
        (self.cache_dir / "budget.json").write_text(
            json.dumps(self.budget_summary(), indent=2) + "\n", encoding="utf-8")

    def _payload(self, messages, max_tokens):
        return {"model": self.price.model, "messages": messages, "max_tokens": max_tokens,
                "temperature": 0, "stream": False,
                "reasoning": {"enabled": False},
                "provider": {"sort": "price", "allow_fallbacks": True,
                             "require_parameters": True,
                             "max_price": {"prompt": self.price.input_per_million,
                                           "completion": self.price.output_per_million}}}

    @_db_locked
    def is_cached(self, messages, max_tokens, stage):
        payload = self._payload(messages, max_tokens)
        key = canonical_hash({"stage": stage, "payload": payload})
        return self._cached_response(key, payload, stage) is not None

    @_db_locked
    def _cached_response(self, key, payload, stage):
        row = self.db.execute("SELECT response_json FROM cache WHERE request_key=?", (key,)).fetchone()
        if row:
            return json.loads(row[0])
        # Routing-only migration: preserve paid stages from the old no-fallback
        # client, without accepting a different model, messages, limits or prices.
        legacy = {**payload, "provider": {**payload["provider"], "allow_fallbacks": False}}
        legacy_key = canonical_hash({"stage": stage, "payload": legacy})
        row = self.db.execute("SELECT response_json FROM cache WHERE request_key=?", (legacy_key,)).fetchone()
        if row:
            result = json.loads(row[0])
            if result.get("model") == self.price.model:
                return result
        return None

    @_db_locked
    def _previous_invalid_output(self, key, payload, stage):
        legacy = {**payload, "provider": {**payload["provider"], "allow_fallbacks": False}}
        legacy_key = canonical_hash({"stage": stage, "payload": legacy})
        row = self.db.execute("""SELECT request_key,generation_id FROM attempts
            WHERE request_key IN (?,?) AND model=? AND state='invalid_output'
            ORDER BY created_at DESC LIMIT 1""", (key, legacy_key, self.price.model)).fetchone()
        return dict(row) if row else None

    @_db_locked
    def cached_artifact(self, key):
        row = self.db.execute("SELECT response_json FROM cache WHERE request_key=?",
                              ("artifact:" + key,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_artifact(self, key, value):
        with self._transaction():
            self.db.execute("INSERT OR REPLACE INTO cache VALUES (?,?)",
                            ("artifact:" + key, json.dumps(value, ensure_ascii=False)))

    def _reported_cost(self, raw, fallback_reserve):
        usage = raw.get("usage") or {}
        value = usage.get("cost")
        if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
            return value, "usage.cost"
        if raw.get("id"):
            try:
                response = self.session.get(self.base_url + "/generation",
                    params={"id": raw["id"]}, headers=self._headers(), timeout=min(30, self.timeout))
                if response.status_code == 200:
                    value = response.json().get("data", {}).get("total_cost")
                    if isinstance(value, (int, float)) and math.isfinite(value) and value >= 0:
                        return value, "generation.total_cost"
            except (requests.RequestException, ValueError, TypeError):
                pass
        # Missing billing must not free reserved money on guessed token counts.
        # Even if usage token counts are available, retain the larger reservation.
        prompt, completion = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if isinstance(prompt, int) and isinstance(completion, int) and min(prompt, completion) >= 0:
            fallback_reserve = max(fallback_reserve, self.price.cost(prompt, completion))
        return fallback_reserve, "estimated"

    def _headers(self):
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json",
                "X-OpenRouter-Title": "DocReranker classroom project"}

    def complete(self, messages: list, *, stage: str, max_tokens: int,
                 reserved_input_tokens: int) -> dict:
        if max_tokens <= 0 or reserved_input_tokens <= 0:
            raise ValueError("Input reservation and max_tokens must be positive")
        if reserved_input_tokens + max_tokens >= 200_000:
            raise ValueError("This annotation client only supports inputs below long-context pricing tiers")
        payload = self._payload(messages, max_tokens)
        key = canonical_hash({"stage": stage, "payload": payload})
        with self._db_lock:
            request_lock = self._request_locks.setdefault(key, threading.Lock())
        # Coalesce identical concurrent stage requests without blocking unrelated HTTP.
        with request_lock:
            try:
                return self._complete_request(key, payload, stage, max_tokens, reserved_input_tokens)
            except InvalidTeacherOutput:
                # The annotation coordinator decides whether to skip or fail the job.
                # Do not poison other workers when this particular failure is handled.
                raise
            except BaseException:
                self.cancel_pending()
                raise

    def _complete_request(self, key, payload, stage, max_tokens, reserved_input_tokens):
        cached = self._cached_response(key, payload, stage)
        if cached is not None:
            return cached
        rejected = self._previous_invalid_output(key, payload, stage)
        if rejected:
            raise InvalidTeacherOutput("Previously billed teacher output was invalid; request will not be repeated",
                                       **rejected)
        reserve = self.price.cost(reserved_input_tokens, max_tokens)
        for attempt in range(self.max_attempts):
            self.raise_if_cancelled()
            self._wait_for_cooldown()
            attempt_id = self._reserve(key, stage, reserve)
            try:
                response = self.session.post(self.base_url + "/chat/completions",
                    headers=self._headers(), json=payload, timeout=self.timeout)
            except requests.RequestException:
                self._settle(attempt_id, state="network_unknown")
                if attempt + 1 == self.max_attempts:
                    raise OpenRouterError("OpenRouter network request failed; reservation retained") from None
                self._pause(min(30, 2 ** attempt))
                continue
            if response.status_code != 200:
                transient = response.status_code == 429 or response.status_code >= 500
                # 5xx can hide an upstream generation; do not release its reservation.
                known_rejected = 400 <= response.status_code < 500
                self._settle(attempt_id, cost=0 if known_rejected else None,
                             source="rejected" if known_rejected else None,
                             state=f"http_{response.status_code}")
                if transient and attempt + 1 < self.max_attempts:
                    self._backoff(response, attempt)
                    continue
                raise OpenRouterError(f"OpenRouter returned HTTP {response.status_code}; see account activity")
            try:
                raw = response.json()
                cost, source = self._reported_cost(raw, reserve)
                choices = raw.get("choices") or [{}]
                choice = choices[0]
                content = choice.get("message", {}).get("content")
                refused = bool(choice.get("message", {}).get("refusal"))
                provider_error = bool(raw.get("error") or choice.get("error") or
                                      choice.get("finish_reason") == "error")
                valid = isinstance(content, str) and bool(content.strip()) and choice.get("finish_reason") == "stop"
                valid = valid and not provider_error and not refused
                result = {"id": raw.get("id"), "model": raw.get("model", self.price.model),
                          "content": content, "finish_reason": choice.get("finish_reason"),
                          "usage": raw.get("usage", {}), "cost_usd": cost, "cost_source": source}
            except (ValueError, TypeError, IndexError, AttributeError):
                self._settle(attempt_id, state="malformed_unknown")
                raise OpenRouterError("Malformed OpenRouter response; reservation retained") from None
            state = "generation_error" if provider_error else ("complete" if valid else "invalid_output")
            self._settle(attempt_id, cost, source, state,
                         result=result, key=key if valid else None)
            # The SQLite ledger is durable already. Export the complete JSONL on close,
            # avoiding O(N^2) file rewrites over a 43,200-request annotation job.
            if self.budget_summary()["remaining_usd"] < -1e-12:
                raise BudgetExceeded("Reported charge exceeded the reservation; execution stopped. "
                                     "Cached result and actual charge are saved; check platform activity")
            if provider_error:
                raise OpenRouterError("Provider failed during generation; billed attempt recorded")
            if not valid:
                raise InvalidTeacherOutput("Teacher output was empty, refused, or truncated; billed attempt recorded",
                                           request_key=key, generation_id=result.get("id"))
            return result
        raise OpenRouterError("No successful request")
