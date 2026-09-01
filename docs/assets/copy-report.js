(function () {
  "use strict";

  var WECHAT_STYLES = {
    a: "color: #576b95; text-decoration: underline;",
    strong: "font-weight: bold;",
    b: "font-weight: bold;",
    em: "font-style: italic;",
    i: "font-style: italic;",
    p: "margin: 0 0 1em 0; line-height: 1.75; font-size: 16px;",
    h1: "font-size: 22px; font-weight: bold; margin: 1.2em 0 0.6em 0; line-height: 1.4;",
    h2: "font-size: 20px; font-weight: bold; margin: 1.1em 0 0.5em 0; line-height: 1.4;",
    h3: "font-size: 18px; font-weight: bold; margin: 1em 0 0.5em 0; line-height: 1.4;",
    h4: "font-size: 17px; font-weight: bold; margin: 0.9em 0 0.4em 0; line-height: 1.4;",
    h5: "font-size: 16px; font-weight: bold; margin: 0.8em 0 0.4em 0; line-height: 1.4;",
    h6: "font-size: 16px; font-weight: bold; margin: 0.8em 0 0.4em 0; line-height: 1.4;",
    ul: "margin: 0 0 1em 0; padding-left: 1.5em;",
    ol: "margin: 0 0 1em 0; padding-left: 1.5em;",
    li: "margin: 0.35em 0; line-height: 1.75; font-size: 16px;",
    blockquote:
      "margin: 0 0 1em 0; padding: 0.5em 1em; border-left: 3px solid #d0d7de; color: #656d76;",
    hr: "border: none; border-top: 1px solid #d0d7de; margin: 1.5em 0;",
    code: "font-family: Consolas, monospace; background: #f6f8fa; padding: 0.1em 0.3em; border-radius: 3px;",
    pre: "margin: 0 0 1em 0; padding: 12px; background: #f6f8fa; border-radius: 6px; overflow-x: auto;",
  };

  var ALLOWED_TAGS = {
    A: true,
    P: true,
    BR: true,
    STRONG: true,
    B: true,
    EM: true,
    I: true,
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
  };

  function mergeStyle(existing, extra) {
    if (!existing) return extra;
    if (!extra) return existing;
    return existing.replace(/;\s*$/, "") + "; " + extra;
  }

  function applyInlineStyle(el, tag) {
    var style = WECHAT_STYLES[tag];
    if (style) {
      el.setAttribute("style", mergeStyle(el.getAttribute("style"), style));
    }
  }

  function isBoldWrapper(node) {
    if (!node || node.nodeType !== Node.ELEMENT_NODE) return false;
    var tag = node.tagName;
    return tag === "STRONG" || tag === "B";
  }

  function unwrapBoldLink(node) {
    if (node.tagName !== "A") return node;
    var child = node.firstElementChild;
    if (
      child &&
      node.childElementCount === 1 &&
      isBoldWrapper(child) &&
      child.textContent === node.textContent
    ) {
      var href = node.getAttribute("href");
      var a = document.createElement("a");
      if (href) {
        a.setAttribute("href", href);
        a.setAttribute("target", "_blank");
      }
      a.setAttribute(
        "style",
        mergeStyle(WECHAT_STYLES.a, "font-weight: bold;")
      );
      a.textContent = child.textContent;
      return a;
    }
    return node;
  }

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

    el = unwrapBoldLink(el);
    applyInlineStyle(el, el.tagName.toLowerCase());
    return el;
  }

  function buildFragmentHtml(source) {
    var wrapper = document.createElement("div");
    Array.prototype.forEach.call(source.childNodes, function (child) {
      var cleaned = cleanNode(child);
      if (cleaned) wrapper.appendChild(cleaned);
    });
    return wrapper.innerHTML;
  }

  function wrapClipboardHtml(fragment) {
    return (
      '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>' +
      "<!--StartFragment-->" +
      fragment +
      "<!--EndFragment-->" +
      "</body></html>"
    );
  }

  function buildPlainText(source) {
    var lines = [];
    function walk(node) {
      if (node.nodeType === Node.TEXT_NODE) {
        var text = node.nodeValue;
        if (text) lines.push(text);
        return;
      }
      if (node.nodeType !== Node.ELEMENT_NODE) return;
      var tag = node.tagName;
      if (tag === "A") {
        var label = (node.textContent || "").trim();
        var href = node.getAttribute("href") || "";
        if (label && href) lines.push(label + " (" + href + ")");
        else if (label) lines.push(label);
        else if (href) lines.push(href);
        return;
      }
      if (tag === "BR") {
        lines.push("\n");
        return;
      }
      if (tag === "P" || tag === "LI" || /^H[1-6]$/.test(tag)) {
        if (lines.length && lines[lines.length - 1] !== "\n") lines.push("\n");
      }
      Array.prototype.forEach.call(node.childNodes, walk);
      if (tag === "P" || tag === "LI" || /^H[1-6]$/.test(tag)) {
        lines.push("\n");
      }
    }
    walk(source);
    return lines.join("").replace(/\n{3,}/g, "\n\n").trim();
  }

  function copyViaCopyEvent(html, plainText) {
    return new Promise(function (resolve, reject) {
      function onCopy(e) {
        e.preventDefault();
        e.clipboardData.setData("text/html", html);
        e.clipboardData.setData("text/plain", plainText);
      }

      document.addEventListener("copy", onCopy);
      var host = document.createElement("div");
      host.setAttribute("contenteditable", "true");
      host.style.position = "fixed";
      host.style.left = "-9999px";
      host.style.top = "0";
      host.innerHTML = html;
      document.body.appendChild(host);

      var selection = window.getSelection();
      var range = document.createRange();
      range.selectNodeContents(host);
      selection.removeAllRanges();
      selection.addRange(range);

      var ok = false;
      try {
        ok = document.execCommand("copy");
      } catch (err) {
        ok = false;
      }

      selection.removeAllRanges();
      document.body.removeChild(host);
      document.removeEventListener("copy", onCopy);

      if (ok) resolve();
      else reject(new Error("copy failed"));
    });
  }

  function copyViaClipboardApi(html, plainText) {
    if (!navigator.clipboard || !window.ClipboardItem) {
      return Promise.reject(new Error("clipboard api unavailable"));
    }
    var item = new ClipboardItem({
      "text/html": new Blob([html], { type: "text/html" }),
      "text/plain": new Blob([plainText], { type: "text/plain" }),
    });
    return navigator.clipboard.write([item]);
  }

  function copyReport(source) {
    var fragment = buildFragmentHtml(source);
    var html = wrapClipboardHtml(fragment);
    var plainText = buildPlainText(source);

    return copyViaCopyEvent(html, plainText).catch(function () {
      return copyViaClipboardApi(html, plainText);
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
    var defaultLabel = btn.textContent.trim();
    var defaultHint = hint ? hint.textContent : "";

    btn.addEventListener("click", function () {
      btn.disabled = true;
      copyReport(source)
        .then(function () {
          btn.textContent = "已复制";
          setState(
            btn,
            hint,
            "is-ok",
            "已复制（含链接），请用 Ctrl+V 粘贴到公众号编辑器"
          );
        })
        .catch(function () {
          btn.textContent = "复制失败";
          setState(
            btn,
            hint,
            "is-err",
            "复制失败，请用 Chrome/Edge 打开后重试，或手动全选复制"
          );
        })
        .finally(function () {
          btn.disabled = false;
          if (resetTimer) clearTimeout(resetTimer);
          resetTimer = setTimeout(function () {
            btn.textContent = defaultLabel;
            setState(btn, hint, null, defaultHint);
          }, 3200);
        });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
