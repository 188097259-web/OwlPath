import asyncio
import ipaddress
import os
import socket
import threading
import weakref
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .errors import ProviderInvocationError
from .models import DataBoundary


BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.google",
    "instance-data",
}

BLOCKED_METADATA_IPS = {
    "169.254.169.254",  # AWS, Azure and other IMDS implementations
    "100.100.100.200",  # Alibaba Cloud metadata
    "fd00:ec2::254",
}


def _dns_error_details(*, transient: bool, timed_out: bool = False) -> dict:
    """Return persistence-safe DNS diagnostics without host/error prose."""

    return {
        "timeout_phase": "dns_preflight",
        "request_dispatched": False,
        "dns_failure_class": (
            "timeout" if timed_out else "temporary" if transient else "unresolved"
        ),
    }


def _blocked_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return True
    # External egress is fail-closed: CGNAT, documentation, benchmark,
    # reserved and overlay ranges are all non-global even when Python does not
    # label them `private`.
    return not ip.is_global


def _metadata_or_special_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return True
    return bool(
        str(ip) in BLOCKED_METADATA_IPS
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _configured_egress_proxy_networks() -> List[Any]:
    """Parse OWLPATH_ALLOW_EGRESS_PROXY_CIDRS.

    Some local research sandboxes answer external DNS queries with fake-IP
    proxy addresses (commonly 198.18.0.0/15) and route the subsequent TLS
    connection by SNI. Those addresses are non-global, so the default
    fail-closed policy rejects them. The operator can explicitly acknowledge
    the sandbox egress CIDRs with this environment variable. The allowance is
    applied only to addresses obtained by resolving a provider hostname;
    literal provider URLs targeting those addresses remain rejected.
    """

    networks: List[Any] = []
    for token in os.environ.get("OWLPATH_ALLOW_EGRESS_PROXY_CIDRS", "").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            networks.append(ipaddress.ip_network(token, strict=False))
        except ValueError:
            continue
    return networks


def _in_configured_egress_proxy(address: str, networks: List[Any]) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    return any(ip in network for network in networks)


def validate_outbound_url(url: str, boundary: DataBoundary, resolve_dns: bool = True) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ProviderInvocationError("unsafe_provider_url", "Provider URL must use http or https")
    if boundary == DataBoundary.EXTERNAL and parsed.scheme != "https":
        raise ProviderInvocationError(
            "external_provider_requires_https",
            "External provider URLs must use HTTPS; HTTP is permitted only for local/private providers",
        )
    if parsed.username or parsed.password:
        raise ProviderInvocationError("unsafe_provider_url", "Provider URL must not contain credentials")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"metadata.google.internal", "metadata.google", "instance-data"}:
        raise ProviderInvocationError("unsafe_provider_url", "Provider URLs cannot target metadata hosts")
    if host in BLOCKED_HOSTNAMES or host.endswith(".localhost") or host.endswith(".local"):
        if boundary != DataBoundary.LOCAL:
            raise ProviderInvocationError("unsafe_provider_url", "External providers cannot target local or metadata hosts")
    try:
        literal_ip = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        literal_ip = None
    if literal_ip is not None and (str(literal_ip) in BLOCKED_METADATA_IPS or literal_ip.is_link_local):
        raise ProviderInvocationError("unsafe_provider_url", "Provider URLs cannot target link-local or cloud metadata addresses")
    if boundary == DataBoundary.LOCAL:
        if literal_ip is not None:
            if literal_ip.is_private or literal_ip.is_loopback:
                return
            raise ProviderInvocationError(
                "local_boundary_requires_private_url",
                "Providers marked local must target a loopback or private-network address",
            )
        if not resolve_dns:
            # At save time we deliberately avoid DNS (saving a temporarily
            # offline local service should remain possible). Only names with an
            # explicit local suffix are unambiguous enough to accept then.
            if host.endswith(".local") or host.endswith(".localhost") or host == "localhost":
                return
            raise ProviderInvocationError(
                "local_boundary_requires_private_url",
                "Providers marked local must use localhost, a .local name, or a private IP address",
            )
        try:
            local_addresses: List[str] = list({
                item[4][0] for item in socket.getaddrinfo(
                    host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM
                )
            })
        except socket.gaierror as exc:
            transient = exc.errno == socket.EAI_AGAIN
            raise ProviderInvocationError(
                "provider_dns_error",
                "Provider hostname could not be resolved",
                retryable=transient,
                safe_details=_dns_error_details(transient=transient),
            ) from exc
        except TimeoutError as exc:
            raise ProviderInvocationError(
                "provider_dns_error",
                "Provider hostname resolution timed out",
                retryable=True,
                safe_details=_dns_error_details(transient=True, timed_out=True),
            ) from exc
        if (
            not local_addresses
            or any(_metadata_or_special_ip(address) for address in local_addresses)
            or any(not (ipaddress.ip_address(address.split("%", 1)[0]).is_private or ipaddress.ip_address(address.split("%", 1)[0]).is_loopback) for address in local_addresses)
        ):
            raise ProviderInvocationError(
                "local_boundary_requires_private_url",
                "Providers marked local must resolve only to loopback or private-network addresses",
            )
        return
    try:
        if _blocked_ip(host):
            ipaddress.ip_address(host)
            raise ProviderInvocationError("unsafe_provider_url", "External providers cannot target private or metadata addresses")
    except ValueError:
        pass
    if not resolve_dns:
        return
    try:
        addresses: List[str] = list({item[4][0] for item in socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)})
    except socket.gaierror as exc:
        transient = exc.errno == socket.EAI_AGAIN
        raise ProviderInvocationError(
            "provider_dns_error",
            "Provider hostname could not be resolved",
            retryable=transient,
            safe_details=_dns_error_details(transient=transient),
        ) from exc
    except TimeoutError as exc:
        raise ProviderInvocationError(
            "provider_dns_error",
            "Provider hostname resolution timed out",
            retryable=True,
            safe_details=_dns_error_details(transient=True, timed_out=True),
        ) from exc
    proxy_networks = _configured_egress_proxy_networks()
    if not addresses or any(
        _blocked_ip(address)
        and not _in_configured_egress_proxy(address, proxy_networks)
        for address in addresses
    ):
        raise ProviderInvocationError("unsafe_provider_url", "External provider hostname resolves to a private or metadata address")


