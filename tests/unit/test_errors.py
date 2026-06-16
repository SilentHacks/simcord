"""Every error factory is part of the stable surface (the transports turn these
into real discord.HTTPException codes), so each must build a well-formed error.
"""

from __future__ import annotations

import inspect

from simcord.backend import errors


def test_every_error_factory_builds_a_backend_error():
    factories = [
        obj
        for obj in vars(errors).values()
        if inspect.isfunction(obj) and obj.__module__ == errors.__name__
    ]
    assert len(factories) >= 20

    for factory in factories:
        params = inspect.signature(factory).parameters.values()
        needs_arg = any(p.default is inspect.Parameter.empty for p in params)
        err = factory("detail") if needs_arg else factory()

        assert isinstance(err, errors.BackendError)
        body = err.to_json()
        assert body["code"] == err.code
        assert isinstance(body["message"], str) and body["message"]
        assert 400 <= err.status < 600
