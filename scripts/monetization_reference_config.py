"""Concrete capture inputs, separated by the operation that consumes them."""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(slots=True)
class CaptureIdentity:
    user: int
    guild: int
    other_user: int | None = None
    other_guild: int | None = None

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in (self.user, self.guild)):
            raise ValueError("Capture requires positive integer owner/server identities.")
        if any(
            value is not None and (type(value) is not int or value <= 0)
            for value in (self.other_user, self.other_guild)
        ):
            raise ValueError("Secondary owner/server identities must be positive integers.")
        if self.other_user == self.user or self.other_guild == self.guild:
            raise ValueError("Secondary identities must differ from primary identities.")


@dataclass(slots=True)
class ProbeConfig:
    identity: CaptureIdentity
    channel: str = "parity"
    probe_timeout: float = 900

    def __post_init__(self):
        if not self.channel or not math.isfinite(self.probe_timeout) or self.probe_timeout <= 0:
            raise ValueError("Probe needs a channel and a finite positive timeout.")


@dataclass(slots=True)
class RequestPolicy:
    request_interval: float = 0.25
    max_pages: int = 100
    observe: float = 60

    def __post_init__(self):
        if (
            not math.isfinite(self.request_interval)
            or self.request_interval < 0
            or not math.isfinite(self.observe)
            or self.observe < 0
            or type(self.max_pages) is not int
            or self.max_pages < 1
        ):
            raise ValueError("Request pacing, observation and pagination limits are invalid.")


@dataclass(slots=True)
class SandboxConfig:
    sku_a: str = "Parity Subscription A"
    sku_b: str = "Parity Subscription B"
    scope: str = "auto"
    pagination_target: int = 101

    def __post_init__(self):
        if (
            not self.sku_a
            or not self.sku_b
            or self.sku_a == self.sku_b
            or self.scope not in {"auto", "user", "guild"}
        ):
            raise ValueError("Sandbox capture requires two distinct SKU names and a supported scope.")


@dataclass(slots=True)
class LifecycleConfig:
    watch_entitlement: list[str] = field(default_factory=list)
    consume_history: list[str] = field(default_factory=list)
    live_lifecycle_confirmed: bool = False
    watch_seconds: float = 3600
    poll_seconds: float = 15


@dataclass(slots=True)
class CheckoutConfig:
    deleted_purchase: bool = False
    live_upgrade: bool = False
    test_mode_confirmed: bool = False
    team_discount_confirmed: bool = False
    consumable: str = "Parity Consumable"
    durable: str = "Parity Durable"
    user_sku_a: str = "Parity Subscription A"
    user_sku_b: str = "Parity Subscription B"
    settle_seconds: float = 120


@dataclass(slots=True)
class SandboxCaptureMode:
    sandbox: SandboxConfig
    lifecycle: LifecycleConfig
    checkout: CheckoutConfig
    mode: str

    def __post_init__(self):
        if self.mode not in {"remaining", "guild"}:
            raise ValueError("Unsupported sandbox capture mode.")


@dataclass(slots=True)
class WatchCaptureMode:
    sandbox: SandboxConfig
    lifecycle: LifecycleConfig
    checkout: CheckoutConfig
    mode: str = field(default="watch", init=False)


@dataclass(slots=True)
class UpgradeCaptureMode:
    sandbox: SandboxConfig
    checkout: CheckoutConfig
    mode: str = field(default="upgrade", init=False)


@dataclass(slots=True)
class CaptureConfig:
    identity: CaptureIdentity
    probe: ProbeConfig
    policy: RequestPolicy
    operation: SandboxCaptureMode | WatchCaptureMode | UpgradeCaptureMode
    resume: bool = False
    cleanup_only: bool = False

    @classmethod
    def from_namespace(cls, args):
        def values(record):
            return {name: getattr(args, name) for name in record.__dataclass_fields__ if hasattr(args, name)}

        identity = CaptureIdentity(**values(CaptureIdentity))
        sandbox = SandboxConfig(**values(SandboxConfig))
        checkout = CheckoutConfig(**values(CheckoutConfig))
        mode = getattr(args, "mode", None) or "remaining"
        if mode == "upgrade":
            operation = UpgradeCaptureMode(sandbox, checkout)
        elif mode == "watch":
            operation = WatchCaptureMode(sandbox, LifecycleConfig(**values(LifecycleConfig)), checkout)
        else:
            operation = SandboxCaptureMode(
                sandbox, LifecycleConfig(**values(LifecycleConfig)), checkout, mode
            )
        return cls(
            identity,
            ProbeConfig(
                identity, **{key: value for key, value in values(ProbeConfig).items() if key != "identity"}
            ),
            RequestPolicy(**values(RequestPolicy)),
            operation,
            bool(getattr(args, "resume", False)),
            False,
        )


@dataclass(slots=True)
class CleanupConfig:
    identity: CaptureIdentity
    policy: RequestPolicy
    resume: bool = True
    cleanup_only: bool = True


@dataclass(slots=True)
class SubscriptionCaptureConfig:
    identity: CaptureIdentity
    probe: ProbeConfig
    policy: RequestPolicy
    sku_names: tuple[str, str]
    mode: str = field(default="subscriptions", init=False)

    def __post_init__(self):
        if len(self.sku_names) != 2 or not all(self.sku_names) or self.sku_names[0] == self.sku_names[1]:
            raise ValueError("Subscription capture requires two distinct SKU names.")


@dataclass(slots=True)
class PurchaseCaptureConfig:
    identity: CaptureIdentity
    probe: ProbeConfig
    policy: RequestPolicy
    consumable: str
    durable: str
    mode: str = field(default="purchases", init=False)
