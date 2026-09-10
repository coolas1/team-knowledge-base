import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'

/**
 * Recovery prompt shown when the SPA fails to boot.
 *
 * Registered from the shell *before* the entry bundle is requested, so it is
 * already listening when that request fails. An in-bundle handler cannot cover
 * this case: if the bundle never loads, nothing in it runs.
 *
 * Builds its node inline rather than relying on injected markup or the CSS
 * bundle, because a deploy that removed the referenced CSS is the same class
 * of failure this handles.
 */
const BOOT_GUARD = `
(function () {
  var booted = false;
  window.__tkbMarkBooted = function () { booted = true; };

  // The guard is about a boot that never happened. Once the app has committed
  // anything into #root it is running, and a later error must not be replaced
  // by a full-screen reload prompt — so the prompt also stands down if the
  // root is no longer empty, which covers the case where the bundle got far
  // enough to render but threw before signalling.
  function appHasRendered() {
    var root = document.getElementById('root');
    return !!(root && root.childElementCount > 0);
  }

  function reveal() {
    if (booted || appHasRendered() || document.getElementById('tkb-boot-recovery')) return;
    var host = document.body || document.documentElement;
    if (!host) return;

    var box = document.createElement('div');
    box.id = 'tkb-boot-recovery';
    box.setAttribute('role', 'alert');
    box.style.cssText = 'position:fixed;inset:0;z-index:9999;display:flex;' +
      'flex-direction:column;align-items:center;justify-content:center;gap:12px;' +
      'padding:32px;box-sizing:border-box;text-align:center;background:#f5f7f8;' +
      'color:#1f2933;font:14px/1.6 system-ui,-apple-system,"Segoe UI","Noto Sans CJK SC",sans-serif;';

    var title = document.createElement('div');
    title.textContent = '页面加载失败';
    title.style.cssText = 'font-size:18px;font-weight:600;';

    var hint = document.createElement('div');
    hint.textContent = '应用资源可能已被更新或暂时无法获取，请刷新页面重试。';
    hint.style.cssText = 'color:#52606d;max-width:32em;';

    var button = document.createElement('button');
    button.type = 'button';
    button.textContent = '刷新页面';
    button.style.cssText = 'margin-top:4px;padding:7px 20px;border:1px solid #1f2933;' +
      'border-radius:5px;background:transparent;color:#1f2933;font-size:14px;cursor:pointer;';
    button.onclick = function () { window.location.reload(); };

    box.appendChild(title);
    box.appendChild(hint);
    box.appendChild(button);
    host.appendChild(box);

    var root = document.getElementById('root');
    if (root) root.style.display = 'none';
  }

  // A resource that fails to load (a hashed bundle removed by a deploy)
  // raises an error event that does not bubble, so it needs the capture phase.
  window.addEventListener('error', function (event) {
    var target = event.target;
    if (target && target !== window && (target.tagName === 'SCRIPT' || target.tagName === 'LINK')) {
      reveal();
    }
  }, true);
  // Errors thrown while the bundle evaluates do bubble.
  window.addEventListener('error', reveal, false);
  window.addEventListener('unhandledrejection', reveal, false);
  // And a request that neither resolves nor errors must still not leave a
  // blank page forever.
  window.setTimeout(reveal, 20000);
})();
`

function bootGuard(): Plugin {
  return {
    name: 'tkb-boot-guard',
    transformIndexHtml: {
      order: 'pre',
      handler() {
        return [
          {
            tag: 'script',
            children: BOOT_GUARD,
            injectTo: 'head-prepend',
          },
        ]
      },
    },
  }
}

export default defineConfig({
  plugins: [bootGuard(), react()],
  server: {
    port: 5173,
    proxy: {
      // Forward /api as-is: the BFF serves API under /api in both dev and prod.
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
