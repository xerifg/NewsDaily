(function () {
  "use strict";

  var ALLOWED_TAGS = {
    A: true,
    P: true,
    BR: true,
    STRONG: true,
    B: true,
    EM: true,
    I: true,
    U: true,
    H1: true,
    H2: true,
    H3: true,
    H4: true,
    H5: true,
    H6: true,
    UL: true,
    OL: true,
    LI: true,
    BLOCKQUOTE: true,
    HR: true,
    CODE: true,
    PRE: true,
    SPAN: true,
    DIV: true,
    SECTION: true,
    ARTICLE: true,
    TABLE: true,
    THEAD: true,
    TBODY: true,
    TR: true,
    TH: true,
    TD: true,
  };

  function cleanNode(node) {
    if (node.nodeType === Node.TEXT_NODE) {
      return document.createTextNode(node.nodeValue);
    }
    if (node.nodeType !== Node.ELEMENT_NODE) {
      return null;
    }

    var tag = node.tagName;
    if (!ALLOWED_TAGS[tag]) {
      var frag = document.createDocumentFragment();
      Array.prototype.forEach.call(node.childNodes, function (child) {
        var cleaned = cleanNode(child);
        if (cleaned) frag.appendChild(cleaned);
      });
      return frag;
    }

    var el = document.createElement(tag.toLowerCase());
    if (tag === "A") {
      var href = node.getAttribute("href");
      if (href) {
        el.setAttribute("href", href);
        el.setAttribute("target", "_blank");
      }
    }

    Array.prototype.forEach.call(node.childNodes, function (child) {
      var cleaned = cleanNode(child);
      if (cleaned) el.appendChild(cleaned);
    });
    return el;
  }

  function buildClipboardHtml(source) {
    var wrapper = document.createElement("div");
    Array.prototype.forEach.call(source.childNodes, function (child) {
      var cleaned = cleanNode(child);
      if (cleaned) wrapper.appendChild(cleaned);
    });
    return wrapper.innerHTML;
  }

  function buildClipboardText(source) {
    return (source.innerText || source.textContent || "").trim();
  }

  function copyViaExecCommand(html, text) {
    var host = document.createElement("div");
    host.setAttribute("contenteditable", "true");
    host.style.position = "fixed";
    host.style.left = "-9999px";
    host.style.top = "0";
    host.innerHTML = html || text;
    document.body.appendChild(host);

    var selection = window.getSelection();
    var range = document.createRange();
    range.selectNodeContents(host);
    selection.removeAllRanges();
    selection.addRange(range);

    var ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (e) {
      ok = false;
    }

    selection.removeAllRanges();
    document.body.removeChild(host);
    return ok;
  }

  function copyReport(source) {
    var html = buildClipboardHtml(source);
    var text = buildClipboardText(source);

    if (navigator.clipboard && window.ClipboardItem) {
      var item = new ClipboardItem({
        "text/html": new Blob([html], { type: "text/html" }),
        "text/plain": new Blob([text], { type: "text/plain" }),
      });
      return navigator.clipboard.write([item]).catch(function () {
        if (!copyViaExecCommand(html, text)) {
          throw new Error("copy failed");
        }
      });
    }

    return new Promise(function (resolve, reject) {
      if (copyViaExecCommand(html, text)) resolve();
      else reject(new Error("copy failed"));
    });
  }

  function setState(btn, hint, kind, message) {
    btn.classList.remove("is-ok", "is-err");
    if (kind) btn.classList.add(kind);
    if (hint && message) hint.textContent = message;
  }

  function init() {
    var btn = document.getElementById("copy-report-btn");
    if (!btn) return;

    var targetId = btn.getAttribute("data-target") || "report-body";
    var source = document.getElementById(targetId);
    var hint = document.getElementById("copy-report-hint");
    if (!source) return;

    var resetTimer = null;
    var defaultLabel = btn.textContent;
    var defaultHint = hint ? hint.textContent : "";

    btn.addEventListener("click", function () {
      btn.disabled = true;
      copyReport(source)
        .then(function () {
          btn.textContent = "已复制";
          setState(btn, hint, "is-ok", "已复制，可粘贴到微信公众号编辑器");
        })
        .catch(function () {
          btn.textContent = "复制失败";
          setState(btn, hint, "is-err", "复制失败，请手动全选正文后复制");
        })
        .finally(function () {
          btn.disabled = false;
          if (resetTimer) clearTimeout(resetTimer);
          resetTimer = setTimeout(function () {
            btn.textContent = defaultLabel;
            setState(btn, hint, null, defaultHint);
          }, 2600);
        });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
