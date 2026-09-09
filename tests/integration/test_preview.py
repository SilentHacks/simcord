import pytest
from aiohttp import ClientSession


@pytest.mark.asyncio
async def test_preview_public_flow_and_at_most_once(env, channel, alice):
    await alice.slash(channel, "panel")

    async with env.preview(channel, viewers=[alice]) as preview:
        assert preview.url.startswith("http://127.0.0.1:")
        assert "#" in preview.url
        page = preview._python
        state = preview.page_payload(page)
        assert state["selected"]["content"] == "Panel"

        body = {
            "sequence": 1,
            "request_id": "preview-click",
            "generation": page.generation,
            "bot_generation": env._generation,
            "kind": "click",
            "custom_id": "persistent:ping",
        }
        first = await preview.action("python", body)
        replay = await preview.action("python", body)
        assert first["dispatched"] is True
        assert replay == first
        assert [item["content"] for item in preview.page_payload(page)["messages"]][-1] == "pong"

    await preview.close()
    await preview.wait_closed()
    assert preview._server.port is None
    assert env._preview is None


@pytest.mark.asyncio
async def test_preview_pages_keep_viewers_and_reject_stale_generation(env, channel, alice):
    bob = env.guild.add_member(env.create_user("bob"))
    await alice.slash(channel, "panel")

    async with env.preview(channel, viewers=[alice, bob]) as preview:
        headers = {"X-Simcord-Capability": preview.capability}
        async with ClientSession() as client:
            response = await client.post(
                preview.origin + "/api/pages", headers=headers, json={"viewer_id": str(alice.id)}
            )
            assert response.status == 200
            alice_page = await response.json()
            response = await client.post(
                preview.origin + "/api/pages", headers=headers, json={"viewer_id": str(bob.id)}
            )
            assert response.status == 200
            bob_page = await response.json()

            assert alice_page["viewerId"] == str(alice.id)
            assert bob_page["viewerId"] == str(bob.id)
            assert alice_page["context"]["id"] != bob_page["context"]["id"]

            context = alice_page["context"]
            action_headers = {**headers, "X-Simcord-Context": context["id"]}
            switch = {
                "sequence": 1,
                "request_id": "switch-viewer",
                "generation": context["generation"],
                "bot_generation": env._generation,
                "kind": "viewer",
                "viewer_id": str(bob.id),
            }
            response = await client.post(preview.origin + "/api/action", headers=action_headers, json=switch)
            assert response.status == 200
            switched = await response.json()
            assert switched["dispatched"] is False

            stale = {**switch, "sequence": 2, "request_id": "stale", "kind": "refresh"}
            response = await client.post(preview.origin + "/api/action", headers=action_headers, json=stale)
            assert response.status == 400

            other_headers = {**headers, "X-Simcord-Context": bob_page["context"]["id"]}
            response = await client.get(preview.origin + "/api/state", headers=other_headers)
            assert response.status == 200
            other = await response.json()
            assert other["viewerId"] == str(bob.id)
