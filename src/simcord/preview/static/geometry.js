/** @param {Element} element */
function rect(element) {
  const box = element.getBoundingClientRect();
  return { x: box.x, y: box.y, width: box.width, height: box.height, right: box.right, bottom: box.bottom };
}
/** @param {{x:number,y:number,right:number,bottom:number}[]} boxes */
function intersection(...boxes) {
  const x = Math.max(0, ...boxes.map(box => box.x));
  const y = Math.max(0, ...boxes.map(box => box.y));
  const right = Math.min(innerWidth, ...boxes.map(box => box.right));
  const bottom = Math.min(innerHeight, ...boxes.map(box => box.bottom));
  return { x, y, right, bottom, width: Math.max(0, right - x), height: Math.max(0, bottom - y) };
}
/** Managed crops stay inside their intersection; replay includes every touched pixel.
 * @param {{x:number,y:number,width:number,height:number,right?:number,bottom?:number}} crop
 * @param {'inside'|'complete'} coverage */
export function rasterCrop(crop, coverage = 'inside') {
  const start = coverage === 'complete' ? Math.floor : Math.ceil;
  const end = coverage === 'complete' ? Math.ceil : Math.floor;
  const x = start(crop.x), y = start(crop.y);
  const right = end(crop.right ?? crop.x + crop.width);
  const bottom = end(crop.bottom ?? crop.y + crop.height);
  return { x, y, width: Math.max(0, right - x), height: Math.max(0, bottom - y) };
}
/** Replay requires the complete control/menu, unlike managed visible intersection.
 * @param {Element[]} elements */
export function completeCrop(elements) {
  const boxes = elements.map(rect);
  const x = Math.min(...boxes.map(box => box.x)), y = Math.min(...boxes.map(box => box.y));
  const right = Math.max(...boxes.map(box => box.right));
  const bottom = Math.max(...boxes.map(box => box.bottom));
  if (x < 0 || y < 0 || right > innerWidth || bottom > innerHeight || right <= x || bottom <= y) {
    throw new Error('complete capture is outside the browser viewport or has no natural geometry');
  }
  return rasterCrop({ x, y, right, bottom, width: right - x, height: bottom - y }, 'complete');
}
/** @param {Element | null} element @param {Element} app @param {Element} stage */
export function ownerGeometry(element, app, stage) {
  if (!element) return null;
  const box = rect(element), appBox = rect(app), stageBox = rect(stage);
  const { x, y, width, height } = intersection(box, appBox, stageBox);
  return {
    rect: { x: box.x, y: box.y, width: box.width, height: box.height },
    viewport: { width: element.clientWidth, height: element.clientHeight },
    contentExtent: { width: element.scrollWidth, height: element.scrollHeight },
    visibleCrop: { x, y, width, height },
    scrollOffset: { x: element.scrollLeft, y: element.scrollTop },
    overflow: { horizontal: element.scrollWidth > element.clientWidth, vertical: element.scrollHeight > element.clientHeight },
    visible: element.checkVisibility(),
  };
}
/** Measure explicit surfaces without selecting DOM or disturbing focus/scroll.
 * @param {{mode:string,target:Element|null,owner:Element|null,scroll:Element|null,app:Element|null,stage?:Element|null}} args */
export function measureCapture({ mode, target, owner, scroll, app, stage = null }) {
  if (!app || !owner || (mode === 'surface' && !target)) return null;
  const viewport = rect(app), bounds = rect(owner), content = rect(target || app);
  const crop = mode === 'viewport' ? { ...viewport }
    : intersection(viewport, bounds, content, ...(stage ? [rect(stage)] : []));
  return { scope: 'visible', logicalViewport: viewport,
    contentExtent: { width: Math.max(content.width, scroll?.scrollWidth || 0), height: Math.max(content.height, scroll?.scrollHeight || 0) },
    visibleCrop: crop, scrollOffset: { x: scroll?.scrollLeft || 0, y: scroll?.scrollTop || 0 },
    overflow: { horizontal: (scroll?.scrollWidth || 0) > (scroll?.clientWidth || 0), vertical: (scroll?.scrollHeight || 0) > (scroll?.clientHeight || 0) },
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
  return measureCapture({ mode, target: target || null, owner, scroll: scroll || null, app });
}
