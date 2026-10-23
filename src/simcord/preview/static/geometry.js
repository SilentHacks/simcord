/** @param {Element} element */
function rect(element) {
  const box = element.getBoundingClientRect();
  return { x: box.x, y: box.y, width: box.width, height: box.height, right: box.right, bottom: box.bottom };
}
/** @param {Element | null} element @param {Element} app @param {Element} stage */
export function ownerGeometry(element, app, stage) {
  if (!element) return null;
  const box = rect(element), appBox = rect(app), stageBox = rect(stage);
  const x = Math.max(box.x, appBox.x, stageBox.x, 0);
  const y = Math.max(box.y, appBox.y, stageBox.y, 0);
  const right = Math.min(box.right, appBox.right, stageBox.right, innerWidth);
  const bottom = Math.min(box.bottom, appBox.bottom, stageBox.bottom, innerHeight);
  return {
    rect: { x: box.x, y: box.y, width: box.width, height: box.height },
    viewport: { width: element.clientWidth, height: element.clientHeight },
    contentExtent: { width: element.scrollWidth, height: element.scrollHeight },
    visibleCrop: { x, y, width: Math.max(0, right - x), height: Math.max(0, bottom - y) },
    scrollOffset: { x: element.scrollLeft, y: element.scrollTop },
    overflow: { horizontal: element.scrollWidth > element.clientWidth, vertical: element.scrollHeight > element.clientHeight },
    visible: element.checkVisibility(),
  };
}
/** @param {{mode: string, modal: boolean, targetId: string | null}} args */
export function captureGeometry({ mode, modal, targetId }) {
  const app = document.getElementById('preview-app');
  const channel = document.getElementById('channel-layout');
  const inChannel = channel && !channel.hidden;
  const target = modal ? document.querySelector('.modal-dialog')
    : inChannel ? [...channel.querySelectorAll('.channel-message[data-message-id]')]
      .find(element => element.getAttribute('data-message-id') === targetId)
      : document.getElementById('focused-content');
  const owner = modal ? app : inChannel ? document.getElementById('channel-timeline')
    : document.getElementById('focused-content') || app;
  const scroll = modal ? document.querySelector('.modal-body') || target : owner;
  if (!app || !owner || (mode === 'surface' && !target)) return null;
  const viewport = rect(app), bounds = rect(owner), content = rect(target || app);
  const crop = mode === 'viewport' ? { ...viewport } : {
    x: Math.max(viewport.x, bounds.x, content.x, 0),
    y: Math.max(viewport.y, bounds.y, content.y, 0),
    right: Math.min(viewport.right, bounds.right, content.right, innerWidth),
    bottom: Math.min(viewport.bottom, bounds.bottom, content.bottom, innerHeight),
    width: 0, height: 0,
  };
  crop.width = crop.right - crop.x;
  crop.height = crop.bottom - crop.y;
  return { scope: 'visible', logicalViewport: viewport,
    contentExtent: { width: Math.max(content.width, scroll?.scrollWidth || 0), height: Math.max(content.height, scroll?.scrollHeight || 0) },
    visibleCrop: crop, scrollOffset: { x: scroll?.scrollLeft || 0, y: scroll?.scrollTop || 0 },
    overflow: { horizontal: (scroll?.scrollWidth || 0) > (scroll?.clientWidth || 0), vertical: (scroll?.scrollHeight || 0) > (scroll?.clientHeight || 0) },
  };
}
