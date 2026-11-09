"""Additional live-only setup checks; this undocumented endpoint is not a backend API.

The endpoint and its response contract were independently observed by the
controller. It reports explicit bits with implicit=false, not action authority.
"""

from __future__ import annotations

import discord

from .gateway import Gateway
from .runner import BASE_PERMISSIONS, MANAGER, Runner


def permission_value(response):
    if (
        not isinstance(response, dict)
        or not isinstance(response.get("permissions"), str)
        or not response["permissions"].isdecimal()
    ):
        raise RuntimeError(
            "Permission oracle response contract changed; preserve private receipts, never assume setup correctness"
        )
    return int(response["permissions"])


def expected_bits(runner, case):
    baseline = runner.adapter.journal.state["baseline_subject_permissions"]
    deny = discord.Permissions(**dict.fromkeys(case.deny, True)).value
    permissions = baseline | runner.role_permissions
    if permissions & 8:
        return discord.Permissions.all().value
    # Every pooled channel explicitly denies everyone VIEW_CHANNEL and grants
    # our subject BASE_PERMISSIONS, with the case's member deny applied last.
    permissions &= ~discord.Permissions(view_channel=True).value
    permissions |= BASE_PERMISSIONS & ~deny
    return permissions & ~deny


class OracleRunner(Runner):
    def __init__(self, adapter, *, gateway):
        super().__init__(adapter, gateway=gateway)
        self.checked = set()

    async def prepare(self, case):
        await super().prepare(case)
        key = (case.target, case.deny, self.role_permissions)
        if key in self.checked:
            return
        status, response = await self.call(
            "admin",
            "GET",
            f"/channels/${case.target}/permissions/member/$subject",
            query={"implicit": "false"},
        )
        if status != 200:
            raise RuntimeError(
                f"Permission setup oracle refused HTTP {status}; no scenario request was attempted"
            )
        actual, expected = permission_value(response), expected_bits(self, case)
        mask = BASE_PERMISSIONS | MANAGER | 8
        matched = actual & mask == expected & mask
        self.adapter.evidence.record(
            "permissions",
            source="live",
            case=case.id,
            implicit=False,
            mask=mask,
            expected=expected & mask,
            observed=actual & mask,
            matched=matched,
        )
        if not matched:
            raise RuntimeError(
                "Live permission setup disagrees with intended symbolic setup; inspect private permissions.jsonl before interpreting behavior"
            )
        self.checked.add(key)
        self.adapter.journal.state.setdefault("permission_oracle_cases", []).append(case.id)
        self.adapter.journal.save()


async def record(adapter, cases, token, session):
    async with Gateway(session, token, adapter.evidence) as gateway:
        runner = OracleRunner(adapter, gateway=gateway)
        await runner.setup()
        return [await runner.run(case) for case in cases]
