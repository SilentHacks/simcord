"""Permission validators used by route handlers (leaf-safe: no backend callers)."""

from __future__ import annotations

from .. import errors, permissions
from .base import BackendBase


class PermissionsMixin(BackendBase):
    def require_hierarchy(self, guild_id: int, actor_id: int, target_id: int) -> None:
        """Role hierarchy: you cannot moderate members at or above your top role."""
        guild = self.get_guild(guild_id)
        if actor_id == guild.owner_id or target_id not in guild.members:
            return
        if target_id == guild.owner_id or guild.top_role_position(target_id) >= guild.top_role_position(
            actor_id
        ):
            raise errors.missing_permissions()

    def require_role_assignable(self, guild_id: int, actor_id: int, role_id: int) -> None:
        """A role can only be assigned/edited if it sits below the actor's top role."""
        guild = self.get_guild(guild_id)
        if actor_id == guild.owner_id:
            return
        role = self.get_role(guild_id, role_id)
        if role.position >= guild.top_role_position(actor_id):
            raise errors.missing_permissions()

    def require_position_assignable(self, guild_id: int, actor_id: int, position: int) -> None:
        """A role can only be moved to a slot below the actor's own top role."""
        guild = self.get_guild(guild_id)
        if actor_id == guild.owner_id:
            return
        if position >= guild.top_role_position(actor_id):
            raise errors.missing_permissions()

    def require_can_grant(self, guild_id: int, actor_id: int, role_permissions: int) -> None:
        """You cannot grant a role permissions you do not hold yourself (unless admin)."""
        guild = self.get_guild(guild_id)
        if actor_id == guild.owner_id:
            return
        actor_perms = self.compute_permissions(guild_id, actor_id)
        if actor_perms & permissions.flag("administrator"):
            return
        if role_permissions & ~actor_perms:
            raise errors.missing_permissions()
