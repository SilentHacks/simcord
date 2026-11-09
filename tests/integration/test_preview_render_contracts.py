"""Native renderer contracts across typed DOM and File-bearing drafts."""

import discord
import pytest


@pytest.mark.asyncio
async def test_native_render_contracts_preserve_files_and_surface_disposal(env, channel, alice):
    pytest.importorskip("playwright")
    from playwright.async_api import async_playwright

    async with env.preview(channel, viewers=[alice]) as preview:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page()
                await page.goto(preview.url)
                result = await page.evaluate("""async () => {
                  const {node, disposeSurface} = await import('/dom.js');
                  const {renderModal} = await import('/components.js');
                  const root = node('div'); document.body.append(root);
                  const {renderMedia} = await import('/media.js');
                  const file = new File(['private'], 'draft.txt', {type:'text/plain'});
                  const drafts = new Map([['upload', [file]]]);
                  const rendered = renderModal(root, {title:'Files', components:[{
                    type:1, components:[{type:19, custom_id:'files', control_key:'upload'}]
                  }]}, {
                    initDraft(key, value) { if (!drafts.has(key)) drafts.set(key, value); return drafts.get(key); },
                    draftValue: key => drafts.get(key),
                  });
                  const values = rendered.controls.files.get();
                  const input = root.querySelector('input[type=file]');
                  const retained = values[0] === file && drafts.get('upload')[0] === file;
                  const url = URL.createObjectURL(new Blob([], {type:'audio/wav'}));
                  const media = renderMedia({asset_id:'audio', content_type:'audio/wav'},
                    'test-player', {loadAsset: async () => url}, 'Audio');
                  root.append(media.element);
                  disposeSurface(root);
                  await Promise.all(media.pending);
                  URL.revokeObjectURL(url);
                  const disposed = media.element._previewMediaPlayer.active === false
                    && !media.element._previewMediaPlayer.element.hasAttribute('src');
                  const native = node('button') instanceof HTMLButtonElement
                    && input instanceof HTMLInputElement;
                  root.remove();
                  return {retained, native, disposed, name: values[0].name};
                }""")
                assert result == {
                    "retained": True,
                    "native": True,
                    "disposed": True,
                    "name": "draft.txt",
                }
            finally:
                await browser.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("everyone", [False, True])
async def test_plain_everyone_mentions_preserve_tokens_and_summary(env, channel, alice, everyone):
    message = await env.bot.get_channel(channel.id).send(
        "@everyone", allowed_mentions=discord.AllowedMentions(everyone=everyone)
    )
    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        snapshot = await preview.snapshot()
        projected = snapshot["messages"][str(message.id)]
        summary = next(item for item in snapshot["messageIndex"] if item["id"] == str(message.id))
        token = (
            {"type": "mention", "kind": "everyone", "id": "", "label": "@everyone"}
            if everyone
            else {"type": "text", "content": "@everyone"}
        )
        assert projected["content_tokens"] == [
            {"type": "paragraph", "tag": "p", "children": [{"type": "inline", "children": [token]}]}
        ]
        assert projected["mention_everyone"] is everyone
        assert projected["mentions"] == {
            "users": [],
            "roles": [],
            "channels": [],
            "everyone": everyone,
        }
        assert summary["excerpt"] == "@everyone"


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["ordinary", "code", "escaped", "embed", "snapshot", "text_display"])
async def test_channel_mention_inventory_covers_all_message_texts(env, channel, alice, surface):
    mentioned = env.guild.create_text_channel("mention-inventory")
    mention = f"<#{mentioned.id}>"
    native = env.bot.get_channel(channel.id)
    if surface == "embed":
        message = await native.send(embed=discord.Embed(description=mention))
    elif surface == "text_display":
        view = discord.ui.LayoutView()
        view.add_item(discord.ui.TextDisplay(mention))
        message = await native.send(view=view)
    else:
        content = mention
        if surface == "code":
            content = f"`{mention}`"
        elif surface == "escaped":
            content = f"\\{mention}"
        message = await native.send(content)
        if surface == "snapshot":
            message = await alice.forward(message, channel)

    async with env.preview(channel, viewers=[alice]) as preview:
        await preview.show(message)
        snapshot = await preview.snapshot()
        projected = snapshot["messages"][str(message.id)]
        assert projected["mention_channel_ids"] == [str(mentioned.id)]
        assert projected["mention_channel_names"] == {str(mentioned.id): "mention-inventory"}
        assert projected["mentions"]["channels"] == [str(mentioned.id)]
        if surface != "embed":
            summary = next(item for item in snapshot["messageIndex"] if item["id"] == str(message.id))
            assert summary["excerpt"] == (mention if surface in {"code", "escaped"} else "#mention-inventory")

        member = env.bot.get_guild(env.guild.id).get_member(alice.id)
        await env.bot.get_channel(mentioned.id).set_permissions(member, view_channel=False)
        revoked = preview._page_payload(preview._python)["messages"][str(message.id)]
        assert revoked["mention_channel_ids"] == []
        assert revoked["mention_channel_names"] == {}
        assert revoked["mentions"]["channels"] == []
