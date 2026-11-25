/*
 NashiGold chat widget. Add to any page (Shopify, WordPress, custom):
   <script src="https://YOUR-SERVER/widget.js" data-api="https://YOUR-SERVER"></script>
*/
(function () {
  var script = document.currentScript;
  var API = (script && script.dataset.api) || "";
  var GOLD = "#B8860B";
  var history = [];

  var css = `
  #ng-btn{position:fixed;bottom:20px;right:20px;width:60px;height:60px;border-radius:50%;background:${GOLD};color:#fff;border:none;font-size:26px;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,.25);z-index:99999}
  #ng-box{position:fixed;bottom:92px;right:20px;width:360px;max-width:calc(100vw - 40px);height:520px;max-height:calc(100vh - 120px);background:#fff;border-radius:16px;box-shadow:0 8px 30px rgba(0,0,0,.2);display:none;flex-direction:column;overflow:hidden;z-index:99999;font-family:system-ui,-apple-system,"Segoe UI",Tahoma,sans-serif}
  #ng-head{background:${GOLD};color:#fff;padding:14px 16px;font-weight:600}
  #ng-head small{display:block;font-weight:400;opacity:.85}
  #ng-msgs{flex:1;overflow-y:auto;padding:12px;background:#faf8f3}
  .ng-m{margin:6px 0;padding:9px 12px;border-radius:12px;max-width:85%;line-height:1.45;font-size:14px;white-space:pre-wrap;word-wrap:break-word}
  .ng-u{background:${GOLD};color:#fff;margin-inline-start:auto}
  .ng-a{background:#fff;border:1px solid #eee;color:#222}
  .ng-a a{color:${GOLD};font-weight:600}
  #ng-form{display:flex;border-top:1px solid #eee}
  #ng-in{flex:1;border:none;padding:12px;font-size:14px;outline:none}
  #ng-send{background:none;border:none;color:${GOLD};font-weight:700;padding:0 16px;cursor:pointer}`;
  var style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  var btn = document.createElement("button");
  btn.id = "ng-btn"; btn.setAttribute("aria-label", "Chat"); btn.textContent = "💬";
  var box = document.createElement("div");
  box.id = "ng-box";
  box.innerHTML =
    '<div id="ng-head">NashiGold Assistant<small>مساعد ناشي جولد · Ask in English or العربية</small></div>' +
    '<div id="ng-msgs"></div>' +
    '<form id="ng-form"><input id="ng-in" dir="auto" autocomplete="off" placeholder="Type a message… اكتب رسالتك"/><button id="ng-send">Send</button></form>';
  document.body.appendChild(btn);
  document.body.appendChild(box);

  var msgs = box.querySelector("#ng-msgs");
  var form = box.querySelector("#ng-form");
  var input = box.querySelector("#ng-in");

  function escapeHtml(s) {
    return s.replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function render(text) {
    // Safe minimal markdown: links [text](https://...) and **bold**
    return escapeHtml(text)
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')
      .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  }
  function add(role, text) {
    var d = document.createElement("div");
    d.className = "ng-m " + (role === "user" ? "ng-u" : "ng-a");
    d.dir = "auto";
    d.innerHTML = role === "user" ? escapeHtml(text) : render(text);
    msgs.appendChild(d);
    msgs.scrollTop = msgs.scrollHeight;
    return d;
  }

  btn.onclick = function () {
    var open = box.style.display === "flex";
    box.style.display = open ? "none" : "flex";
    if (!open && !msgs.children.length)
      add("assistant", "Welcome to NashiGold ✨ How can I help you today?\nأهلاً بك في ناشي جولد، كيف أقدر أساعدك؟");
    if (!open) input.focus();
  };

  form.onsubmit = async function (e) {
    e.preventDefault();
    var text = input.value.trim();
    if (!text) return;
    input.value = "";
    add("user", text);
    history.push({ role: "user", content: text });
    var typing = add("assistant", "…");
    try {
      var r = await fetch(API + "/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ messages: history.slice(-10) }),
      });
      if (!r.ok) throw new Error(r.status);
      var data = await r.json();
      typing.innerHTML = render(data.reply);
      history.push({ role: "assistant", content: data.reply });
    } catch (err) {
      typing.textContent = "Sorry, something went wrong. Please try again. عذراً، حدث خطأ.";
      history.pop();
    }
    msgs.scrollTop = msgs.scrollHeight;
  };
})();
