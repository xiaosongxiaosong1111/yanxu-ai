/* 主题引导：在样式表解析前确定浅色/深色，避免首屏闪烁。 */
(function () {
  var root = document.documentElement;
  var key = 'yanshu-theme';
  var media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

  function stored() {
    try { return localStorage.getItem(key); } catch (error) { return null; }
  }
  function forced() {
    try {
      var value = new URLSearchParams(location.search).get('theme');
      return value === 'dark' || value === 'light' ? value : null;
    } catch (error) { return null; }
  }
  function resolved() {
    var query = forced();
    if (query) return query;
    var value = stored();
    if (value === 'dark' || value === 'light') return value;
    return media && media.matches ? 'dark' : 'light';
  }
  function apply(theme) {
    root.setAttribute('data-theme', theme);
    root.style.colorScheme = theme;
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', theme === 'dark' ? '#0c1018' : '#f5f6fa');
  }
  function set(theme) {
    try { localStorage.setItem(key, theme); } catch (error) { /* 存储不可用时本次会话仍然生效 */ }
    apply(theme);
  }

  apply(resolved());
  window.yanshuTheme = {
    current: function () { return root.getAttribute('data-theme') === 'dark' ? 'dark' : 'light'; },
    set: set,
    toggle: function () { var next = resolved() === 'dark' ? 'light' : 'dark'; set(next); return next; },
    system: function () {
      try { localStorage.removeItem(key); } catch (error) { /* ignore */ }
      apply(media && media.matches ? 'dark' : 'light');
    }
  };
  if (media && media.addEventListener) {
    media.addEventListener('change', function () {
      var value = stored();
      if (value !== 'dark' && value !== 'light') apply(media.matches ? 'dark' : 'light');
    });
  }
})();
