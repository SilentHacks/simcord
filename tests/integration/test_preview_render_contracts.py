"""Native renderer contracts across typed DOM and File-bearing drafts."""

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
