(function () {
  "use strict";

  var P =
    "margin: 0 0 12px 0; line-height: 1.75; font-size: 16px; color: #333;";
  var DESC =
    "margin: 0 0 12px 0; line-height: 1.75; font-size: 16px; color: #555; padding-left: 1.5em;";
  var TITLE =
    "margin: 0 0 8px 0; line-height: 1.4; font-size: 22px; font-weight: bold; text-align: center; color: #333;";
  var META =
    "margin: 0 0 16px 0; line-height: 1.5; font-size: 14px; color: #888; text-align: center;";
  var SOURCE =
    "margin: 0 0 16px 0; line-height: 1.5; font-size: 14px; color: #576b95; word-break: break-all;";
  var HEADING =
    "margin: 18px 0 8px 0; line-height: 1.4; font-size: 18px; font-weight: bold; color: #333;";
  var SEPARATOR =
    "margin: 20px 0 8px 0; line-height: 0; font-size: 0; border-top: 1px solid #e8e8e8;";
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

  function textAfterLink(container, link) {
    if (!container || !link) return "";

    var after = "";
    var pastLink = false;

    function walk(node) {
      if (node === link) {
        pastLink = true;
        return;
      }
      if (!pastLink) {
        if (node.nodeType === Node.ELEMENT_NODE) {
          Array.prototype.forEach.call(node.childNodes, walk);
        }
        return;
      }
      if (node.nodeType === Node.TEXT_NODE) {
        after += node.textContent;
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        Array.prototype.forEach.call(node.childNodes, walk);
      }
    }

    walk(container);
    return normalizeText(after);
  }

  function extractBlocks(source) {
    var blocks = [];
    var hasTitle = false;

    function shouldSkipDateBanner(text) {
      return hasTitle && /📅/.test(text) && /日报/.test(text);
    }

    function push(block) {
      if (block.type === "separator") {
        blocks.push(block);
        return;
      }
      if (block.type === "title") {
        if (!block.text) return;
        hasTitle = true;
        blocks.push(block);
        return;
      }
      if (block.type === "heading" && shouldSkipDateBanner(block.text)) {
        return;
      }
      if (block.type === "link") {
        if (!block.href || !block.text) return;
      } else if (!block.text) {
        return;
      }
      blocks.push(block);
    }

    function pushDescription(container, link) {
      var desc = textAfterLink(container, link);
      if (desc) {
        push({ type: "text", text: desc, indent: true });
      }
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
        pushDescription(p, link);
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
        pushDescription(li, link);
      } else {
        push({ type: "text", text: text, prefix: index + ". " });
      }
    }

    function walk(node) {
      if (!node || node.nodeType !== Node.ELEMENT_NODE) return;

      var tag = node.tagName;

      if (tag === "HR") {
        push({ type: "separator" });
        return;
      }

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

  function prependSourceUrl(blocks, sourceUrl) {
    if (!sourceUrl) return blocks;
    return [{ type: "source_url", url: sourceUrl }].concat(blocks);
  }

  function renderWechatHtml(blocks) {
    var html = [];

    for (var i = 0; i < blocks.length; i++) {
      var block = blocks[i];

      if (block.type === "separator") {
        html.push('<p style="' + SEPARATOR + '">&nbsp;</p>');
        continue;
      }

      if (block.type === "source_url") {
        html.push(
          '<p style="' +
            SOURCE +
            '">原文链接：' +
            escapeHtml(block.url) +
            "</p>"
        );
        continue;
      }

      if (block.type === "title") {
        html.push(
          '<p style="' + TITLE + '">' + escapeHtml(block.text) + "</p>"
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
          '<p style="' + HEADING + '">' + escapeHtml(block.text) + "</p>"
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

      var style = block.indent ? DESC : P;
      html.push(
        '<p style="' +
          style +
          '">' +
          escapeHtml(block.prefix || "") +
          escapeHtml(block.text) +
          "</p>"
      );
    }

    return "<section>" + html.join("") + "</section>";
  }

  function renderPlainText(blocks) {
    var lines = [];

    for (var i = 0; i < blocks.length; i++) {
      var block = blocks[i];

      if (block.type === "separator") {
        lines.push("");
        continue;
      }

      if (block.type === "source_url") {
        lines.push("原文链接：" + block.url);
        lines.push("");
        continue;
      }

      if (block.type === "title" || block.type === "heading") {
        lines.push(block.text);
        lines.push("");
        continue;
      }

      if (block.type === "meta") {
        lines.push(block.text);
        lines.push("");
        continue;
      }

      if (block.type === "link") {
        lines.push((block.prefix || "") + block.text);
        lines.push(block.href);
        continue;
      }

      if (block.type === "text") {
        var prefix = block.indent ? "   " : block.prefix || "";
        lines.push(prefix + block.text);
        continue;
      }
    }

    return lines.join("\n").replace(/\n{3,}/g, "\n\n").trim();
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

  function copyWithClipboardApi(htmlFragment, plainText) {
    if (!navigator.clipboard || !navigator.clipboard.write) {
      return Promise.reject(new Error("clipboard api unavailable"));
    }

    if (window.ClipboardItem) {
      return navigator.clipboard
        .write([
          new ClipboardItem({
            "text/html": new Blob([htmlFragment], { type: "text/html" }),
            "text/plain": new Blob([plainText], { type: "text/plain" }),
          }),
        ])
        .catch(function () {
          if (navigator.clipboard.writeText) {
            return navigator.clipboard.writeText(plainText);
          }
          return Promise.reject(new Error("clipboard write failed"));
        });
    }

    if (navigator.clipboard.writeText) {
      return navigator.clipboard.writeText(plainText);
    }

    return Promise.reject(new Error("clipboard api unsupported"));
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

  function copyViaHtmlClipboard(fragment, plainText) {
    var cfHtml = buildCfHtml(fragment);

    return new Promise(function (resolve, reject) {
      function onCopy(e) {
        e.preventDefault();
        e.clipboardData.setData("text/html", cfHtml);
        e.clipboardData.setData("text/plain", plainText);
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

  function copyReport(source, sourceUrl) {
    var blocks = prependSourceUrl(extractBlocks(source), sourceUrl);
    var fragment = renderWechatHtml(blocks);
    var plainText = renderPlainText(blocks);

    return copyWithClipboardApi(fragment, plainText)
      .catch(function () {
        return copyViaHtmlClipboard(fragment, plainText);
      })
      .catch(function () {
        return copyNative(fragment);
      })
      .catch(function () {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          return navigator.clipboard.writeText(plainText);
        }
        return Promise.reject(new Error("copy failed"));
      });
  }

  function setState(btn, kind) {
    btn.classList.remove("is-ok", "is-err");
    if (kind) btn.classList.add(kind);
  }

  function init() {
    var btn = document.getElementById("copy-report-btn");
    if (!btn) return;

    var targetId = btn.getAttribute("data-target") || "report-body";
    var source = document.getElementById(targetId);
    if (!source) return;

    var sourceUrl =
      btn.getAttribute("data-source-url") ||
      window.location.href.split("#")[0].split("?")[0];
    var resetTimer = null;
    var defaultLabel = btn.textContent.trim();

    btn.addEventListener("click", function () {
      btn.disabled = true;
      copyReport(source, sourceUrl)
        .then(function () {
          btn.textContent = "已复制";
          setState(btn, "is-ok");
        })
        .catch(function () {
          btn.textContent = "复制失败";
          setState(btn, "is-err");
        })
        .finally(function () {
          btn.disabled = false;
          if (resetTimer) clearTimeout(resetTimer);
          resetTimer = setTimeout(function () {
            btn.textContent = defaultLabel;
            setState(btn, null);
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
