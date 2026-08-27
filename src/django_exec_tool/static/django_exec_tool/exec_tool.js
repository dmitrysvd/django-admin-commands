/* Живой вывод запуска.
 *
 * Обычный поллинг, а не WebSocket: инструмент должен подключаться в любой
 * проект одной строкой в INSTALLED_APPS, а channels/ASGI — это требование к
 * инфраструктуре всего проекта. Инкрементальность даёт номер последнего чанка.
 */
(function () {
  var configEl = document.getElementById("exec-tool-config");
  if (!configEl) return;
  var config = JSON.parse(configEl.textContent);
  var output = document.getElementById("run-output");
  var statusEl = document.getElementById("run-status");
  var exitEl = document.getElementById("run-exit");
  var after = 0;
  var delay = 1000;

  function atBottom() {
    return output.scrollTop + output.clientHeight >= output.scrollHeight - 20;
  }

  function poll() {
    fetch(config.outputUrl + "?after=" + after, { credentials: "same-origin" })
      .then(function (response) { return response.json(); })
      .then(function (data) {
        var stick = atBottom();
        data.chunks.forEach(function (chunk) {
          output.appendChild(document.createTextNode(chunk.text));
        });
        if (data.chunks.length) {
          after = data.last_seq;
          if (stick) output.scrollTop = output.scrollHeight;
        }
        if (statusEl) statusEl.textContent = data.status_display;
        if (exitEl && data.exit_code !== null) exitEl.textContent = data.exit_code;
        if (data.active) {
          setTimeout(poll, delay);
        } else {
          // Ещё один заход: последние строки могли не успеть долиться.
          setTimeout(function () { if (!window.execToolDone) { window.execToolDone = true; poll(); } }, delay);
        }
      })
      .catch(function () { setTimeout(poll, delay * 5); });
  }

  poll();
})();
