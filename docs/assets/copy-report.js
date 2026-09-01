(function () {
  "use strict";

  var P =
    "margin: 0 0 12px 0; line-height: 1.75; font-size: 16px; color: #333;";
  var TITLE =
    "margin: 0 0 8px 0; line-height: 1.4; font-size: 22px; font-weight: bold; text-align: center; color: #333;";
  var META =
    "margin: 0 0 16px 0; line-height: 1.5; font-size: 14px; color: #888; text-align: center;";
  var HEADING =
    "margin: 18px 0 8px 0; line-height: 1.4; font-size: 18px; font-weight: bold; color: #333;";
  var LINK =
    "color: #576b95; text-decoration: underline; font-weight: bold;";

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function escapeAttr(value) {
    return escapeHtml(value).replace(/'/g, "&#39;");
  }

  function normalizeText(value) {
    return String(value || "")
      .replace(/\u00a0/g, " ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function isSectionHeading(node) {
    if (!node || node.nodeType !== Node.ELEMENT_NODE) return false;
    if (/^H[1-6]$/.test(node.tagName)) return true;
    if (node.tagName !== "P") return false;
    var strong = node.querySelector("strong");
    if (!strong) return false;
    return normalizeText(strong.textContent) === normalizeText(node.textContent);
  }

  function extractBlocks(source) {
    var blocks = [];

    function push(block) {
      if (block.type === "link") {
        if (!block.href || !block.text) return;
      } else if (!block.text) {
        return;
      }
      blocks.push(block);
    }

    function processParagraph(p, prefix) {
      var text = normalizeText(p.textContent);
      if (!text) return;

      var link = p.querySelector("a");
      if (link) {
        push({
          type: "link",
          href: link.href,
          text: normalizeText(link.textContent),
          prefix: prefix || "",
        });
        return;
      }

      if (isSectionHeading(p)) {
        push({ type: "heading", text: text });
        return;
      }

      push({ type: "text", text: text, prefix: prefix || "" });
    }

    function processListItem(li, index) {
      var paragraphs = li.querySelectorAll(":scope > p");
      if (paragraphs.length) {
        for (var i = 0; i < paragraphs.length; i++) {
          processParagraph(paragraphs[i], i === 0 ? index + ". " : "");
        }
        return;
      }

      var link = li.querySelector("a");
      var text = normalizeText(li.textContent);
      if (!text) return;

      if (link) {
        push({
          type: "link",
          href: link.href,
          text: normalizeText(link.textContent),
          prefix: index + ". ",
        });
      } else {
        push({ type: "text", text: text, prefix: index + ". " });
      }
    }

    function walk(node) {
      if (!node || node.nodeType !== Node.ELEMENT_NODE) return;

      var tag = node.tagName;

      if (tag === "HR") return;

      if (tag === "H1") {
        push({ type: "title", text: normalizeText(node.textContent) });
        return;
      }

      if (tag === "BLOCKQUOTE") {
        push({ type: "meta", text: normalizeText(node.textContent) });
        return;
      }

      if (/^H[2-6]$/.test(tag)) {
        push({ type: "heading", text: normalizeText(node.textContent) });
        return;
      }

      if (tag === "OL") {
        var items = node.querySelectorAll(":scope > li");
        for (var i = 0; i < items.length; i++) {
          processListItem(items[i], i + 1);
        }
        return;
      }

      if (tag === "UL") {
        var ulItems = node.querySelectorAll(":scope > li");
        for (var j = 0; j < ulItems.length; j++) {
          processListItem(ulItems[j], j + 1);
        }
        return;
      }

      if (tag === "P") {
        processParagraph(node, "");
        return;
      }

      Array.prototype.forEach.call(node.children, walk);
    }

    Array.prototype.forEach.call(source.children, walk);
    return blocks;
  }

  function renderWechatHtml(blocks) {
    var html = [];

    for (var i = 0; i < blocks.length; i++) {
      var block = blocks[i];

      if (block.type === "title") {
        html.push(
          '<p style="' +
            TITLE +
            '">' +
            escapeHtml(block.text) +
            "</p>"
        );
        continue;
      }

      if (block.type === "meta") {
        html.push(
          '<p style="' + META + '">' + escapeHtml(block.text) + "</p>"
        );
        continue;
      }

      if (block.type === "heading") {
        html.push(
          '<p style="' +
            HEADING +
            '">' +
            escapeHtml(block.text) +
            "</p>"
        );
        continue;
      }

      if (block.type === "link") {
        html.push(
          '<p style="' +
            P +
            '">' +
            escapeHtml(block.prefix || "") +
            '<a href="' +
            escapeAttr(block.href) +
            '" target="_blank" style="' +
            LINK +
            '">' +
            escapeHtml(block.text) +
            "</a></p>"
        );
        continue;
      }

      html.push(
        '<p style="' +
          P +
          '">' +
          escapeHtml(block.prefix || "") +
          escapeHtml(block.text) +
          "</p>"
      );
    }

    return "<section>" + html.join("") + "</section>";
  }

  function buildCfHtml(fragment) {
    var body =
      '<html><head><meta http-equiv="Content-Type" content="text/html; charset=utf-8"></head><body><!--StartFragment-->' +
      fragment +
      "<!--EndFragment--></body></html>";
    var placeholder =
      "Version:0.9\r\nStartHTML:0000000000\r\nEndHTML:0000000000\r\nStartFragment:0000000000\r\nEndFragment:0000000000\r\n";
    var draft = placeholder + body;
    var encoder = new TextEncoder();

    function byteOffset(charIndex) {
      return encoder.encode(draft.slice(0, charIndex)).length;
    }

    var startHtml = byteOffset(draft.indexOf("<html>"));
    var startFragment =
      byteOffset(draft.indexOf("<!--StartFragment-->")) +
      "<!--StartFragment-->".length;
    var endFragment = byteOffset(draft.indexOf("<!--EndFragment-->"));
    var endHtml = byteOffset(draft.length);
    var pad = function (n) {
      return ("0000000000" + n).slice(-10);
    };

    var header =
      "Version:0.9\r\n" +
      "StartHTML:" +
      pad(startHtml) +
      "\r\n" +
      "EndHTML:" +
      pad(endHtml) +
      "\r\n" +
      "StartFragment:" +
      pad(startFragment) +
      "\r\n" +
      "EndFragment:" +
      pad(endFragment) +
      "\r\n";

    draft = header + body;
    startHtml = byteOffset(draft.indexOf("<html>"));
    startFragment =
      byteOffset(draft.indexOf("<!--StartFragment-->")) +
      "<!--StartFragment-->".length;
    endFragment = byteOffset(draft.indexOf("<!--EndFragment-->"));
    endHtml = byteOffset(draft.length);

    header =
      "Version:0.9\r\n" +
      "StartHTML:" +
      pad(startHtml) +
      "\r\n" +
      "EndHTML:" +
      pad(endHtml) +
      "\r\n" +
      "StartFragment:" +
      pad(startFragment) +
      "\r\n" +
      "EndFragment:" +
      pad(endFragment) +
      "\r\n";

    return header + body;
  }

  function copyNative(fragment) {
    return new Promise(function (resolve, reject) {
      var host = document.createElement("div");
      host.setAttribute("contenteditable", "true");
      host.style.position = "fixed";
      host.style.left = "-9999px";
      host.style.top = "0";
      host.style.opacity = "0";
      host.innerHTML = fragment;
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

      if (ok) resolve();
      else reject(new Error("native copy failed"));
    });
  }

  function copyViaHtmlClipboard(fragment) {
    var cfHtml = buildCfHtml(fragment);

    return new Promise(function (resolve, reject) {
      function onCopy(e) {
        e.preventDefault();
        e.clipboardData.setData("text/html", cfHtml);
      }

      document.addEventListener("copy", onCopy, true);
      var host = document.createElement("div");
      host.setAttribute("contenteditable", "true");
      host.style.position = "fixed";
      host.style.left = "-9999px";
      host.style.top = "0";
      host.innerHTML = fragment;
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
      document.removeEventListener("copy", onCopy, true);

      if (ok) resolve();
      else reject(new Error("html clipboard failed"));
    });
  }

  function copyReport(source) {
    var fragment = renderWechatHtml(extractBlocks(source));
    return copyNative(fragment).catch(function () {
      return copyViaHtmlClipboard(fragment);
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
            "已复制（含可点链接），Ctrl+V 粘贴到公众号编辑器"
          );
        })
        .catch(function () {
          btn.textContent = "复制失败";
          setState(
            btn,
            hint,
            "is-err",
            "复制失败，请用 Chrome/Edge 打开后重试"
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