class _AsyncValidationLoopState:
    def __init__(self) -> None:
        self.inflight: Dict[Tuple[str, int, str], asyncio.Task[None]] = {}
        self.lock = asyncio.Lock()


class AsyncOutboundURLValidator:
    """Fail-closed async wrapper with bounded DNS retry and single-flight.

    URL syntax, credentials, scheme and literal-address policy are checked on
    every call. Concurrent checks for the same EXTERNAL host and port share one
    in-flight resolution, but the completed result is never cached: every later
    validation resolves again so a public-to-private DNS change is rejected.

    This validation does not pin the validated address to httpx's subsequent
    socket connection. DNS rebinding in that final resolver-to-connect window
    still requires defense in depth such as an egress proxy/firewall, a strict
    provider-host allowlist, or a transport that connects to a validated IP
    while preserving TLS SNI and certificate verification.
    """

    def __init__(
        self,
        *,
        dns_retry_backoff_seconds: float = 0.05,
        dns_attempt_timeout_seconds: float = 3.0,
    ) -> None:
        self.dns_retry_backoff_seconds = max(
            0.0, min(float(dns_retry_backoff_seconds), 0.5)
        )
        self.dns_attempt_timeout_seconds = max(
            0.1, min(float(dns_attempt_timeout_seconds), 10.0)
        )
        self._states: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop,
            weakref.ReferenceType[_AsyncValidationLoopState],
        ] = weakref.WeakKeyDictionary()
        self._states_lock = threading.Lock()

    def _loop_state(self) -> _AsyncValidationLoopState:
        loop = asyncio.get_running_loop()
        with self._states_lock:
            state_reference = self._states.get(loop)
            state = state_reference() if state_reference is not None else None
            if state is None:
                state = _AsyncValidationLoopState()
                # The state contains loop-bound locks/tasks. Storing it strongly
                # as a WeakKeyDictionary value would keep the weak loop key alive
                # through value -> state -> loop. Active validate coroutines and
                # their shared task hold the state strongly for exactly as long
                # as single-flight coordination is required.
                self._states[loop] = weakref.ref(state)
            return state

    @staticmethod
    def _singleflight_key(
        url: str, boundary: DataBoundary,
    ) -> Tuple[str, int, str]:
        parsed = urlparse(url)
        host = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        return host, port, boundary.value

    @staticmethod
    def _is_hostname(url: str) -> bool:
        host = (urlparse(url).hostname or "").split("%", 1)[0]
        try:
            ipaddress.ip_address(host)
        except ValueError:
            return True
        return False

    @staticmethod
    def _transient_dns_error(exc: ProviderInvocationError) -> bool:
        details = exc.safe_details if isinstance(exc.safe_details, dict) else {}
        return bool(
            exc.code == "provider_dns_error"
            and exc.retryable
            and details.get("dns_failure_class") in {"temporary", "timeout"}
        )

    async def _one_full_validation(
        self,
        url: str,
        boundary: DataBoundary,
    ) -> None:
        try:
            await asyncio.wait_for(
                asyncio.to_thread(validate_outbound_url, url, boundary, True),
                timeout=self.dns_attempt_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise ProviderInvocationError(
                "provider_dns_error",
                "Provider hostname resolution timed out",
                retryable=True,
                safe_details=_dns_error_details(transient=True, timed_out=True),
            ) from exc

    async def _validate_with_retry(
        self,
        url: str,
        boundary: DataBoundary,
    ) -> None:
        for attempt in range(2):
            try:
                await self._one_full_validation(url, boundary)
                return
            except ProviderInvocationError as exc:
                if attempt > 0 or not self._transient_dns_error(exc):
                    raise
                if self.dns_retry_backoff_seconds:
                    await asyncio.sleep(self.dns_retry_backoff_seconds)

    async def _resolve_external_singleflight(
        self,
        state: _AsyncValidationLoopState,
        key: Tuple[str, int, str],
        url: str,
        boundary: DataBoundary,
    ) -> None:
        current_task = asyncio.current_task()
        try:
            await self._validate_with_retry(url, boundary)
        finally:
            async with state.lock:
                if state.inflight.get(key) is current_task:
                    state.inflight.pop(key, None)

    async def validate(
        self,
        url: str,
        boundary: DataBoundary,
        *,
        resolve_dns: bool = True,
    ) -> None:
        if boundary != DataBoundary.EXTERNAL:
            if not resolve_dns:
                validate_outbound_url(url, boundary, resolve_dns=False)
            else:
                # Preserve the existing runtime behavior for internal DNS
                # names: they are accepted only after resolving entirely to
                # loopback/private addresses. Internal checks are not shared.
                await self._validate_with_retry(url, boundary)
            return

        # Run static policy on every call. A concurrent DNS single-flight must
        # not make a later URL with plaintext HTTP or credentials acceptable.
        validate_outbound_url(url, boundary, resolve_dns=False)
        if not resolve_dns:
            return

        # Single-flight is restricted to public EXTERNAL hostnames. Local
        # boundaries and literal IPs execute the complete validator independently.
        if not self._is_hostname(url):
            await self._validate_with_retry(url, boundary)
            return

        state = self._loop_state()
        key = self._singleflight_key(url, boundary)
        async with state.lock:
            task = state.inflight.get(key)
            if task is None:
                task = asyncio.create_task(
                    self._resolve_external_singleflight(
                        state, key, url, boundary
                    )
                )
                # If every waiter is cancelled, consume a later task
                # exception while retaining normal propagation to awaiters.
                task.add_done_callback(
                    lambda completed: (
                        completed.exception() if not completed.cancelled() else None
                    )
                )
                state.inflight[key] = task
        await asyncio.shield(task)
