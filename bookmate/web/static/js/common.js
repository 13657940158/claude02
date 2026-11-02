// 墨舟 BookMate —— 全站公共逻辑：登录态、导航、轻量 UI 工具
import { currentUser, setToken, setUser, toast } from "./api.js";

export * from "./api.js";

document.documentElement.classList.add("js-ready");

const nav = document.querySelector("[data-nav]");
const toggle = document.querySelector("[data-nav-toggle]");
/* 无 JS 时导航始终可见；有 JS 时移动端默认收起，由按钮控制 */
if (toggle && nav) {
  toggle.addEventListener("click", () => {
    const open = nav.classList.toggle("open");
    toggle.setAttribute("aria-expanded", String(open));
  });
}

const user = currentUser();
const authBox = document.getElementById("nav-auth");
if (authBox) {
  if (user) {
    authBox.innerHTML = `
      <span class="nav-user" title="${escapeAttr(user.email || "")}">${escapeAttr(user.nickname || "书友")}</span>
      ${user.role === "admin" ? `<a class="btn btn-ghost" href="/admin">管理后台</a>` : ""}
      <a class="btn btn-ghost" href="/account">我的账号</a>
      <button class="btn btn-ghost" type="button" data-logout>退出</button>`;
    authBox.querySelector("[data-logout]")?.addEventListener("click", () => {
      setToken("");
      setUser(null);
      toast("已退出登录");
      setTimeout(() => location.assign("/"), 400);
    });
  }
}

/** 访问受保护页面：未登录时带 next 跳转登录页 */
export function requireLogin() {
  const me = currentUser();
  if (!me) {
    const next = encodeURIComponent(location.pathname + location.search);
    location.replace(`/login?next=${next}`);
    return null;
  }
  return me;
}

export function isAdmin() {
  return currentUser()?.role === "admin";
}

export function escapeAttr(value) {
  return String(value ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/** 相对时间：刚刚 / N 分钟前 / N 小时前 / 日期 */
export function relTime(value) {
  if (!value) return "";
  const time = new Date(value).getTime();
  if (Number.isNaN(time)) return String(value);
  const diff = (Date.now() - time) / 1000;
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  const d = new Date(time);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** 把回答里的 [n] 渲染成可点击的引用角标 */
export function renderCitations(text, citations = []) {
  const map = new Map((citations || []).map((c) => [Number(c.index), c]));
  let html = String(text ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  html = html.replace(/\[(\d+(?:[,，]\s*\d+)*)\]/g, (match, inner) => {
    return inner.split(/[,，]/).filter(Boolean).map((n) => {
      const idx = Number(n.trim());
      const cite = map.get(idx);
      const title = cite
        ? `《${cite.book_title || ""}》${cite.chapter_no ? `第${cite.chapter_no}章` : ""} ${cite.chapter_title || ""}`
        : `引用 ${idx}`;
      return `<sup class="cite" title="${escapeAttr(title)}">[${idx}]</sup>`;
    }).join("");
  });
  return html.replace(/\n/g, "<br>");
}

/** 引用列表（来源卡片） */
export function citationList(citations = []) {
  if (!citations.length) return "";
  return `<ol class="cite-list">${citations.map((c) => `
    <li><span class="cite-no">[${c.index}]</span>
      <a href="/books/${c.book_id}#chapter-${c.chapter_no}">《${escapeAttr(c.book_title)}》第${c.chapter_no}章 ${escapeAttr(c.chapter_title || "")}</a>
      <span class="cite-snippet">${escapeAttr((c.snippet || "").slice(0, 90))}</span>
    </li>`).join("")}</ol>`;
}

export function emptyState(title, hint = "", actionHtml = "") {
  return `<div class="empty-state"><h2>${escapeAttr(title)}</h2>
    ${hint ? `<p class="muted">${escapeAttr(hint)}</p>` : ""}${actionHtml}</div>`;
}

export function skeletonList(count = 3, height = 96) {
  return `<div class="skel-list">${Array.from({ length: count }, () =>
    `<div class="skeleton" style="height:${height}px"></div>`).join("")}</div>`;
}

export function debounce(fn, wait = 300) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

/** 读取查询参数 */
export function queryParam(name, fallback = "") {
  return new URLSearchParams(location.search).get(name) || fallback;
}

export function bookHref(bookId) {
  return `/books/${bookId}`;
}

if (window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) {
  document.documentElement.classList.add("reduce-motion");
}
